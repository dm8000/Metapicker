#!/usr/bin/env python3
"""
Screening metrics, read from the raw probabilities in SQLite. Makes no API calls.

PER REVIEW, THEN MACRO-AVERAGE. Never pool all records into one bucket: reviews range
from hundreds to tens of thousands of records and from 0.2% to 27% prevalence.
Micro-averaging would let the largest review decide the number on its own.

THE METRICS THAT DECIDE, and why the four classic ones are not enough: at 0.8%
prevalence, a classifier that says "no" to everything scores 99.2% accuracy and 100%
specificity.

  AUC-ROC        threshold-free — the primary comparison between variants and predictors
  WSS@95         Work Saved over Sampling at 95% recall: how much screening is spared
                 while still finding 95% of the included studies. It is the field's
                 standard metric, and the only one that answers "would this actually
                 save work?"
  Recall@budget  recall after screening 10%, 20%, 50% of records
  Recall 100%    how much of the list must be screened to find ALL of them. In a
                 systematic review, the missed study is the error that matters.

Sensitivity, specificity, precision and F1 come out at the operating threshold, chosen
OUT-OF-FOLD by leave-one-review-out (threshold.py).

    ./evaluate.py --source synergy --target ta
    ./evaluate.py --source synergy --target final --question retrieve
"""

from __future__ import annotations

import argparse
import bisect
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "results" / "answers.sqlite"


# ------------------------------------------------------------------------- metrics

def auc(scores: list[float], labels: list[int]) -> float:
    """AUC-ROC by pair counting, with half a point for ties."""
    pos = [s for s, r in zip(scores, labels) if r]
    neg = sorted(s for s, r in zip(scores, labels) if not r)
    if not pos or not neg:
        return float("nan")
    t = 0.0
    for p in pos:
        lo = bisect.bisect_left(neg, p)
        t += lo + (bisect.bisect_right(neg, p) - lo) / 2
    return t / (len(pos) * len(neg))


def _ranked(scores: list[float], labels: list[int]) -> list[int]:
    """Labels in descending score order — the order a reviewer would screen the list."""
    return [r for _, r in sorted(zip(scores, labels), key=lambda x: -x[0])]


def recall_at(scores: list[float], labels: list[int], fraction: float) -> float:
    o = _ranked(scores, labels)
    total = sum(o)
    if not total:
        return float("nan")
    k = max(1, int(round(len(o) * fraction)))
    return sum(o[:k]) / total


def screened_for_recall(scores: list[float], labels: list[int], target: float) -> float:
    """Fraction of the list that must be screened to reach `target` recall."""
    o = _ranked(scores, labels)
    total = sum(o)
    if not total:
        return float("nan")
    needed = math.ceil(target * total)
    found = 0
    for i, r in enumerate(o, 1):
        found += r
        if found >= needed:
            return i / len(o)
    return 1.0


def wss(scores: list[float], labels: list[int], target: float = 0.95) -> float:
    """
    Work Saved over Sampling. `1 - fraction_screened` is gross work saved; subtracting
    `1 - target` discounts what random sampling would already give for free.
    """
    f = screened_for_recall(scores, labels, target)
    return float("nan") if math.isnan(f) else (1 - f) - (1 - target)


def confusion(scores: list[float], labels: list[int],
              threshold: float) -> tuple[int, int, int, int]:
    tp = sum(1 for s, r in zip(scores, labels) if s >= threshold and r)
    fp = sum(1 for s, r in zip(scores, labels) if s >= threshold and not r)
    fn = sum(1 for s, r in zip(scores, labels) if s < threshold and r)
    tn = sum(1 for s, r in zip(scores, labels) if s < threshold and not r)
    return tp, fp, fn, tn


