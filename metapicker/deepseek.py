#!/usr/bin/env python3
"""
DeepSeek through the chat window: generates batches ready to paste, and imports the reply.

WHY IT IS SLICED. No review fits in a single paste — the smallest (Hanlon_2022, 206
records) is 40k tokens. 100 records per batch lands around 20k, comfortable for the chat
window, and the reply is 100 short lines.

THE PROMPT CARRIES THE ASYMMETRIC RULE: excluding requires evidence, including does not.
It is the same rule the JEV facets use and the one `choice` uses. Without it DeepSeek is
playing a different game — judging final eligibility rather than screening — and the
three-way comparison no longer measures the same thing.

VALIDATION ON IMPORT IS WHAT PROTECTS THE BENCHMARK. The likely failure here is not a
loud error, it is a truncated reply that looks complete: the model answers 60 of 100
lines, the file looks legitimate, and 40 records go missing unnoticed. So nothing is
imported partially — either the whole batch is valid or it is refused with the reason.

    ./deepseek.py generate
    ./deepseek.py import-review --file results/hanlon.txt --review Hanlon_2022
    ./deepseek.py status
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from metapicker.sources import synergy  # noqa: E402

OUT = ROOT / "results" / "deepseek"
DB = ROOT / "results" / "answers.sqlite"

# The three reviews of the comparison set, chosen by CONTRAST in JEV's performance: best
# case, second best, second worst. If all three models agree on Theobald and diverge on
# Deckers, the difficulty belongs to the review; if one of them rescues Deckers, it is a
# difference between models.
REVIEWS = ["Theobald_2021", "Hanlon_2022", "Deckers_2022"]
PER_BATCH = 100

HEADER = """\
# Systematic review screening — {rev}, batch {n} of {tot}

You are a reviewer performing **title and abstract screening** for the systematic review
below. For each record, decide whether it should be **retrieved for full-text
assessment**.

This is not the final eligibility decision. The question is: is it worth pulling the full
article to assess it?

**Screening rule — important:** excluding requires evidence, including does not. If the
title and abstract do not carry enough information to safely discard the record, the
verdict is **YES**. Many records have no abstract; absence of information is not grounds
for exclusion.

---

## Review

{title}

## Eligibility criteria

{criteria}

---

## Records ({count})

"""

FOOTER = """
---

## Reply format

Reply with **nothing but** one line per record, in exactly this format:

```
1: YES
2: NO
3: YES
```

