"""
Metrics against cases with known answers. A wrong metric does not warn you: it just
returns a plausible number. These tests exist so the wrong number fails loudly.
"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metapicker import evaluate as A  # noqa: E402


def test_auc_perfect_and_inverted():
    sc = [0.9, 0.8, 0.2, 0.1]; lab = [1, 1, 0, 0]
    assert A.auc(sc, lab) == 1.0
    assert A.auc(sc, [0, 0, 1, 1]) == 0.0


def test_auc_all_ties_is_half():
    # All the same score: no information at all. Must be exactly 0.5.
    assert A.auc([0.5] * 6, [1, 0, 1, 0, 1, 0]) == 0.5


def test_auc_known_value():
    # 2 positives, 2 negatives; 1 inverted pair out of 4 -> 0.75
    assert abs(A.auc([0.9, 0.4, 0.6, 0.1], [1, 1, 0, 0]) - 0.75) < 1e-9


def test_screened_for_recall():
    # 10 records, 2 positives at ranks 1 and 5 in descending order
    sc = [1.0, .9, .8, .7, .6, .5, .4, .3, .2, .1]
    lab = [1, 0, 0, 0, 1, 0, 0, 0, 0, 0]
    assert abs(A.screened_for_recall(sc, lab, 0.5) - 0.1) < 1e-9   # 1 of 2 -> 1 item
    assert abs(A.screened_for_recall(sc, lab, 1.0) - 0.5) < 1e-9   # 2 of 2 -> 5 items


def test_wss_perfect_ranker():
    # 100 records, 5 positives at the top. 95% recall needs ceil(4.75)=5 items = 5% of
    # the list. WSS = (1-0.05) - 0.05 = 0.90
    sc = [1.0 - i / 100 for i in range(100)]
    lab = [1] * 5 + [0] * 95
    assert abs(A.wss(sc, lab, 0.95) - 0.90) < 1e-9


def test_wss_random_is_near_zero():
    # A ranker with no signal: positives spread out. Finding 95% of them requires
    # screening ~95% of the list, so WSS must land near 0 — not 0.9.
    n = 1000
    sc = [1.0 - i / n for i in range(n)]
    lab = [1 if i % 50 == 0 else 0 for i in range(n)]   # 20 positives, evenly spaced
    w = A.wss(sc, lab, 0.95)
    assert abs(w) < 0.06, f"WSS of a signal-free ranker should be ~0, got {w:.3f}"


def test_threshold_for_recall_hits_target():
    sc = [0.99, 0.80, 0.70, 0.30, 0.10]; lab = [1, 0, 1, 0, 0]
    t = A.threshold_for_recall(sc, lab, 1.0)
    c = A.classic(sc, lab, t)
    assert c["sensitivity"] == 1.0, "the 100%-recall threshold must catch all of them"


def test_classic_on_a_hand_case():
    sc = [0.9, 0.8, 0.4, 0.2]; lab = [1, 0, 1, 0]
    c = A.classic(sc, lab, 0.5)          # >= 0.5 -> positive: 0.9(TP) 0.8(FP)
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (1, 1, 1, 1)
    assert c["sensitivity"] == 0.5 and c["specificity"] == 0.5
    assert c["precision"] == 0.5 and abs(c["f1"] - 0.5) < 1e-9


def test_single_class_does_not_crash():
    assert math.isnan(A.auc([0.5, 0.6], [0, 0]))
    assert math.isnan(A.recall_at([0.5, 0.6], [0, 0], 0.5))


def test_diz_nao_a_tudo_tem_specificity_perfeita():
    # O motivo de sensitivity/specificity nao bastarem: o classificador inutil
    # marca 100% de specificity e 0% de sensitivity a 1% de prevalencia.
    sc = [0.0] * 1000
    lab = [1] * 10 + [0] * 990
    c = A.classic(sc, lab, 0.5)
    assert c["specificity"] == 1.0 and c["sensitivity"] == 0.0


if __name__ == "__main__":
    fs = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in fs:
        f(); print(f"  ok  {f.__name__}")
    print(f"\n{len(fs)} tests passed")
