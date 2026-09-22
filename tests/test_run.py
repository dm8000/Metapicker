"""
The runner end to end, with the API MOCKED. No network, no cents.

It exists because a bug in the runner does not show up as an error: it shows up as spent
credit. Covers the three requirements — resumability, raw probability, budget stop.
"""
import sqlite3, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metapicker import jev, run, screening  # noqa: E402

CALLS = []


def fake_ask(state, questions, **kw):
    """Returns the exact API shape. High score only when the RECORD matches — looking at
    the whole state does not work, because the review title is in every one of them."""
    CALLS.append(state)
    hit = "wilson" in (state["record"]["title"] or "").lower()
    jev.LEDGER.record({"input_tokens": 800, "output_tokens": 0}, 0.3)
    return {"model": "jev-1.13.0",
            "answers": {q: {"type": "noul", "noul": 0.9 if hit else 0.1}
                        for q in questions},
            "usage": {"input_tokens": 800, "output_tokens": 0}}


def build(n=10):
    review = {"r1": {"title": "Therapies for Wilson disease",
                     "raw_criteria": "controlled studies"}}
    records = [{"review_id": "r1", "record_id": f"W{i}",
                "title": "Wilson disease trial" if i % 2 == 0 else "Unrelated topic",
                "abstract": "abstract text", "year": "2020",
                "label_ta": i % 2, "label_final": i % 2} for i in range(n)]
    return review, records


def _con():
    return run.connect(Path(tempfile.mkdtemp()) / "t.sqlite")


def test_stores_probability_not_boolean():
    jev.ask, real = fake_ask, jev.ask
    try:
        con = _con(); review, records = build(6)
        run.run(con, "synergy", review, records)
        values = [v for (v,) in con.execute("SELECT value FROM answers")]
        assert values and all(isinstance(v, float) for v in values)
        assert set(values) == {0.9, 0.1}, f"expected probabilities, got {set(values)}"
        assert len(values) == 6 * len(screening.IDS), "a question was not stored"
    finally:
        jev.ask = real


def test_resumes_without_repeating_calls():
    jev.ask, real = fake_ask, jev.ask
    try:
        con = _con(); review, records = build(8)
        CALLS.clear()
        run.run(con, "synergy", review, records)
        first = len(CALLS)
        r2 = run.run(con, "synergy", review, records)      # again, same data
        assert len(CALLS) == first, "resuming re-made calls — that would spend again"
        assert r2["new"] == 0 and r2["skipped"] == 8
    finally:
        jev.ask = real


def test_resumes_only_what_is_missing():
    jev.ask, real = fake_ask, jev.ask
    try:
        con = _con(); review, records = build(10)
        run.run(con, "synergy", review, records[:4])
        CALLS.clear()
        r = run.run(con, "synergy", review, records)
        assert r["new"] == 6 and len(CALLS) == 6, \
            f"should call only the missing 6, called {len(CALLS)}"
    finally:
        jev.ask = real


def test_budget_stop():
    jev.ask, real = fake_ask, jev.ask
    try:
        con = _con(); review, records = build(500)
        jev.LEDGER.reset()
        # 800 tok/call = US$ 0.0000336. A US$ 0.0001 cap is ~3 calls -> cuts in the first
        # chunk of 2.
        r = run.run(con, "synergy", review, records, budget=0.0001, chunk=2)
        assert r["not_run"] > 0, "the budget cap did not cut the run"
        assert r["new"] < 500, "ran everything despite the cap"
    finally:
        jev.ask = real


def test_labels_land_in_the_database():
    jev.ask, real = fake_ask, jev.ask
    try:
        con = _con(); review, records = build(4)
        run.run(con, "synergy", review, records)
        n = con.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
        assert n == 4, "the evaluator depends on labels stored alongside"
        assert con.execute("SELECT SUM(label_ta) FROM labels").fetchone()[0] == 2
    finally:
        jev.ask = real


def test_a_failure_does_not_vanish_from_the_count():
    def failing(state, questions, **kw):
        raise jev.JevError("simulated")
    jev.ask, real = failing, jev.ask
    try:
        con = _con(); review, records = build(3)
        r = run.run(con, "synergy", review, records)
        assert r["new"] == 0
        n = con.execute("SELECT COUNT(*) FROM calls WHERE error IS NOT NULL").fetchone()[0]
        assert n == 3, "a failure has to stay on record, not disappear"
    finally:
        jev.ask = real


if __name__ == "__main__":
    fs = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in fs:
        jev.LEDGER.reset(); f(); print(f"  ok  {f.__name__}")
    print(f"\n{len(fs)} tests passed")
