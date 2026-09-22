"""
Locks JEV's two traps against regression. Makes no network calls.

  1. the answer lives at r["answers"][qid], NOT at r[qid]
  2. noul returns a PROBABILITY, not a boolean
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metapicker import jev  # noqa: E402

# The exact shape the API returns.
RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "retrieve": {"type": "noul", "noul": 0.87},
        "population": {"type": "noul", "noul": 0.03},
        "domain": {"type": "choice", "choice": "oncology", "confidence": 0.61},
        "strength": {"type": "score", "score": 3.4, "confidence": 0.55},
    },
    "usage": {"input_tokens": 812, "output_tokens": 0},
}


def test_noul_is_float_not_bool():
    v = jev.get(RESPONSE, "retrieve")
    assert isinstance(v, float), f"noul should be float, got {type(v).__name__}"
    assert not isinstance(v, bool), "noul became a boolean — trap 2 is back"
    assert v == 0.87


def test_low_noul_is_not_silently_falsy():
    # 0.03 is a strong NO. If any code does `if value:` it disappears. This test documents
    # that the value is continuous and that 0.03 is not False.
    v = jev.get(RESPONSE, "population")
    assert 0.0 <= v <= 1.0 and v == 0.03


def test_choice_and_score_carry_confidence():
    option, c = jev.get(RESPONSE, "domain")
    assert option == "oncology" and c == 0.61
    value, c2 = jev.get(RESPONSE, "strength")
    assert value == 3.4 and c2 == 0.55


def test_answer_is_not_at_the_root():
    # Trap 1: r[qid] does not exist. If it ever does, the design changed.
    assert "retrieve" not in RESPONSE, "the API now exposes at the root — revisit get()"


def test_missing_question_raises_listing_what_arrived():
    try:
        jev.get(RESPONSE, "nonexistent")
    except jev.JevError as e:
        assert "retrieve" in str(e), "the error must list what arrived, for diagnosis"
    else:
        raise AssertionError("a missing question should raise JevError")


def test_402_is_fatal_not_a_silent_none():
    """Exhausted credit has to ABORT the batch. Swallowing it as an ordinary failure makes
    the runner announce «no calls» after thousands of attempts — which really happened."""

    class FakeResponse:
        status_code = 402
        text = '{"detail":{"error_type":"billing_error"}}'
        headers: dict = {}

    class FakeClient:
        def post(self, *a, **k):
            return FakeResponse()

    real = jev.client
    jev.client = lambda: FakeClient()
    try:
        try:
            jev.ask("x", {"q": jev.noul("y")}, attempts=3)
        except jev.FatalError:
            pass
        else:
            raise AssertionError("402 should raise FatalError")
        try:
            jev.in_batch([("x", {"q": jev.noul("y")})] * 4)
        except jev.FatalError:
            pass
        else:
            raise AssertionError("in_batch should propagate FatalError, not swallow it")
    finally:
        jev.client = real


def test_ledger_computes_cost():
    c = jev.Ledger()
    c.record({"input_tokens": 1_000_000, "output_tokens": 0}, 1.0)
    assert abs(c.cost - 0.042) < 1e-9, f"wrong cost: {c.cost}"


def test_score_rejects_invalid_level_count():
    try:
        jev.score("x", ["a"])
    except ValueError:
        pass
    else:
        raise AssertionError("score with 1 level should be rejected")


if __name__ == "__main__":
    fs = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in fs:
        f()
        print(f"  ok  {f.__name__}")
    print(f"\n{len(fs)} tests passed")
