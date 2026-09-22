#!/usr/bin/env python3
"""
Operating threshold chosen OUT-OF-FOLD, by leave-one-review-out.

WHY THIS IS NOT OPTIONAL. Picking the threshold that maximises a metric and then
reporting that metric on the SAME data inflates the result without warning. The number
comes out pretty, nobody sees the leak, and the conclusion is false. Here each review's
threshold is fitted on the OTHERS, and the gap between the honest and the inflated number
is reported in percentage points — if the gap is large, the threshold does not generalise
and the result is worthless.

Screening is RECALL-oriented: losing a study is the error that matters, while a false
positive costs only one full text to read. So the default fitting criterion is "the
highest threshold that still reaches 95% sensitivity", not "the one that maximises
accuracy".

    ./threshold.py --source synergy --target ta
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metapicker import evaluate as E  # noqa: E402

DB = ROOT / "results" / "answers.sqlite"


def fitted_threshold(train: list[tuple[list[float], list[int]]],
                     target_recall: float = 0.95, quantile: float = 0.0) -> float:
    """
    Each training review's threshold that reaches the target recall, aggregated by
    QUANTILE.

    `quantile=0` is the minimum. It looked right — it preserves recall on the unseen
    review — and with 3 reviews it cost 3.7 pp of specificity. With 21 it costs 40.5 pp:
    the minimum is pinned by the most extreme review in the set, so the MORE training
    reviews you have, the more conservative the threshold becomes. A rule that gets worse
    with data, which is why the quantile is a parameter and the report compares both.
    """
    ts = sorted(E.threshold_for_recall(sc, lab, target_recall)
                for sc, lab in train if sum(lab))
    if not ts:
        return 0.5
    i = min(len(ts) - 1, max(0, int(round(quantile * (len(ts) - 1)))))
    return ts[i]


def out_of_fold(per: dict[str, tuple[list[float], list[int]]],
                target_recall: float = 0.95, quantile: float = 0.0) -> dict:
    revs = [r for r in per if sum(per[r][1]) > 0]
    if len(revs) < 2:
        return {"error": f"only {len(revs)} review(s) with positives — "
                         "leave-one-out needs at least 2"}
    rows, sens_o, spec_o, sens_i, spec_i, thresholds = [], [], [], [], [], []
    for r in revs:
        train = [per[o] for o in revs if o != r]
        t_out = fitted_threshold(train, target_recall, quantile)
        sc, lab = per[r]
        c_out = E.classic(sc, lab, t_out)
        t_in = E.threshold_for_recall(sc, lab, target_recall)   # deliberate leak
        c_in = E.classic(sc, lab, t_in)
        rows.append((r, len(sc), sum(lab), t_out, c_out, t_in, c_in))
        thresholds.append(t_out)
        for lst, v in ((sens_o, c_out["sensitivity"]), (spec_o, c_out["specificity"]),
                       (sens_i, c_in["sensitivity"]), (spec_i, c_in["specificity"])):
            if not math.isnan(v):
                lst.append(v)
    avg = lambda x: sum(x) / len(x) if x else float("nan")  # noqa: E731
    return {"rows": rows, "thresholds": thresholds,
            "sens_out": avg(sens_o), "spec_out": avg(spec_o),
            "sens_in": avg(sens_i), "spec_in": avg(spec_i)}


def report(res: dict, target_recall: float) -> list[str]:
    if "error" in res:
        return [f"  {res['error']}"]
    L = [f"{'review':28} {'n':>7} {'pos':>5} {'thresh':>7} {'sens':>7} {'spec':>7} "
         f"{'FN':>4}",
         "  (threshold fitted on the OTHER reviews — never on its own)"]
    for r, n, pos, t, c, _, _ in sorted(res["rows"], key=lambda x: -x[1]):
        L.append(f"{r[:28]:28} {n:7,} {pos:5} {t:7.3f} {c['sensitivity']:7.1%} "
                 f"{c['specificity']:7.1%} {c['fn']:4}")
    d_sens = (res["sens_in"] - res["sens_out"]) * 100
    d_spec = (res["spec_in"] - res["spec_out"]) * 100
    L += ["",
          f"  OUT-OF-FOLD (honest)      sensitivity {res['sens_out']:6.1%}   "
          f"specificity {res['spec_out']:6.1%}",
          f"  fitted on its own data    sensitivity {res['sens_in']:6.1%}   "
          f"specificity {res['spec_in']:6.1%}",
          f"  leakage avoided: {d_sens:+.1f} pp of sensitivity, "
          f"{d_spec:+.1f} pp of specificity",
          "",
          f"  fitting target: {target_recall:.0%} sensitivity. If out-of-fold sensitivity",
          f"  lands well below that, the threshold does not generalise across reviews —",
          f"  and a single threshold for all of them is not usable.", ""]
    return L


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="synergy")
    ap.add_argument("--target", default="ta", choices=["ta", "final"])
    ap.add_argument("--variant", default="abstract")
    ap.add_argument("--question", default="retrieve")
    ap.add_argument("--recall", type=float, default=0.95)
    ap.add_argument("--quantile", type=float, default=0.0,
                    help="0=minimum (conservative), 0.1=10th percentile, 0.5=median")
    ap.add_argument("--db", type=Path, default=DB)
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    per = E.load(con, a.source, a.question, a.variant, a.target)
    if not per:
        print("no data for those filters", file=sys.stderr)
        return 1
    print("=" * 78)
    print(f"OUT-OF-FOLD THRESHOLD · {a.source} · «{a.question}» · target="
          + ("passed T/A" if a.target == "ta" else "included in synthesis"))
    print("=" * 78)
    print("\n".join(report(out_of_fold(per, a.recall, a.quantile), a.recall)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