Rules for the reply:
- exactly **{count} lines**, numbered **1 to {count}**, in order;
- only `YES` or `NO`;
- **no** text before, after, or between the lines — no explanation, no summary.
"""


def _records_of_review(review_id: str):
    records, reviews = synergy.load([review_id])
    return records, vars(reviews[0])


def generate(reviews: list[str], per_batch: int) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    n_batches = n_records = 0
    for review_id in reviews:
        records, review = _records_of_review(review_id)
        batches = [records[i:i + per_batch] for i in range(0, len(records), per_batch)]
        for n, block in enumerate(batches, 1):
            body = [HEADER.format(
                rev=review_id, n=n, tot=len(batches),
                title=review["title"] or review_id,
                criteria=review["raw_criteria"] or "(not reported in the source)",
                count=len(block))]
            for i, r in enumerate(block, 1):
                a = (r.abstract or "").strip() or "(no abstract)"
                body.append(f"**[{i}]** {r.title or '(no title)'}\n\n{a}\n")
            body.append(FOOTER.format(count=len(block)))

            base = OUT / f"{review_id}-batch-{n}"
            base.with_suffix(".md").write_text("\n".join(body), encoding="utf-8")
            base.with_suffix(".json").write_text(json.dumps({
                "review_id": review_id, "batch": n, "of": len(batches),
                "map": {str(i): r.record_id for i, r in enumerate(block, 1)},
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            tok = len(base.with_suffix(".md").read_text()) // 4
            print(f"  {base.name}.md   {len(block):3} records · ~{tok:,} tokens")
            n_batches += 1; n_records += len(block)
    print(f"\n  {n_batches} batches · {n_records:,} records · in {OUT}")
    print(f"  answer each one, saving the reply to <name>.reply.txt")


# Accepts YES/NO and the Portuguese SIM/NAO the original experiment used.
LINE = re.compile(r"^\s*\[?(\d+)\]?\s*[:.\)\-]\s*(YES|NO|SIM|NAO|N\u00c3O|S|N)\s*$",
                  re.IGNORECASE)
NEGATIVE = ("N",)


def _verdict(token: str) -> int:
    return 0 if token.upper().rstrip("\u00c3").startswith(NEGATIVE) else 1


def parse(text: str, expected: int) -> dict[int, int]:
    """
    Turns a pasted reply into {number: 0|1}. RAISES on any inconsistency — the failure
    that matters here is silent, so nothing is imported partially.
    """
    seen: dict[int, int] = {}
    bad: list[str] = []
    for line in text.splitlines():
        if not line.strip() or line.strip().startswith("```"):
            continue
        m = LINE.match(line)
        if not m:
            bad.append(line.strip()[:60]); continue
        num = int(m.group(1))
        if num in seen:
            raise ValueError(f"number {num} appears twice")
        if not 1 <= num <= expected:
            raise ValueError(f"number {num} outside the range 1-{expected}")
        seen[num] = _verdict(m.group(2))
    if bad:
        raise ValueError(f"{len(bad)} unrecognised line(s), e.g.: {bad[:3]}")
    missing = sorted(set(range(1, expected + 1)) - set(seen))
    if missing:
        raise ValueError(f"missing {len(missing)} of {expected} "
                         f"(first: {missing[:8]}) — reply probably truncated")
    return seen


def import_batch(name: str) -> int:
    base = OUT / name
    meta_f, reply_f = base.with_suffix(".json"), Path(f"{base}.reply.txt")
    if not meta_f.exists():
        print(f"no {meta_f.name} — run `generate` first", file=sys.stderr); return 1
    if not reply_f.exists():
        print(f"no {reply_f.name}", file=sys.stderr); return 1
    meta = json.loads(meta_f.read_text())
    mapping = meta["map"]
    try:
        verdicts = parse(reply_f.read_text(encoding="utf-8"), len(mapping))
    except ValueError as e:
        print(f"REFUSED — {e}\n  nothing was imported.", file=sys.stderr); return 2

    con = sqlite3.connect(DB)
    rows = [("synergy", meta["review_id"], mapping[str(k)], "deepseek", "enters",
             float(v)) for k, v in sorted(verdicts.items())]
    con.executemany("INSERT OR REPLACE INTO answers VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    yes = sum(verdicts.values())
    print(f"  {name}: {len(rows)} imported · {yes} YES ({yes/len(rows):.0%}) · "
          f"{len(rows)-yes} NO")
    return 0


def import_review(path: Path, review_id: str) -> int:
    """
    Imports the reply for a WHOLE review pasted into one file.

    Numbering RESTARTS with each batch — the chat answers 1..100, then 1..100 again — and
    the file may carry model prose before the answers. So: extract the verdict lines in
    order, split into runs where the number returns to 1, and require the runs to match
    the generated batch sizes ONE BY ONE.

    Without that requirement, one batch too many or too few would shift every subsequent
    record_id, and the benchmark would come out silently wrong.
    """
    batches = sorted(OUT.glob(f"{review_id}-batch-*.json"),
                     key=lambda x: int(x.stem.rsplit("-", 1)[-1]))
    if not batches:
        print(f"no batches for {review_id} — run `generate`", file=sys.stderr); return 1
    maps = [json.loads(x.read_text())["map"] for x in batches]

    pairs, junk = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m:
            pairs.append((int(m.group(1)), m.group(2)))
        elif line.strip() and not line.strip().startswith("```"):
            junk += 1

    runs, current = [], []
    for n, v in pairs:
        if n == 1 and current:
            runs.append(current); current = []
        current.append((n, v))
    if current:
        runs.append(current)

    expected = [len(m) for m in maps]
    got = [len(r) for r in runs]
    if got != expected:
        print(f"REFUSED — {review_id}: runs {got}, expected batches {expected}.\n"
              f"  nothing was imported.", file=sys.stderr); return 2
    for i, r in enumerate(runs, 1):
        if [n for n, _ in r] != list(range(1, len(r) + 1)):
            print(f"REFUSED — {review_id} batch {i}: numbering is not 1..{len(r)}.\n"
                  f"  nothing was imported.", file=sys.stderr); return 2

    con = sqlite3.connect(DB)
    rows, yes = [], 0
    for mapping, run in zip(maps, runs):
        for n, v in run:
            value = float(_verdict(v))
            yes += int(value)
            rows.append(("synergy", review_id, mapping[str(n)], "deepseek", "enters",
                         value))
    if len({r[2] for r in rows}) != len(rows):
        print(f"REFUSED — {review_id}: duplicate record_id.", file=sys.stderr); return 2
    con.executemany("INSERT OR REPLACE INTO answers VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    print(f"  {review_id}: {len(rows)} imported across {len(runs)} batches · "
          f"{yes} YES ({yes/len(rows):.0%}) · {len(rows)-yes} NO"
          + (f" · {junk} prose line(s) ignored" if junk else ""))
    return 0


def status() -> None:
    con = sqlite3.connect(DB)
    stored = dict(con.execute(
        "SELECT review_id, COUNT(*) FROM answers WHERE variant='deepseek' "
        "GROUP BY review_id"))
    print(f"{'batch':28} {'records':>9} {'reply':>8}")
    for f in sorted(OUT.glob("*.json")):
        m = json.loads(f.read_text())
        reply = Path(str(f)[:-5] + ".reply.txt")
        print(f"{f.stem:28} {len(m.get('map') or m.get('mapa', {})):9} "
              f"{'yes' if reply.exists() else 'MISSING':>8}")
    print()
    for rev, n in sorted(stored.items()):
        print(f"  {rev}: {n} records in the database")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["generate", "import", "import-review", "status"])
    ap.add_argument("--batch", help="batch name, e.g. Hanlon_2022-batch-1")
    ap.add_argument("--file", type=Path, help="whole-review reply file")
    ap.add_argument("--review", help="review id, for import-review")
    ap.add_argument("--reviews", nargs="*", default=REVIEWS)
    ap.add_argument("--per-batch", type=int, default=PER_BATCH)
    a = ap.parse_args()
    if a.action == "generate":
        generate(a.reviews, a.per_batch); return 0
    if a.action == "status":
        status(); return 0
    if a.action == "import-review":
        if not (a.file and a.review):
            print("--file and --review are required", file=sys.stderr); return 1
        return import_review(a.file, a.review)
    if not a.batch:
        print("--batch is required", file=sys.stderr); return 1
    return import_batch(a.batch)


if __name__ == "__main__":
    sys.exit(main())
