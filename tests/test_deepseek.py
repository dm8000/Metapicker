"""
The DeepSeek importer. The failure that matters on this front is NOT loud: it is a
truncated reply that looks complete — the model answers 60 of 100 lines, the file looks
legitimate, and 40 records vanish unnoticed. Each test below is one way that happens.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metapicker import deepseek as D  # noqa: E402


def _refuses(text, n, fragment):
    try:
        D.parse(text, n)
    except ValueError as e:
        assert fragment in str(e).lower(), f"wrong reason: {e}"
    else:
        raise AssertionError(f"should have refused ({fragment})")


def test_accepts_a_well_formed_reply():
    assert D.parse("1: YES\n2: NO\n3: YES", 3) == {1: 1, 2: 0, 3: 1}


def test_accepts_format_variations():
    # code fences, [n], accents, S/N, spaces — the chat formats replies differently
    assert D.parse("```\n[1]: YES\n2. NO\n3) S\n 4 - N \n```", 4) == {1: 1, 2: 0, 3: 1, 4: 0}


def test_accepts_the_portuguese_originals():
    # The archived experiment was run with a Portuguese prompt; SIM/NAO must still parse.
    assert D.parse("1: SIM\n2: NAO\n3: NÃO", 3) == {1: 1, 2: 0, 3: 0}


def test_refuses_a_truncated_reply():
    # The dangerous case: 60 perfect lines out of 100.
    _refuses("\n".join(f"{i}: YES" for i in range(1, 61)), 100, "truncated")


def test_refuses_a_repeated_number():
    _refuses("1: YES\n2: NO\n2: YES\n3: YES", 3, "twice")


def test_refuses_a_number_outside_the_range():
    _refuses("1: YES\n2: NO\n7: YES", 3, "outside the range")


def test_refuses_an_invented_verdict():
    _refuses("1: YES\n2: MAYBE\n3: YES", 3, "unrecognised")


def test_refuses_explanatory_prose():
    # The model "being helpful": explaining before answering.
    _refuses("Sure! Here is the analysis:\n1: YES\n2: NO\n3: YES", 3, "unrecognised")


def test_refuses_empty():
    _refuses("", 10, "truncated")


def test_does_not_confuse_no_with_yes():
    for neg in ("NO", "NAO", "NÃO", "no", "n", "N"):
        assert D.parse(f"1: {neg}", 1) == {1: 0}, f"{neg} became YES"
    for pos in ("YES", "yes", "SIM", "sim", "S", "s"):
        assert D.parse(f"1: {pos}", 1) == {1: 1}, f"{pos} became NO"


def test_round_trip_with_the_map():
    """generate -> answer -> import must match number to record_id."""
    import json
    f = next(D.OUT.glob("*-1.json"), None)
    if f is None:
        print("     (no batches generated — skipping)"); return
    m = json.loads(f.read_text())
    mapping = m.get("map") or m.get("mapa")
    n = len(mapping)
    v = D.parse("\n".join(f"{i}: {'YES' if i % 3 == 0 else 'NO'}"
                          for i in range(1, n + 1)), n)
    assert len(v) == n
    assert len({mapping[str(k)] for k in v}) == n, "duplicate record_id in the map"


if __name__ == "__main__":
    fs = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in fs:
        f(); print(f"  ok  {f.__name__}")
    print(f"\n{len(fs)} tests passed")
