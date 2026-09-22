"""
Client for JEV (TypeSafe) — a *System One model*: it evaluates TYPED questions against a
state and returns structured results. It does not generate text.

    POST https://api.typesafe.ai/v1/systemone
    Authorization: Bearer <key>
    {"state": ..., "model": "jev-latest", "questions": {"id": {"type": "noul", ...}}}

TWO TRAPS, both paid for with real mistakes elsewhere:

  1. the answer lives at `r["answers"][qid]`, NOT at `r[qid]`;
  2. `noul` returns a PROBABILITY (0..1), not a boolean. Treating it as a bool turns
     everything into "yes" and the report comes out with 0.00 confidence on every item
     without anyone noticing.

`obter()` exists so no script ever unpacks that by hand again, and tests/test_jev.py
locks both against regression.

MEASURED LIMITS (docs.typesafe.ai/models):
  64k tokens per request; 32k for `state` plus the longest question
  US$ 0.042 per million INPUT tokens; output free
  1,200 req/min and 250,000 tokens/s

THE KEY never appears in a log, an exception or an output file. It is read from
`api_key_jev.txt` (already in .gitignore) or from TYPESAFE_API_KEY.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
MODEL = os.environ.get("JEV_MODEL", "jev-latest")

# 1,200 req/min = 20/s. 8 in parallel stays comfortably below that and already makes wall
# time tolerable at our sizes (15k records in SYNERGY+).
PARALLEL = int(os.environ.get("JEV_PARALLEL", "8"))

# Hard API limit for `state` plus the longest question. Used by screening.py to truncate
# full text before spending a call that would come back 422.
STATE_TOKEN_CAP = 32_000

INPUT_PRICE_PER_MILLION = 0.042


class JevError(RuntimeError):
    pass


class FatalError(JevError):
    """An error that is pointless to retry: credit exhausted (402), invalid key (401),
    malformed request (422). It propagates past the batch loop instead of becoming one
    more None — swallowing it makes the runner announce "no calls" after thousands of
    attempts, which is exactly what happened when the credit ran out."""


class Ledger:
    """Accumulates tokens, calls, cost and latency. Deliberately not thread-local:
    `in_batch` runs across threads and the total has to cover the whole batch."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.calls = self.failures = 0
        self.input = self.output = 0
        self.seconds = 0.0
        self.latencies: list[float] = []

    def record(self, usage: dict, secs: float) -> None:
        self.calls += 1
        self.input += int(usage.get("input_tokens") or 0)
        self.output += int(usage.get("output_tokens") or 0)
        self.seconds += secs
        self.latencies.append(secs)

    @property
    def cost(self) -> float:
        return self.input / 1e6 * INPUT_PRICE_PER_MILLION

    def p50(self) -> float:
        return sorted(self.latencies)[len(self.latencies) // 2] if self.latencies else 0.0

    def lines(self, prefix: str = "  ") -> list[str]:
        if not self.calls:
            return [f"{prefix}no calls"]
        return [
            f"{prefix}calls          {self.calls:,}"
            + (f" ({self.failures} failed)" if self.failures else ""),
            f"{prefix}input tokens   {self.input:,}",
            f"{prefix}cost           US$ {self.cost:.4f}",
            f"{prefix}latency p50    {self.p50():.2f}s per call",
            f"{prefix}summed latency {self.seconds/60:.1f} min "
            f"(wall time is lower: {PARALLEL} in parallel)",
        ]


LEDGER = Ledger()


def _key() -> str:
    try:
        return (ROOT / "api_key_jev.txt").read_text().strip()
    except Exception:
        k = os.environ.get("TYPESAFE_API_KEY")
        if not k:
            raise JevError("no key: neither api_key_jev.txt nor TYPESAFE_API_KEY") from None
        return k


_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            headers={"Authorization": f"Bearer {_key()}",
                     "Content-Type": "application/json"},
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_connections=PARALLEL * 2))
    return _client


