#!/usr/bin/env python3
"""
Model comparison — JEV × Qwen reranker × DeepSeek — on the SAME binary decision.

Why binary: DeepSeek through the chat window only answers yes/no. Forcing all of them to
the common denominator is the fair framing, and the cost is declared — ranking was where
JEV was strong (AUC 0.920), and a binary decision throws that advantage away.

HOW EACH ONE BECOMES YES/NO, and the care each needs:

  DeepSeek   already binary.
  JEV        `choice` primitive: the decision belongs to the model, not to a threshold
             we picked.
  Qwen       produces only a score. Threshold fitted on the reviews OUTSIDE the
             comparison set — an entirely disjoint set, not leave-one-out.
  JEV-noul   a fourth column, thresholded like Qwen. Not redundant: measured against
             `choice`, it tells us how much of that threshold was our own arbitrariness.

    ./compare.py --target ta --with-abstract-only
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metapicker import evaluate as E, threshold as T  # noqa: E402

DB = ROOT / "results" / "answers.sqlite"
COMPARISON = ["Theobald_2021", "Hanlon_2022", "Deckers_2022"]


def threshold_from_outside(con, question: str, variant: str, target: str,
                           exclude: list[str]) -> tuple[float, int]:
    """Fitted on the reviews that are NOT in the comparison set."""
    per = E.load(con, "synergy", question, variant, target)
    outside = [per[r] for r in per if r not in exclude and sum(per[r][1])]
    if not outside:
        raise SystemExit(f"no outside reviews to fit «{question}»")
    return T.fitted_threshold(outside, 0.95, 0.10), len(outside)


def decisions(con, target: str, with_abstract_only: bool = False) -> tuple[dict, dict]:
    """
    {model: {(rev, rec): 0|1}} and {(rev, rec): label}.

    `with_abstract_only` restricts to records that HAVE an abstract. That is the
    normalisation that makes the comparison legitimate: the premise is that the authors
    screened on title and abstract, but 74% of the records they approved reach the model
    with the title alone — OpenAlex does not have those abstracts today, and the reviewer
    almost certainly had them in 2021. Measuring on the full slice measures who guesses
    better from a title, not who screens better.
    """
    col = "label_ta" if target == "ta" else "label_final"
    t_noul, n_noul = threshold_from_outside(con, "retrieve", "abstract", target,
                                            COMPARISON)
    t_qwen, n_qwen = threshold_from_outside(con, "relevance", "qwen", target, COMPARISON)
    print(f"  JEV-noul threshold  {t_noul:.3f}  (fitted on {n_noul} outside reviews)")
    print(f"  Qwen threshold      {t_qwen:.4f}  (fitted on {n_qwen} outside reviews)")

    sources = {"JEV (choice)":   ("choice", "choice", None),
               "JEV (noul→thr)": ("retrieve", "abstract", t_noul),
               "Qwen (rerank)":  ("relevance", "qwen", t_qwen),
               "DeepSeek":       ("enters", "deepseek", None)}
    models: dict[str, dict] = {}
    for name, (question, variant, thr) in sources.items():
        d = {}
        for rev, rec, v in con.execute(
                "SELECT review_id, record_id, value FROM answers WHERE source='synergy' "
                "AND question=? AND variant=?", (question, variant)):
            if rev in COMPARISON:
                d[(rev, rec)] = int(v >= thr) if thr is not None else int(v)
        models[name] = d
    extra = " AND has_abstract=1" if with_abstract_only else ""
    labels = {(r, d): v for r, d, v in con.execute(
        f"SELECT review_id, record_id, {col} FROM labels WHERE source='synergy' "
        f"AND {col} IS NOT NULL{extra}") if r in COMPARISON}
    return models, labels


def metrics(dec: dict, labels: dict, keys: list) -> dict:
    tp = sum(1 for k in keys if dec[k] and labels[k])
    fp = sum(1 for k in keys if dec[k] and not labels[k])
    fn = sum(1 for k in keys if not dec[k] and labels[k])
    tn = len(keys) - tp - fp - fn
    n = len(keys)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if prec + rec and prec == prec and rec == rec else 0.0
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (tn + fn) * (tn + fp)) / n ** 2
    return {"precision": prec, "recall": rec, "f1": f1, "overlap": po,
            "kappa": (po - pe) / (1 - pe) if pe < 1 else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "kept": tp + fp}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default="ta", choices=["ta", "final"])
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--with-abstract-only", action="store_true",
                    help="normalise: only records that HAVE an abstract, where the "
                         "premise «the authors screened on title and abstract» holds")
    a = ap.parse_args()
    con = sqlite3.connect(a.db)

    print("=" * 84)
    print("MODEL COMPARISON — binary decision · target: "
          + ("passed T/A screening" if a.target == "ta" else "included in synthesis"))
    if a.with_abstract_only:
        print("NORMALISED: records WITH an abstract only — where both sides had the "
              "same information")
    print("=" * 84)
    models, labels = decisions(con, a.target, a.with_abstract_only)

    missing = [m for m, d in models.items() if not d]
    if missing:
        print(f"\n  NO DATA YET: {', '.join(missing)}")
    models = {m: d for m, d in models.items() if d}
    if not models:
        return 1

    # Only records ALL of them judged — comparing over different sets is not comparing.
    common = sorted(set.intersection(*(set(d) for d in models.values())) & set(labels))
    print(f"\n  {len(common):,} records judged by all {len(models)} "
          f"({len(set(r for r, _ in common))} reviews)")
    if not common:
        return 1

    print("\n" + "─" * 84)
    print("AGAINST THE GROUND TRUTH")
    print("─" * 84)
    for rev in COMPARISON:
        ks = [k for k in common if k[0] == rev]
        if not ks:
            continue
        pos = sum(labels[k] for k in ks)
        print(f"\n{rev}   n={len(ks)}   positives={pos} ({pos/len(ks):.1%})")
        print(f"  {'model':16} {'kept':>6} {'precision':>10} {'recall':>8} "
              f"{'F1':>6} {'overlap':>8} {'kappa':>7}")
        for m, d in models.items():
            x = metrics(d, labels, ks)
            print(f"  {m:16} {x['kept']:6} {x['precision']:9.1%} {x['recall']:7.1%} "
                  f"{x['f1']:6.3f} {x['overlap']:7.1%} {x['kappa']:7.3f}")

    print(f"\n{'MACRO-AVERAGE across reviews':^84}")
    print(f"  {'model':16} {'precision':>10} {'recall':>8} {'F1':>6} {'overlap':>8} "
          f"{'kappa':>7}")
    revs = [r for r in COMPARISON if any(k[0] == r for k in common)]
    for m, d in models.items():
        xs = [metrics(d, labels, [k for k in common if k[0] == r]) for r in revs]
        avg = lambda c: sum(x[c] for x in xs if x[c] == x[c]) / len(xs)  # noqa: E731
        print(f"  {m:16} {avg('precision'):9.1%} {avg('recall'):7.1%} {avg('f1'):6.3f} "
              f"{avg('overlap'):7.1%} {avg('kappa'):7.3f}")

    print("\n" + "─" * 84)
    print("BETWEEN THE MODELS — do they agree with each other, regardless of being right?")
    print("─" * 84)
    names = list(models)
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            ov = sum(models[x][k] == models[y][k] for k in common) / len(common)
            px, py = (sum(models[n][k] for k in common) / len(common) for n in (x, y))
            pe = px * py + (1 - px) * (1 - py)
            print(f"  {x:16} × {y:16} overlap {ov:6.1%}   kappa "
                  f"{((ov - pe) / (1 - pe) if pe < 1 else 0):6.3f}")

    print("\n" + "─" * 84)
    print("WHERE THE DIFFERENCE IS")
    print("─" * 84)
    all_yes = [k for k in common if all(models[m][k] for m in names)]
    all_no = [k for k in common if not any(models[m][k] for m in names)]
    pos = [k for k in common if labels[k]]
    print(f"  all {len(names)} say YES: {len(all_yes):5} records "
          f"({sum(labels[k] for k in all_yes)} were positives)")
    print(f"  all {len(names)} say NO:  {len(all_no):5} records "
          f"({sum(labels[k] for k in all_no)} were positives — LOST by everyone)")
    print(f"\n  positives found by only ONE model (this is where they differ):")
    for m in names:
        only = [k for k in pos
                if models[m][k] and not any(models[o][k] for o in names if o != m)]
        print(f"    {m:16} {len(only):4}")

    if len(names) >= 3:
        vote = {k: int(sum(models[m][k] for m in names) * 2 > len(names)) for k in common}
        xs = [metrics(vote, labels, [k for k in common if k[0] == r]) for r in revs]
        avg = lambda c: sum(x[c] for x in xs if x[c] == x[c]) / len(xs)  # noqa: E731
        print(f"\n  majority vote of {len(names)}:  precision {avg('precision'):.1%} · "
              f"recall {avg('recall'):.1%} · F1 {avg('f1'):.3f} · "
              f"kappa {avg('kappa'):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
