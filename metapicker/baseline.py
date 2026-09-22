#!/usr/bin/env python3
"""
BM25 baseline: ranks records by similarity between the eligibility criteria and
title+abstract. The same information the models get, with no model at all.

WHY IT IS MANDATORY. An AUC of 0.85 means nothing on its own. If BM25 — which is word
counting — comes close, the model is not reading criteria, it is matching topic, and
there is no news. Runs on CPU.

BM25 is implemented here rather than pulled in as a dependency for twenty lines.

    ./baseline.py --source synergy
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metapicker import evaluate as E  # noqa: E402

WORD = re.compile(r"[a-z0-9]+")
# Minimal list: only what distorts BM25 through raw frequency. Not linguistic cleaning.
STOP = set("""a an the of in on for to and or with without by from as at is are was
were be been being this that these those we our it its their they he she which who whom
not no nor if then than so such but had has have do does did can could would should may
might must will shall""".split())


def tokens(t: str) -> list[str]:
    return [w for w in WORD.findall((t or "").lower())
            if w not in STOP and len(w) > 2]


def bm25(query: list[str], docs: list[list[str]], k1: float = 1.5,
         b: float = 0.75) -> list[float]:
    n = len(docs)
    if not n:
        return []
    lengths = [len(d) for d in docs]
    avg = sum(lengths) / n or 1.0
    df = Counter()
    for d in docs:
        df.update(set(d))
    idf = {w: math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) for w in set(query)
           if w in df}
    out = []
    for d, ld in zip(docs, lengths):
        tf = Counter(d)
        s = 0.0
        for w, iw in idf.items():
            f = tf.get(w, 0)
            if f:
                s += iw * f * (k1 + 1) / (f + k1 * (1 - b + b * ld / avg))
        out.append(s)
    return out


def score_records(review: dict, records: list[dict]) -> list[float]:
    crit = review.get("raw_criteria") or review.get("title") or ""
    query = tokens(f"{review.get('title','')} {crit}")
    docs = [tokens(f"{r.get('title','')} {r.get('abstract','')}") for r in records]
    return bm25(query, docs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="synergy")
    ap.add_argument("--target", default="ta", choices=["ta", "final"])
    ap.add_argument("--reviews", nargs="*")
    a = ap.parse_args()

    if a.source != "synergy":
        print("synergy only for now", file=sys.stderr); return 1
    from metapicker.sources import synergy
    records, reviews = synergy.load(a.reviews or None)
    per_review: dict[str, list] = {}
    for r in records:
        per_review.setdefault(r.review_id, []).append(vars(r))

    field = "label_ta" if a.target == "ta" else "label_final"
    per: dict[str, tuple[list[float], list[int]]] = {}
    for v in reviews:
        g = per_review.get(v.review_id) or []
        lab = [x[field] for x in g]
        if any(r is None for r in lab) or not sum(r for r in lab if r):
            continue
        per[v.review_id] = (score_records(vars(v), g), [int(r) for r in lab])

    print("=" * 78)
    print(f"BM25 BASELINE · {a.source} · target="
          + ("passed T/A" if a.target == "ta" else "included in synthesis"))
    print("=" * 78, "")
    print("\n".join(E.report(per, "BM25 (criteria × title+abstract)")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
