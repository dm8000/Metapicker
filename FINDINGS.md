# Metapicker — working notes

What was **measured**, what was **refuted**, and what it cost. It does not repeat what the
code already says. Reference date: 2026-09-22.

## The question

Does JEV work for picking scientific articles? Not in the abstract — against the
documented judgement of real systematic review authors, over **the complete set they
screened**, not just the ones that survived.

## Refuted

**«SYNERGY+ does not exist.»** Wrong, and it was my error. It does: release
`synergy_plus_v3.0`, published 2026-08-27 on dataverse.nl (DOI 10.34894/DDCVCV). It is too
recent to show up in search indexes — secondary sources still describe SYNERGY v1
(26 reviews / 169,288 records). v3 carries datasets from 2024 and 2025.

**The GitHub `index.json` is the v1 snapshot.** It points at a different collection of
datasets (the `Cohen_2006_*` ones). Loading from it gave 80,203 records and 1,781
inclusions against the published 169,288 / 2,834 — the load sanity check caught it before
any money was spent. The right source is the official package, which downloads from
dataverse.nl into `~/.synergy_dataset_source/synergy-dataset-plus/`.

**«You need an LLM to extract the eligibility criteria.»** You do not. They come ready in
`metadata.json` → `publication.eligibility_criteria`, per review.

**«MetaSyn documents each review's hard negatives.»** It does not. It is a shared candidate
pool — `matched_corpus_ids` gives only the matched positives, and title matching covers
51.6% (67.7% on the test split). Reconstructing a per-review screening set from it is not
executable.

**«JEV cannot take full text.»** An impression I gave and had to correct. The `state`
accepts any text; the limit is 32k tokens for `state` plus the longest question, and a
typical biomedical article is 6–12k. The bottleneck was always the dataset, never the
model.

**«One record per call is how JEV works.»** No — that was my design error. The `state`
accepts an array and questions are evaluated in parallel against it, so a question can
address item `n`. Batching 100 records per call cut cost 5× with no loss of accuracy. See
the batching section below.

## Measured

### JEV (docs.typesafe.ai/models, confirmed by live calls)

| | |
|---|---|
| endpoint | `POST https://api.typesafe.ai/v1/systemone`, `jev-latest` → `jev-1.13.0` |
| limits | 64k tokens/request · 32k for `state` + the longest question |
| price | US$ 0.042 per million **input** tokens; output free |
| rate | 1,200 req/min · 250,000 tokens/s |
| **measured cost** | **1,022 tokens/record** with 6 nouls → **US$ 0.043 per thousand** |
| **latency p50** | **0.29 s** per call |

### Smoke test — the facet design proved itself

Three constructed cases against the criteria of a Wilson disease review:

| case | `retrieve` | facets |
|---|---|---|
| RCT on topic | 0.97 | all ≥ 0.97 |
| narrative review, **same topic** | 0.07 | `design`=0.03 · `comparator`=0.11 · but `population`=0.65 |
| off topic | 0.03 | all ≤ 0.11 |

The middle case is the one that matters: the facets say **why** it excluded — wrong
design, not wrong topic. JEV generates no text, so without the facets that information
does not exist.

## Design decisions, with the reason

**The question is «is it worth retrieving the full text?», not «does it meet the
criteria».** At the title/abstract stage a reviewer is not deciding eligibility — they are
deciding whether to retrieve the article. Asking the second one penalises JEV for failing
to guess what only the full text reveals.

**The facets are asymmetric: when in doubt, true.** That is the rule of real screening —
excluding requires evidence, including does not. A symmetric facet would turn "the abstract
does not say" into an exclusion, which is the error that loses a study.

**Raw probabilities are stored, never booleans.** Every threshold, metric and re-analysis
comes out of SQLite without spending again.

**Fewer reviews, each complete** — rather than sampling records inside each review. With
limited credit it is the only cut that does not break the central requirement of running
over the complete set the authors screened.

## Machine constraints (2026-09-21/22)

RTX 3090. Disk started at 99% (9 GB free); clearing `~/.cache/uv` (20 GB) and
`~/.cache/pip` (8.4 GB) freed 30 GB for the 8B reranker.

## Cost ledger

| stage | calls | tokens | cost |
|---|---|---|---|
| smoke test | 3 | 2,510 | US$ 0.0001 |
| pilot (3 complete reviews) | 2,633 | 2,691,336 | US$ 0.1130 |
| main run (18 reviews) | 12,478 | 13,334,454 | US$ 0.5600 |
| `choice`, one at a time (3 reviews) | 1,051 | 1,029,616 | US$ 0.0432 |
| `choice`, batched (3 reviews) | 12 | 205,440 | US$ 0.0086 |
| **total** | **16,177** | **17,263,356** | **US$ 0.725** |

Qwen 0.6B: 15,111 pairs in 9.2 min on GPU, free. Qwen 8B: 15,111 pairs in ~58 min, free.
DeepSeek: 169,324 tokens through the chat window, ~US$ 0.0315 at V4 Flash rates.

## Main run — 21 complete reviews

