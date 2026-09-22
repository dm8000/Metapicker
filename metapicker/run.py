#!/usr/bin/env python3
"""
Runs JEV over a screening set and stores RAW PROBABILITIES in SQLite.

THREE REQUIREMENTS, each for a concrete reason:

  1. RESUMABLE. Unique key (source, review_id, record_id, question) with INSERT OR
     IGNORE. Tens of thousands of calls will hit transient failures; rerunning continues
     where it stopped instead of paying for everything again.
  2. PROBABILITY, NEVER A BOOLEAN. Every threshold, every metric and every re-analysis
     comes out of here without spending again. Writing `eligible: true` throws away
     exactly the information that makes the experiment re-runnable.
  3. BUDGET STOP. `--budget` cuts the run when accumulated cost passes the cap. With
     limited credit, an unbraked loop spends everything before anyone looks.

    ./run.py --source synergy --reviews Donners_2021 Sep_2021 --budget 0.10
    ./run.py --source synergy --budget 0.70        # up to the cap, resumable
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metapicker import jev, screening  # noqa: E402

DB = ROOT / "results" / "answers.sqlite"

# Which option of each `choice` question counts as positive.
POSITIVE = {"choice": "retrieve"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    source     TEXT NOT NULL,
    review_id  TEXT NOT NULL,
    record_id  TEXT NOT NULL,
    variant    TEXT NOT NULL,      -- 'abstract' | 'batch' | 'qwen' | 'deepseek' ...
    question   TEXT NOT NULL,
    value      REAL NOT NULL,      -- probability 0..1, NEVER a boolean
    PRIMARY KEY (source, review_id, record_id, variant, question)
);
CREATE TABLE IF NOT EXISTS calls (
    source    TEXT, review_id TEXT, record_id TEXT, variant TEXT,
    tokens    INTEGER, seconds REAL, error TEXT, at REAL,
    PRIMARY KEY (source, review_id, record_id, variant)
);
CREATE TABLE IF NOT EXISTS labels (
    source    TEXT, review_id TEXT, record_id TEXT,
    label_ta  INTEGER, label_final INTEGER, has_abstract INTEGER,
    PRIMARY KEY (source, review_id, record_id)
);
CREATE INDEX IF NOT EXISTS ix_ans ON answers (source, review_id, question);
"""


def connect(path: Path = DB) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=60)
    c.executescript(SCHEMA)
    return c


def already_done(con: sqlite3.Connection, source: str, variant: str) -> set[tuple[str, str]]:
    return {(r, d) for r, d in con.execute(
        "SELECT DISTINCT review_id, record_id FROM calls "
        "WHERE source=? AND variant=? AND error IS NULL", (source, variant))}


