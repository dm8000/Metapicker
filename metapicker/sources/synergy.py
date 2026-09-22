#!/usr/bin/env python3
"""
SYNERGY+ v3.0 — the COMPLETE set of records each review's authors retrieved and screened,
each with a binary label. It is the only dataset here that IS literally each review's
screening set rather than a reconstructed pool.

TWO LABELS, and the distinction decides what is being measured:

  label_abstract_included -> passed title/abstract screening   PRIMARY TARGET: this is the
                             decision the model is actually making
  label_included          -> entered the synthesis after full text   secondary target

Charging a model against the second penalises it for exclusions only the full text
reveals. The gap between the two measures exactly that discount.

WHY THIS MODULE DOES NOT USE `python -m synergy_dataset get`. The official builder drops
records with no abstract in OpenAlex. Measured on two reviews: it would keep 35-48% of
records — and, worse, only 30-51% of the INCLUDED ones, which are the ground truth. That
would break the central requirement of running over the complete screened set, and would
inflate prevalence. The `works_*.zip` archives carry 100% of records and 100% of titles;
it is the abstract that is missing for ~60%. A title-only record is still a record the
authors screened, so it stays — flagged, with every metric also reported on the
with-abstract slice.

    ./synergy.py build      # labels.csv + works_*.zip -> data/synergy/*.jsonl
    ./synergy.py check
    ./synergy.py catalogue
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

csv.field_size_limit(10 ** 7)

ROOT = Path(__file__).resolve().parents[2]
BUILT = Path(os.environ.get("METAPICKER_SYNERGY", ROOT / "data" / "synergy"))
SOURCE = Path("~/.synergy_dataset_source/synergy-dataset-plus").expanduser()


@dataclass
class Record:
    source: str
    review_id: str
    record_id: str
    title: str
    abstract: str
    year: str
    doi: str
    pmid: str
    label_ta: int | None
    label_final: int


@dataclass
class Review:
    source: str
    review_id: str
    title: str
    question: str
    raw_criteria: str
    criteria: dict
    doi: str
    published_n: int
    published_included: int
    has_ta_label: bool


def _label(v) -> int | None:
    v = str(v or "").strip()
    if v in ("0", "0.0", "False"):
        return 0
    if v in ("1", "1.0", "True"):
        return 1
    return None


def _meta(name: str) -> dict:
    f = SOURCE / name / "metadata.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _review_title(name: str) -> str:
    # Empty, not the dataset key itself: "Cinquin 2018" in the `state` is noise — the
    # model would read a filename as if it were the review's question.
    f = SOURCE / name / "metadata_publication.json"
    if not f.exists():
        return ""
    try:
        return json.loads(f.read_text()).get("title") or ""
    except Exception:
        return ""


def _inverted_to_text(inv: dict | None) -> str:
    """OpenAlex stores the abstract as an inverted index {word: [positions]}."""
    if not inv:
        return ""
    pos = [(i, w) for w, idxs in inv.items() for i in idxs]
    return " ".join(w for _, w in sorted(pos))


def build(only: list[str] | None = None) -> None:
    """labels.csv + works_*.zip -> one JSONL per review, with only what the models use."""
    import zipfile
    BUILT.mkdir(parents=True, exist_ok=True)
    names = sorted(d.name for d in SOURCE.iterdir() if d.is_dir())
    if only:
        names = [n for n in names if n in set(only)]
    for i, name in enumerate(names, 1):
        d = SOURCE / name
        zips = sorted(d.glob("works_*.zip"))
        if not (d / "labels.csv").exists() or not zips:
            continue
        out = BUILT / f"{name}.jsonl"
        if out.exists() and out.stat().st_size > 0:
            continue
        with (d / "labels.csv").open(newline="", encoding="utf-8") as fh:
            labels = list(csv.DictReader(fh))
        by_id = {(x.get("openalex_id") or "").rsplit("/", 1)[-1].lower(): x
                 for x in labels if x.get("openalex_id")}
        works: dict[str, dict] = {}
        for z in zips:
            zf = zipfile.ZipFile(z)
            for n in zf.namelist():
                try:
                    block = json.loads(zf.read(n))
                except Exception:
                    continue
                for w in (block if isinstance(block, list) else [block]):
                    works[(w.get("id") or "").rsplit("/", 1)[-1].lower()] = w
        rows = []
        for k, lab in by_id.items():
            w = works.get(k)
            if w is None:
                continue
            inv = (w.get("abstract_inverted_index_cleaned")
                   or w.get("abstract_inverted_index"))
            rows.append({
                "record_id": k,
                "title": (w.get("title") or w.get("display_name") or "").strip(),
                "abstract": _inverted_to_text(inv).strip(),
                "year": str(w.get("publication_year") or ""),
                "doi": (lab.get("doi") or w.get("doi") or "").strip(),
                "pmid": (lab.get("pmid") or "").strip(),
                "label_ta": _label(lab.get("label_abstract_included")),
                "label_final": _label(lab.get("label_included")),
            })
        with out.open("w", encoding="utf-8") as fh:
            for r in rows:
                print(json.dumps(r, ensure_ascii=False), file=fh)
        print(f"  [{i}/{len(names)}] {name}: {len(rows):,} records "
              f"({sum(1 for r in rows if r['abstract'])/max(1,len(rows)):.0%} "
              f"with abstract)", file=sys.stderr)


def load(only: list[str] | None = None) -> tuple[list[Record], list[Review]]:
    if not BUILT.exists() or not any(BUILT.glob("*.jsonl")):
        raise SystemExit(f"{BUILT} is empty — run first:  "
                         f"./metapicker/sources/synergy.py build")
    records: list[Record] = []
    reviews: list[Review] = []
    for f in sorted(BUILT.glob("*.jsonl")):
        name = f.stem
        if only and name not in only:
            continue
        # Iterate the file, not splitlines(): splitlines() also breaks on U+2028/U+2029,
        # which appear raw inside OpenAlex titles.
        with f.open(encoding="utf-8") as fh:
            rows = [json.loads(l) for l in fh if l.strip()]
        if not rows:
            continue
        m = _meta(name)
        pub = m.get("publication") or {}
        data = m.get("data") or {}
        reviews.append(Review(
            source="synergy", review_id=name, title=_review_title(name),
            question=_review_title(name),
            raw_criteria=(pub.get("eligibility_criteria") or "").strip(),
            criteria={}, doi=str(pub.get("doi") or ""),
            published_n=int(data.get("n_records") or 0),
            published_included=int(data.get("n_records_included") or 0),
            has_ta_label=any(x.get("label_ta") is not None for x in rows)))
        for x in rows:
            if x.get("label_final") is None:
                continue
            records.append(Record(
                source="synergy", review_id=name, record_id=x["record_id"],
                title=x["title"], abstract=x["abstract"], year=x["year"],
                doi=x["doi"], pmid=x["pmid"],
                label_ta=x.get("label_ta"), label_final=x["label_final"]))
    return records, reviews


def check() -> int:
    records, reviews = load()
    n, inc = len(records), sum(r.label_final for r in records)
    ta = sum(1 for r in records if r.label_ta == 1)
    print(f"reviews           {len(reviews)}   "
          f"({sum(1 for v in reviews if v.has_ta_label)} with a T/A label, "
          f"{sum(1 for v in reviews if v.raw_criteria)} with criteria)")
    print(f"records           {n:,}")
    print(f"included (final)  {inc:,}   prevalence {inc/n:.2%}")
    print(f"passed T/A        {ta:,}")
    print(f"no abstract       {sum(1 for r in records if not r.abstract):,}")

    # The load has to match the n published in each review's metadata.json.
    bad, per = [], {}
    for r in records:
        per[r.review_id] = per.get(r.review_id, 0) + 1
    for v in reviews:
        if v.published_n and abs(per.get(v.review_id, 0) - v.published_n) > \
                max(5, v.published_n * 0.02):
            bad.append((v.review_id, per.get(v.review_id, 0), v.published_n))
    if bad:
        print(f"\n{len(bad)} reviews diverge from the published n (5 largest):")
        for k, a, b in sorted(bad, key=lambda x: -abs(x[1] - x[2]))[:5]:
            print(f"   {k:32} loaded {a:6,} · published {b:6,}")
    print("\nload sanity: OK" if n and inc else "\nload sanity: FAILED")
    return 0 if n and inc else 1


def catalogue() -> None:
    """Per-review table, for choosing what fits the budget."""
    records, reviews = load()
    per: dict[str, list[Record]] = {}
    for r in records:
        per.setdefault(r.review_id, []).append(r)
    print(f"{'review':34} {'n':>7} {'pos':>5} {'prev':>7} {'T/A':>6} {'no abs':>7} "
          f"{'crit':>5} {'US$':>7}")
    total = 0.0
    for v in sorted(reviews, key=lambda x: len(per.get(x.review_id, []))):
        g = per.get(v.review_id, [])
        if not g:
            continue
        inc = sum(r.label_final for r in g)
        ta = sum(1 for r in g if r.label_ta == 1)
        no_abs = sum(1 for r in g if not r.abstract)
        cost = len(g) * 1022 / 1e6 * 0.042      # 1,022 tok/record, measured
        total += cost
        print(f"{v.review_id[:34]:34} {len(g):7,} {inc:5} {inc/len(g):6.2%} "
              f"{(ta if v.has_ta_label else 0):6} {no_abs/len(g):6.1%} "
              f"{'yes' if v.raw_criteria else 'NO':>5} {cost:7.3f}")
    print(f"\n{'TOTAL':34} {len(records):7,} {'':5} {'':7} {'':6} {'':7} {'':5} {total:7.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["build", "check", "catalogue"])
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    if a.action == "build":
        build(a.only or None); return 0
    return check() if a.action == "check" else (catalogue() or 0)


if __name__ == "__main__":
    sys.exit(main())