15,111 records, each review entire. Prevalence 0.62% to 14.2%; 37% to 80% without an
abstract. Full report in `results/comparison.md`.

### Ranking — target: included in synthesis

| predictor | AUC | WSS@95 | R@10% | R@20% |
|---|---|---|---|---|
| BM25 (baseline) | 0.651 | 15.1% | 22% | 38% |
| JEV — holistic `retrieve` noul | 0.905 | 59.3% | 60% | 79% |
| JEV — facets (minimum) | 0.907 | 58.3% | 61% | 81% |
| **JEV — facets (product)** | **0.920** | **61.1%** | **65%** | **83%** |

### The pilot's n=3 misled about which aggregate wins

In the pilot, facet-minimum won (0.857 vs 0.854 for the product). With 21 reviews the
**product** wins (0.920 vs 0.907), and the minimum loses even to the holistic on WSS. The
ordering among the three predictors was not stable at n=3 — recorded because it is exactly
the kind of conclusion the pilot could not support and that would have become a finding
had it not been redone.

### The threshold rule got worse with more data — fixed

`fitted_threshold` aggregated by MINIMUM across training reviews. With 3 reviews the
leakage was 3.7 pp; with 21 it became **40.5 pp of specificity**, because the minimum is
pinned by the most extreme review and grows more conservative as reviews are added. A rule
that gets worse with data. Replaced by a parameterised quantile:

| aggregation | sensitivity (out-of-fold) | specificity | leakage |
|---|---|---|---|
| minimum (quantile 0) | 99.0% | 27.1% | 40.5 pp |
| **10th percentile** | **96.6%** | **51.1%** | **16.5 pp** |
| 25th percentile | 94.5% | 60.2% | 7.4 pp |

## Overlap with the authors, and why raw agreement misleads

AUC, WSS@95 and R@k measure **ranking**. None of them involves a threshold, so none of
them says whether the model *decides* like the reviewer. Computed out-of-fold over the
same 21 reviews:

| | recall threshold (0.14) | agreement threshold (0.82) |
|---|---|---|
| raw agreement | 55.3% | 92.2% |
| **Cohen's kappa** | **0.106** (slight) | **0.410** (moderate) |
| sensitivity | 96.6% | 53.2% |
| specificity | 51.1% | 95.1% |

The 0.82 threshold came out identical across all 21 folds — stable, not fitted per review.
**The best achievable kappa is 0.410.** Agreement between two human reviewers in screening
usually runs 0.5–0.8: as an autonomous decider JEV falls below a second human reviewer.

## FRAMING CORRECTION: the fair target is T/A screening

The authors had exactly one moment where they held only title and abstract — the T/A
screening stage. That is the decision JEV should be charged against. Measuring against
`label_included` penalises it for exclusions that only surfaced on full-text reading.

| target | precision (macro) | top-k | precision **with abstract** |
|---|---|---|---|
| included in synthesis | 46.1% | 47.5% | 58.2% |
| **passed T/A screening** | **52.6%** | **54.7%** | **69.3%** |

**The fair number is 69.3%**: precision when both sides had the same information — a
record with an abstract, against the decision the authors made seeing only title and
abstract. At ~50% recall.

### Ranking and deciding do not move together

AUC is HIGHER on the final target (0.920) than on T/A (0.824), while precision is higher on
T/A. Not a contradiction: T/A has 3,029 positives against 902, many of them borderline, so
ranking the whole list gets harder at the same time the binary decision gets easier.
Reporting only one of the two metric families gives the wrong impression about the other.

## Verified against the source papers

All three describe title/abstract screening as a stage distinct from full text:

**Hanlon 2022** — *"Two independent reviewers (PH and HM) screened all titles and
abstracts"* … *"assessed full texts of all relevant articles for eligibility"* …
*"Databases searches identified 367 titles and abstracts, after removal of duplicates, of
which 91 were retained for full-text screening. From these, 17 eligible full texts were
identified"*. It also reports the agreement between the two human reviewers: **kappa 98%**.

**Theobald 2021** — *"1,095 articles were screened for eligibility based on title,
abstract, and keywords. Of those, 669 articles were rejected… The remaining 396 studies
entered a detailed evaluation… Hence, 49 studies"*.

**Deckers 2022** — *"Then we applied the criteria in three exclusion stages: based on the
title, based on the abstract, and based on the full text. This resulted in the inclusion
of 20 primary studies."*

An internal check agrees: across the 21 reviews, **all 902 final inclusions passed T/A,
with zero violations** — consistent with a stage that precedes full-text reading.

### But the reconciliation exposes three problems

| | paper: screened → full text → included | ours: records → T/A=1 → final=1 | coverage |
|---|---|---|---|
| Hanlon | 367 → 91 → 17 | 206 → 38 → 16 | **56%** |
| Theobald | 1,095 → 396 → 49 | 351 → 184 → 35 | **32%** |
| Deckers | 602 → — → 20 | 494 → 34 → 16 | **82%** |

1. **We see a third to a half of what the authors screened.** SYNERGY only keeps records
   that matched in OpenAlex. The "complete screened set" is the slice that matched.