def classic(scores: list[float], labels: list[int], threshold: float) -> dict:
    tp, fp, fn, tn = confusion(scores, labels, threshold)
    sens = tp / (tp + fn) if tp + fn else float("nan")
    spec = tn / (tn + fp) if tn + fp else float("nan")
    prec = tp / (tp + fp) if tp + fp else float("nan")
    f1 = (2 * prec * sens / (prec + sens)
          if prec + sens and not math.isnan(prec) and not math.isnan(sens) else 0.0)
    return {"sensitivity": sens, "specificity": spec, "precision": prec, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def threshold_for_recall(scores: list[float], labels: list[int],
                         target: float = 0.95) -> float:
    """Highest threshold that still reaches `target` sensitivity."""
    pairs = sorted(zip(scores, labels), key=lambda x: -x[0])
    total = sum(r for _, r in pairs)
    if not total:
        return 0.0
    needed = math.ceil(target * total)
    found = 0
    for s, r in pairs:
        found += r
        if found >= needed:
            return s
    return 0.0


# --------------------------------------------------------------------------- reading

def load(con: sqlite3.Connection, source: str, question: str, variant: str,
         target: str, with_abstract_only: bool = False
         ) -> dict[str, tuple[list[float], list[int]]]:
    col = "label_ta" if target == "ta" else "label_final"
    # 61% of the corpus has no abstract in OpenAlex, and the human reviewer almost
    # certainly had one. Restricting is the normalisation that makes the measurement
    # comparable to their judgement.
    extra = " AND l.has_abstract=1" if with_abstract_only else ""
    q = f"""SELECT a.review_id, a.value, l.{col}
            FROM answers a JOIN labels l
              ON a.source=l.source AND a.review_id=l.review_id
             AND a.record_id=l.record_id
            WHERE a.source=? AND a.question=? AND a.variant=?
              AND l.{col} IS NOT NULL{extra}"""
    per: dict[str, tuple[list[float], list[int]]] = {}
    for rev, v, r in con.execute(q, (source, question, variant)):
        s, t = per.setdefault(rev, ([], []))
        s.append(float(v)); t.append(int(r))
    return per


def combine_facets(con: sqlite3.Connection, source: str, variant: str, target: str,
                   mode: str = "min") -> dict[str, tuple[list[float], list[int]]]:
    """The five-facet aggregate, as a competing predictor against the holistic noul."""
    from metapicker.screening import FACETS
    col = "label_ta" if target == "ta" else "label_final"
    q = f"""SELECT a.review_id, a.record_id, a.question, a.value, l.{col}
            FROM answers a JOIN labels l
              ON a.source=l.source AND a.review_id=l.review_id
             AND a.record_id=l.record_id
            WHERE a.source=? AND a.variant=? AND l.{col} IS NOT NULL
              AND a.question IN ({','.join('?' * len(FACETS))})"""
    acc: dict[tuple[str, str], tuple[list[float], int]] = {}
    for rev, rec, _, v, r in con.execute(q, (source, variant, *FACETS)):
        vs, _ = acc.setdefault((rev, rec), ([], int(r)))
        vs.append(float(v))
    per: dict[str, tuple[list[float], list[int]]] = {}
    for (rev, _), (vs, r) in acc.items():
        if len(vs) < len(FACETS):
            continue
        v = min(vs) if mode == "min" else math.prod(vs)
        s, t = per.setdefault(rev, ([], []))
        s.append(v); t.append(r)
    return per


# ------------------------------------------------------------------------- reporting

def report(per: dict, name: str, threshold: float | None = None) -> list[str]:
    L = [f"── {name} " + "─" * max(0, 62 - len(name)), ""]
    L.append(f"{'review':28} {'n':>7} {'pos':>5} {'prev':>6} {'AUC':>6} "
             f"{'WSS@95':>7} {'R@10%':>6} {'R@20%':>6} {'100%':>6}")
    agg = {k: [] for k in ("auc", "wss", "r10", "r20", "t100")}
    for rev in sorted(per, key=lambda r: -len(per[r][0])):
        sc, lab = per[rev]
        n, pos = len(sc), sum(lab)
        if pos == 0 or pos == n:
            L.append(f"{rev[:28]:28} {n:7,} {pos:5}   — only one class")
            continue
        a, w = auc(sc, lab), wss(sc, lab)
        r10, r20 = recall_at(sc, lab, 0.10), recall_at(sc, lab, 0.20)
        t100 = screened_for_recall(sc, lab, 1.0)
        for k, v in zip(agg, (a, w, r10, r20, t100)):
            if not math.isnan(v):
                agg[k].append(v)
        L.append(f"{rev[:28]:28} {n:7,} {pos:5} {pos/n:5.2%} {a:6.3f} "
                 f"{w:7.1%} {r10:6.0%} {r20:6.0%} {t100:6.0%}")

    def m(k):
        return sum(agg[k]) / len(agg[k]) if agg[k] else float("nan")
    L += ["", f"{'MACRO-AVERAGE':28} {len(agg['auc']):7} {'':5} {'':6} {m('auc'):6.3f} "
              f"{m('wss'):7.1%} {m('r10'):6.0%} {m('r20'):6.0%} {m('t100'):6.0%}", ""]

    if threshold is not None:
        total = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        sens, specs, precs, f1s = [], [], [], []
        for rev in per:
            sc, lab = per[rev]
            if not sum(lab):
                continue
            c = classic(sc, lab, threshold)
            for k in total:
                total[k] += c[k]
            for lst, k in ((sens, "sensitivity"), (specs, "specificity"),
                           (precs, "precision"), (f1s, "f1")):
                if not math.isnan(c[k]):
                    lst.append(c[k])
        avg = lambda x: sum(x) / len(x) if x else float("nan")  # noqa: E731
        L += [f"at threshold {threshold:.3f} (macro-averaged across reviews):",
              f"   sensitivity {avg(sens):6.1%}    specificity {avg(specs):6.1%}",
              f"   precision   {avg(precs):6.1%}    F1          {avg(f1s):6.3f}",
              f"   totals: TP={total['tp']} FP={total['fp']} FN={total['fn']} "
              f"TN={total['tn']}", ""]
    return L


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="synergy")
    ap.add_argument("--target", default="ta", choices=["ta", "final"])
    ap.add_argument("--variant", default="abstract")
    ap.add_argument("--question", default="retrieve")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--facets", action="store_true", help="also the facet aggregate")
    ap.add_argument("--with-abstract-only", action="store_true")
    ap.add_argument("--db", type=Path, default=DB)
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    target_name = "passed T/A screening" if a.target == "ta" else "included in synthesis"
    L = ["=" * 78,
         f"JEV — title/abstract screening · source={a.source} · variant={a.variant}",
         f"target: {target_name}", "=" * 78, ""]

    per = load(con, a.source, a.question, a.variant, a.target, a.with_abstract_only)
    if not per:
        print("no data for those filters", file=sys.stderr)
        return 1
    L += report(per, f"noul «{a.question}» (holistic)", a.threshold)

    if a.facets:
        for mode in ("min", "product"):
            f = combine_facets(con, a.source, a.variant, a.target, mode)
            if f:
                L += report(f, f"facet aggregate ({mode})", a.threshold)

    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
