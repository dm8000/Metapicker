#!/usr/bin/env python3
"""
JEV in BATCH: N records per call, one `choice` question per record.

WHY THIS EXISTS. The original run sent ONE record per call, following the fan-out example
in the docs — which is one item with many questions. Measured: 88% of JEV's bill was
repeated header (criteria 460,628 tokens, instructions and structure ~469,000) against
99,724 tokens for the records themselves. A design error, not an API limitation.

The `state` accepts an array and questions are evaluated in parallel against it, so a
question can address item `n`. The task instruction lives in the state ONCE; each question
stays minimal.

THE RISK IS CROSS-CONTAMINATION BETWEEN ITEMS: with 100 records in the array, anchoring
"considering only record n" is harder than with 5. That is why this writes to its own
variant, so the batch judgement can be compared item by item against the one-at-a-time
run already in the database.

    ./run_batch.py --per-call 100
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metapicker import jev, run  # noqa: E402
from metapicker.sources import synergy  # noqa: E402

COMPARISON = ["Theobald_2021", "Hanlon_2022", "Deckers_2022"]

INSTRUCTION = (
    "Screening titles and abstracts for a systematic review. For each record in the "
    "records array, decide whether it should be retrieved for full-text assessment "
    "against the eligibility criteria. Screening is recall-oriented: excluding requires "
    "evidence, including does not. Many records have no abstract; absence of information "
    "is not grounds for exclusion. Judge each record on its own, independently of the "
    "others.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reviews", nargs="*", default=COMPARISON)
    ap.add_argument("--per-call", type=int, default=100)
    ap.add_argument("--variant", default="batch")
    ap.add_argument("--budget", type=float, default=0.05)
    a = ap.parse_args()

    con = run.connect()
    done = {(r, d) for r, d in con.execute(
        "SELECT DISTINCT review_id, record_id FROM answers WHERE source='synergy' "
        "AND variant=?", (a.variant,))}
    t0 = time.time()
    cost0 = jev.LEDGER.cost
    total = 0

    for review_id in a.reviews:
        records, reviews = synergy.load([review_id])
        pending = [r for r in records if (review_id, r.record_id) not in done]
        if not pending:
            print(f"  {review_id}: already done", file=sys.stderr); continue
        rev = vars(reviews[0])
        blocks = [pending[i:i + a.per_call] for i in range(0, len(pending), a.per_call)]

        jobs = []
        for block in blocks:
            state = {
                "task": INSTRUCTION,
                "review": rev.get("title") or review_id,
                "eligibility_criteria": rev.get("raw_criteria") or "",
                "records": [{"n": i,
                             "title": (r.title or "").strip(),
                             "abstract": (r.abstract or "").strip()
                                         or "(no abstract available for this record)"}
                            for i, r in enumerate(block, 1)],
            }
            questions = {f"r{i}": jev.choice(
                f"Record {i} in the records array.",
                {"retrieve": "retrieve this record for full-text assessment",
                 "exclude": "discard this record on title and abstract alone"})
                for i in range(1, len(block) + 1)}
            jobs.append((state, questions))

        results = jev.in_batch(jobs, parallel=4)
        rows = []
        for block, resp in zip(blocks, results):
            if resp is None:
                continue
            for i, r in enumerate(block, 1):
                try:
                    option, _ = jev.get(resp, f"r{i}")
                except jev.JevError:
                    continue
                rows.append(("synergy", review_id, r.record_id, a.variant, "choice",
                             1.0 if option == "retrieve" else 0.0))
        con.executemany("INSERT OR IGNORE INTO answers VALUES (?,?,?,?,?,?)", rows)
        con.commit()
        total += len(rows)
        print(f"  {review_id}: {len(rows)}/{len(pending)} in {len(blocks)} calls · "
              f"US$ {jev.LEDGER.cost - cost0:.4f}", file=sys.stderr)
        if a.budget and jev.LEDGER.cost - cost0 >= a.budget:
            print("  BUDGET STOP", file=sys.stderr); break

    print(f"\n  {total:,} records · {(time.time()-t0)/60:.1f} min")
    for l in jev.LEDGER.lines():
        print(l)
    if jev.LEDGER.calls:
        print(f"  {jev.LEDGER.input/max(1,total):.0f} tokens per record "
              f"(one-at-a-time cost 979)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