2. **The pass rate does not reconcile, and diverges in opposite directions.** If the loss
   were random the rates would match. They do not, so **the loss is biased** in a way that
   cannot be quantified without the original data.
3. **Theobald screened on "title, abstract and keywords"** — a third field no model
   received, on top of the 74% with no abstract and the publication-type tag a reviewer
   sees in their reference manager.

## The official SYNERGY builder drops most of the ground truth

It discards records with no abstract in OpenAlex. Measured on two reviews: it would keep
35–48% of records and **only 30–51% of the INCLUDED ones**. The `works_*.zip` archives
carry 100% of records and 100% of titles, so `sources/synergy.py` builds from those
instead. The price is that **67% of the corpus is title-only**, declared and sliced out of
every headline metric.

## Batching: 88% of JEV's bill was repeated header

Decomposed over the same 1,051 records, with the same tokenizer:

| component | DeepSeek | JEV `choice` |
|---|---|---|
| the records themselves | 99,724 | 99,724 |
| eligibility criteria | 5,125 | **460,628** |
| instructions / structure | 8,996 | ~469,000 |
| **total** | **113,845** | **1,029,616** |

The content that matters is identical. DeepSeek pays the header 88× fewer times because it
receives 100 records per paste. Batching JEV the same way cut it from 979 to **195 tokens
per record** — 12 calls, 6 seconds, US$ 0.0086.

Two caveats, both real:

- **Batched and one-at-a-time agree on only 79.6%** of the 1,051 records, and the
  difference has direction: one-at-a-time says YES to 67%, batched to 51%. Seeing 100
  records side by side makes JEV more selective. It is not the same classifier; it scores
  the same by a different route.
- Comparing token counts across providers means comparing **different rulers**: the
  TypeSafe API billed 1,029,616 for content the Qwen3 tokenizer counts as 718,483 — a
  factor of **1.43×**.

## Native `choice` beats the thresholded noul

`choice` returns the selected option, so no threshold is picked by us.

| review | noul→threshold says YES | `choice` says YES | agree | kappa |
|---|---|---|---|---|
| Theobald_2021 | 91% | 75% | 84.3% | 0.473 |
| Hanlon_2022 | 75% | 36% | 60.7% | 0.311 |
| Deckers_2022 | 96% | 74% | 77.3% | 0.191 |

The threshold was more arbitrary than the method admitted. `choice` picks less and hits
more, in precision, F1 and kappa, on all three reviews.

## The 8B reranker: five of six GGUF builds are broken

The conversion has to extract `cls.output.weight` from `lm_head`; without it llama.cpp
falls back to generic pooling and returns ~1e-22 scores in no meaningful order — in the
smoke test the broken build ranked a narrative review **above** the on-topic RCT. Metadata
injection would not have fixed it, because a tensor is missing, not a key.

Checked and broken: DevQuasar, Mungert, mradermacher, dean2155, greenwich157,
QuantFactory. Working: `Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp`. The server also needs
`--pooling rank --embedding` alongside `--reranking`.

With the correct build, the smoke test separates cleanly:

| case | 8B | 0.6B | JEV |
|---|---|---|---|
| RCT on topic | 0.9442 | 0.9999 | 0.970 |
| **narrative review, same topic** | **0.1700** | 0.9590 | 0.070 |
| off topic | 0.0001 | 0.0003 | 0.030 |

The separation between the RCT and the narrative review goes from **+0.041 on the 0.6B to
+0.774 on the 8B**. The 8B reads criteria; the 0.6B matches topic.

## The random baseline recalibrates two columns

Monte Carlo over 20,000 draws, matched on how many records each method kept:

| method | kept | correct among kept (real vs random) | of the 66, kept |
|---|---|---|---|
| JEV batched | 115 | 45.2% vs 20.0–31.3% | 52 vs 23–36 |
| DeepSeek | 101 | 45.5% vs 18.8–32.7% | 46 vs 19–33 |
| Qwen 8B | 177 | 36.7% vs — | 65 |
| Qwen 0.6B | 222 | 29.7% vs 23.4–27.9% | 66 vs 52–62 |
| random half | 128 | 25.7% | 33 |

All beat chance at p < 0.0001, but by very different margins. **The 0.6B sits on the
edge** — 29.7% against a random ceiling of 27.9%. Keeping 222 of 257, most of its apparent
performance comes from barely removing anything.

And the column that carries no such illusion: removing half at random leaves ~33 of the 66
good articles, with probability ~10⁻³⁴ that all 66 survive.

## Still outstanding

- **US$ 0.07** to redo `Cinquin_2018` and `Tumkaya_2018` with the review title in the
  state. The pilot ran while the SYNERGY+ download was still going, so two of the 21
  reviews were judged without it — their metadata files arrived 1m48s and 7m16s *after*
  the calls. Both sit below the average, so the global number is understated.
- A two-stage pipeline: **8B ranks (AUC 0.896), JEV decides on the top of the list**.
  Nobody has measured it.
- The full-text ablation on MetaSyn, which ships JATS sections directly.