def run(con: sqlite3.Connection, source: str, reviews: dict, records: list,
        *, variant: str = "abstract", questions: dict | None = None,
        budget: float = 0.0, parallel: int = jev.PARALLEL,
        chunk: int = 200) -> dict:
    """
    `reviews`: {review_id: review dict}. `records`: list of unified record dicts. Labels
    are written alongside so the evaluator does not depend on the original load.
    """
    questions = questions or screening.QUESTIONS
    done = already_done(con, source, variant)
    pending = [r for r in records if (r["review_id"], r["record_id"]) not in done]

    print(f"  {len(records):,} records · {len(done):,} already done · "
          f"{len(pending):,} pending", file=sys.stderr)
    if not pending:
        return {"new": 0, "skipped": len(done)}

    cost0 = jev.LEDGER.cost
    t0 = time.time()
    new = stopped = 0

    for start in range(0, len(pending), chunk):
        if budget and (jev.LEDGER.cost - cost0) >= budget:
            stopped = len(pending) - start
            print(f"\n  BUDGET STOP: US$ {jev.LEDGER.cost - cost0:.4f} "
                  f">= US$ {budget:.2f}. {stopped:,} records not run.", file=sys.stderr)
            break

        block = pending[start:start + chunk]
        jobs = [(screening.build_state(reviews[r["review_id"]], r), questions)
                for r in block]
        results = jev.in_batch(jobs, parallel=parallel)

        rows_a, rows_c, rows_l = [], [], []
        for rec, resp in zip(block, results):
            key = (source, rec["review_id"], rec["record_id"], variant)
            if resp is None:
                rows_c.append((*key, 0, 0.0, "failed", time.time()))
                continue
            for q in questions:
                try:
                    v = jev.get(resp, q)
                except jev.JevError:
                    continue
                if isinstance(v, tuple):
                    # `choice` returns (option, confidence). We store the DECISION as 1/0
                    # — confidence is dropped, because the whole point of using `choice`
                    # is that there is no continuous number for anyone to threshold later.
                    v = 1.0 if v[0] == POSITIVE.get(q, "retrieve") else 0.0
                rows_a.append((*key[:3], variant, q, float(v)))
            rows_c.append((*key, int((resp.get("usage") or {}).get("input_tokens") or 0),
                           0.0, None, time.time()))
            rows_l.append((source, rec["review_id"], rec["record_id"],
                           rec.get("label_ta"), rec["label_final"],
                           1 if (rec.get("abstract") or "").strip() else 0))
            new += 1

        con.executemany("INSERT OR IGNORE INTO answers VALUES (?,?,?,?,?,?)", rows_a)
        con.executemany("INSERT OR IGNORE INTO calls   VALUES (?,?,?,?,?,?,?,?)", rows_c)
        con.executemany("INSERT OR IGNORE INTO labels  VALUES (?,?,?,?,?,?)", rows_l)
        con.commit()

        at = min(start + chunk, len(pending))
        sys.stderr.write(
            f"\r  {at:,}/{len(pending):,}"
            f" · US$ {jev.LEDGER.cost - cost0:.4f} · {(time.time()-t0)/60:.1f} min   ")
    sys.stderr.write("\n")
    return {"new": new, "skipped": len(done), "not_run": stopped,
            "cost": jev.LEDGER.cost - cost0, "minutes": (time.time() - t0) / 60}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="synergy", choices=["synergy", "metasyn", "chan"])
    ap.add_argument("--reviews", nargs="*", help="ids; empty = all")
    ap.add_argument("--limit", type=int, default=0, help="max records per review")
    ap.add_argument("--budget", type=float, default=0.0, help="cap in US$; 0 = no cap")
    ap.add_argument("--holistic-only", action="store_true",
                    help="only the `retrieve` question (~30%% fewer tokens)")
    ap.add_argument("--choice", action="store_true",
                    help="`choice` primitive: NATIVE binary decision, no threshold "
                         "picked by us")
    ap.add_argument("--variant", default="abstract")
    ap.add_argument("--parallel", type=int, default=jev.PARALLEL)
    a = ap.parse_args()

    if a.source == "synergy":
        from metapicker.sources import synergy
        records, reviews = synergy.load()
    else:
        print(f"source '{a.source}' not implemented yet", file=sys.stderr)
        return 1

    review_map = {v.review_id: vars(v) for v in reviews}
    recs = [vars(r) for r in records]
    if a.reviews:
        recs = [r for r in recs if r["review_id"] in set(a.reviews)]
    if a.limit:
        seen: dict[str, int] = {}
        cut = []
        for r in recs:
            k = r["review_id"]
            if seen.get(k, 0) < a.limit:
                cut.append(r); seen[k] = seen.get(k, 0) + 1
        recs = cut
    if not recs:
        print("no records selected", file=sys.stderr)
        return 1

    questions = ({"choice": screening.CHOICE} if a.choice
                 else screening.holistic_only() if a.holistic_only
                 else screening.QUESTIONS)
    con = connect()
    r = run(con, a.source, review_map, recs, variant=a.variant, questions=questions,
            budget=a.budget, parallel=a.parallel)

    print(f"\n  new {r['new']:,} · skipped {r['skipped']:,}"
          + (f" · NOT RUN {r['not_run']:,}" if r.get("not_run") else ""))
    for l in jev.LEDGER.lines():
        print(l)
    return 0


if __name__ == "__main__":
    sys.exit(main())