# ------------------------------------------------------------------ building questions

def noul(instructions: str, *, true: str | None = None,
         false: str | None = None) -> dict:
    """Truth-value question. The answer comes back as a PROBABILITY in 0..1."""
    q: dict = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {"true": true or "yes", "false": false or "no"}
    return q


def choice(instructions: str, options: dict[str, str]) -> dict:
    """Pick one of the named options. 255 maximum."""
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list[str]) -> dict:
    """Rate against a rubric. The API requires 2 to 10 levels."""
    if not 2 <= len(levels) <= 10:
        raise ValueError(f"score requires 2 to 10 levels, got {len(levels)}")
    return {"type": "score", "instructions": instructions, "criteria": levels}


# --------------------------------------------------------------------------- asking

def ask(state: Any, questions: dict[str, dict], *,
        model: str = MODEL, attempts: int = 4) -> dict:
    """
    One request: several questions against the SAME state, evaluated in parallel by the
    service — and the state is billed ONCE. Returns raw JSON; use `get()`.

    429 and 529 get exponential backoff, as the docs prescribe. The error message NEVER
    includes the request body, so neither the key nor the state leaks into a log.
    """
    body = {"state": state, "model": model, "questions": questions}
    wait = 1.0
    for t in range(attempts):
        try:
            r = client().post(URL, json=body)
        except httpx.HTTPError as e:
            if t == attempts - 1:
                raise FatalError(f"network failure: {type(e).__name__}") from None
            time.sleep(wait); wait *= 2
            continue
        if r.status_code == 200:
            d = r.json()
            LEDGER.record(d.get("usage") or {}, r.elapsed.total_seconds())
            return d
        if r.status_code in (429, 529) and t < attempts - 1:
            time.sleep(float(r.headers.get("retry-after") or wait)); wait *= 2
            continue
        if r.status_code in (401, 402, 422):
            raise FatalError(f"HTTP {r.status_code}: {r.text[:300]}")
        raise JevError(f"HTTP {r.status_code}: {r.text[:300]}")
    raise JevError("ran out of attempts")


def get(response: dict, qid: str) -> Any:
    """
    Unpack ONE answer.

      noul   -> float 0..1 (PROBABILITY, not a boolean)
      choice -> (option, confidence)
      score  -> (value, confidence)
    """
    a = (response.get("answers") or {}).get(qid)
    if a is None:
        raise JevError(f"question '{qid}' missing; got "
                       f"{sorted((response.get('answers') or {}).keys())}")
    kind = a.get("type")
    if kind == "noul":
        return float(a["noul"])
    if kind == "choice":
        return a["choice"], float(a.get("confidence") or 0.0)
    if kind == "score":
        return a["score"], float(a.get("confidence") or 0.0)
    raise JevError(f"unknown answer type: {kind!r}")


def in_batch(jobs: Iterable[tuple[Any, dict[str, dict]]], *,
             parallel: int = PARALLEL, model: str = MODEL,
             progress=None) -> list[dict | None]:
    """
    Several states in parallel, preserving INPUT ORDER. A failed item becomes None — it
    never drops out of the list, because a missing item would silently corrupt counts.
    """
    jobs = list(jobs)
    out: list[dict | None] = [None] * len(jobs)
    fatal: list[FatalError] = []
    done = 0

    def one(i: int) -> tuple[int, dict | None]:
        if fatal:
            return i, None
        state, qs = jobs[i]
        try:
            return i, ask(state, qs, model=model)
        except FatalError as e:
            fatal.append(e)
            return i, None
        except JevError:
            LEDGER.failures += 1
            return i, None

    with ThreadPoolExecutor(max_workers=parallel) as ex:
        for i, r in ex.map(one, range(len(jobs))):
            out[i] = r
            done += 1
            if progress and done % 25 == 0:
                progress(done, len(jobs))
    if fatal:
        raise fatal[0]
    if progress:
        progress(done, len(jobs))
    return out
