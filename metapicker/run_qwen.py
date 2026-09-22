#!/usr/bin/env python3
"""
Runs Qwen3-Reranker over the reviews and stores RAW scores in the same SQLite as JEV.

It runs over all 21 reviews, not just the 3 in the comparison set, for two reasons: it
provides the threshold-fitting set (the 18 outside the comparison) and it delivers a
JEV-vs-Qwen comparison over the full benchmark. It is local and free — the only cost is
wall time.

    ./run_qwen.py                          # 0.6B
    ./run_qwen.py --variant qwen8b         # 8B, separate variant so both can be compared
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metapicker import qwen, run  # noqa: E402
from metapicker.sources import synergy  # noqa: E402

# The same 21 reviews JEV ran on — the comparison has to be over the same records, not
# over different samples.
THE_21 = """Hanlon_2022 Brons_2024 Theobald_2021 Kapuka_2021 Quevedo_2023 Zinsser_2022
Deckers_2022 Oliveira_2021 Toffalini_2021 Abgaz_2023 van_der_Valk_2021 Clark_2021
Wijnen_2024 Sanchez-Acedo_2023 Maciel_2024 Noetel_2021 Sanchez-Gomez_2024 Fong_2021
Tumkaya_2018 Cinquin_2018 Mejean_2024""".split()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reviews", nargs="*", default=THE_21)
    ap.add_argument("--block", type=int, default=64,
                    help="documents per request; the server has 8 slots of 4096 tokens")
    ap.add_argument("--parallel", type=int, default=qwen.PARALLEL)
    ap.add_argument("--variant", default="qwen",
                    help="'qwen' = 0.6B, 'qwen8b' = 8B")
    a = ap.parse_args()

    con = run.connect()
    done = {(r, d) for r, d in con.execute(
        "SELECT DISTINCT review_id, record_id FROM answers "
        "WHERE source='synergy' AND variant=?", (a.variant,))}
    t0 = time.time()
    total = 0

    for n, review_id in enumerate(a.reviews, 1):
        records, reviews = synergy.load([review_id])
        if not reviews:
            print(f"  [{n}/{len(a.reviews)}] {review_id}: missing", file=sys.stderr)
            continue
        pending = [r for r in records if (review_id, r.record_id) not in done]
        if not pending:
            print(f"  [{n}/{len(a.reviews)}] {review_id}: already done", file=sys.stderr)
            continue
        q = qwen.query(vars(reviews[0]))
        blocks = [pending[i:i + a.block] for i in range(0, len(pending), a.block)]
        jobs = [(q, [qwen.document(r.title, r.abstract) for r in b], review_id)
                for b in blocks]
        results = qwen.in_batch(jobs, parallel=a.parallel)

        rows, labels = [], []
        for block, scores in zip(blocks, results):
            if scores is None:
                continue
            for r, s in zip(block, scores):
                rows.append(("synergy", review_id, r.record_id, a.variant, "relevance",
                             float(s)))
                labels.append(("synergy", review_id, r.record_id, r.label_ta,
                               r.label_final, 1 if (r.abstract or "").strip() else 0))
        con.executemany("INSERT OR IGNORE INTO answers VALUES (?,?,?,?,?,?)", rows)
        con.executemany("INSERT OR IGNORE INTO labels VALUES (?,?,?,?,?,?)", labels)
        con.commit()
        total += len(rows)
        print(f"  [{n}/{len(a.reviews)}] {review_id}: {len(rows):,} · "
              f"{(time.time()-t0)/60:.1f} min", file=sys.stderr)

    print(f"\n  {total:,} scores stored in {(time.time()-t0)/60:.1f} min")
    for l in qwen.LEDGER.lines():
        print(l)
    if qwen.TRUNCATED:
        print(f"\n  WARNING — truncated in {qwen.TRUNCATED}: the comparison on those")
        print("  reviews is compromised, the reranker got less criteria text than JEV.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
