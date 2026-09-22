"""
Builds the `state` and the questions sent to JEV for ONE screening record.

HOW THE QUESTION IS FRAMED IS THE HEART OF THE DESIGN. At the title/abstract stage a
reviewer is NOT deciding "does this study meet the criteria" — only the full text answers
that. They are deciding "is it worth retrieving the full text?". Asking the first one
penalises JEV for failing to guess what it cannot see, and measures the wrong thing.

SIX NOULS IN ONE REQUEST. The `state` is billed once, so the facets come almost free and
deliver three things:

  1. two competing predictors — the holistic `retrieve`, and the facet aggregate (min and
     product). Which one wins is a real finding about HOW JEV decides;
  2. the error analysis. JEV generates no text: there is no rationale to read. "Does JEV
     fail more on Population or on Outcome?" only has an answer if the facet is asked;
  3. cheap falsification — if the five facets score AUC ~0.5 while the holistic does not,
     JEV is matching TOPIC, not criteria.

Questions in English: the JEV docs state English gives optimal accuracy, and the corpora
are all English.
"""

from __future__ import annotations

from typing import Any

from . import jev

# Token approximation: ~4 chars per token in English. Enough to truncate BEFORE spending a
# call that would come back 422 for blowing past the 32k `state` limit.
CHARS_PER_TOKEN = 4
STATE_CHAR_CAP = jev.STATE_TOKEN_CAP * CHARS_PER_TOKEN - 4_000  # headroom for questions


def _clip(t: str, n: int) -> str:
    t = (t or "").strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " […]"


# ------------------------------------------------------------------------- questions

RETRIEVE = jev.noul(
    "Given the review's eligibility criteria, should this record be retrieved for "
    "full-text assessment? Screening is recall-oriented: retrieve the record when it "
    "plausibly meets the criteria, or when the title and abstract do not contain enough "
    "information to rule it out.",
    true="retrieve this record for full-text assessment",
    false="safely exclude this record on title and abstract alone")

FACETS = {
    "population": jev.noul(
        "Does the population studied in this record match the review's target "
        "population?",
        true="the population matches, or the abstract does not rule it out",
        false="the abstract makes clear the population does not match"),
    "intervention": jev.noul(
        "Does this record study the intervention or exposure the review asks about?",
        true="the intervention/exposure matches, or is not ruled out",
        false="the abstract makes clear the intervention/exposure does not match"),
    "comparator": jev.noul(
        "Does this record include the comparator the review requires?",
        true="the comparator is present, or the review requires none",
        false="the abstract makes clear the required comparator is absent"),
    "outcome": jev.noul(
        "Does this record report at least one of the outcomes the review asks about?",
        true="at least one required outcome is reported, or is not ruled out",
        false="the abstract makes clear none of the required outcomes is reported"),
    "design": jev.noul(
        "Is this record an eligible study design for the review?",
        true="the study design is eligible, or cannot be determined from the abstract",
        false="the abstract makes clear the design is ineligible (e.g. it is a review, "
              "editorial, case report, or protocol when those are excluded)"),
}

QUESTIONS: dict[str, dict] = {"retrieve": RETRIEVE, **FACETS}
IDS = list(QUESTIONS)

# The facets are deliberately asymmetric: when in doubt, TRUE. That is the rule of real
# screening — excluding requires evidence, including does not. A symmetric facet would
# turn "the abstract does not say" into an exclusion, which is the error that loses a
# study.

# NATIVE CHOICE. `noul` returns a probability, and turning it into yes/no requires a
# threshold that WE pick — our arbitrariness sitting inside the model's result. `choice`
# returns the selected option: the decision belongs to JEV. That is what makes the
# comparison against DeepSeek, which only answers binary, a comparison between models
# rather than between a model and a threshold.
#
# Same asymmetric framing as the facets and the DeepSeek prompt — all three have to be
# playing the same game.
CHOICE = jev.choice(
    "Screening title and abstract for a systematic review. Should this record be "
    "retrieved for full-text assessment against the eligibility criteria? Screening is "
    "recall-oriented: excluding requires evidence, including does not. Many records have "
    "no abstract; absence of information is not grounds for exclusion.",
    {"retrieve": "retrieve the full text of this record for assessment",
     "exclude": "discard this record on title and abstract alone"})


def holistic_only() -> dict[str, dict]:
    """Cheap variant: only the question that decides. ~30% fewer tokens per record."""
    return {"retrieve": RETRIEVE}


# ----------------------------------------------------------------------------- state

def build_state(review: dict[str, Any], record: dict[str, Any], *,
                sections: list[dict] | None = None) -> dict:
    """
    `state` as a JSON object — the JEV docs recommend structure over a loose string when
    the decision compares distinct parts.

    `sections` (optional) is full-text content, for the ablation. When present it enters
    truncated, prioritising Methods and Results, where eligibility is actually decided.
    """
    crit = review.get("criteria") or {}
    question = _clip(review.get("question") or review.get("title") or "", 1_200)
    state: dict[str, Any] = {
        "eligibility_criteria": (
            {k: _clip(v, 1_500) for k, v in crit.items() if v}
            or _clip(review.get("raw_criteria") or "", 6_000)),
        "record": {
            "title": _clip(record.get("title") or "", 1_000),
            "abstract": _clip(record.get("abstract") or "", 6_000),
            "year": record.get("year") or "",
        },
    }
    if question:
        # Key omitted when there is no published title, rather than filled with an empty
        # string: an empty key suggests to the model that the information exists and is
        # null.
        state = {"review_question": question, **state}
    if not state["record"]["abstract"]:
        # Declared in the state itself: JEV needs to know the abstract does not exist,
        # and not read absence of information as absence of eligibility.
        state["record"]["abstract"] = "(no abstract available for this record)"

    if sections:
        priority = ("method", "material", "result", "design", "participant",
                    "population", "intervention", "outcome")
        def weight(s: dict) -> int:
            h = (s.get("heading") or "").lower()
            return 0 if any(p in h for p in priority) else 1
        ordered = sorted(sections, key=weight)
        budget = STATE_CHAR_CAP - len(str(state))
        body, used = [], 0
        for s in ordered:
            t = (s.get("text") or "").strip()
            if not t or used + len(t) > budget:
                continue
            body.append({"heading": s.get("heading") or "", "text": t})
            used += len(t)
        if body:
            state["record"]["full_text_sections"] = body

    return state


def estimated_size(state: dict) -> int:
    """Approximate tokens in the state. Used to predict cost before spending."""
    return len(str(state)) // CHARS_PER_TOKEN
