# Metapicker

A benchmark of **JEV** (TypeSafe, a *System One model*), **DeepSeek V4 Flash** and a local
**Qwen3-Reranker** on **systematic review screening**: given a review's eligibility
criteria and the complete set of records its authors retrieved, can a model separate what
is worth reading from what is not?

The requirement that shapes the whole design: evaluation runs over **the complete set the
authors screened**, not over the studies they included. Without the documented exclusions
there is no specificity to measure.

## Result

257 records with an abstract, across 3 reviews. 66 of them (26%) were kept by the authors
for full-text reading.

| method | kept | % of list | correct among kept | correct among discarded | of the 66, kept | AUC | cost |
|---|---|---|---|---|---|---|---|
| **JEV `choice`, batched** | 115 | 45% | **45.2%** | 90.1% | 52 | **0.729** | **US$ 0.0086** |
| DeepSeek V4 Flash | **101** | 39% | **45.5%** | 87.2% | 46 | 0.705 | US$ 0.0315 |
| Qwen3-Reranker 8B (local) | 177 | 69% | 36.7% | **98.8%** | **65** | 0.699 | **US$ 0** |
| Qwen3-Reranker 0.6B (local) | 222 | 86% | 29.7% | 100.0% | 66 | 0.592 | US$ 0 |
| *random half* | *128* | *50%* | *25.7%* | *74.3%* | *33* | *0.500* | — |
| *(the authors)* | *66* | *26%* | *100%* | *100%* | *66* | — | — |

**The random row is the one that recalibrates the others.** Cutting half the list at random
already scores 74.3% "correct among discarded", because three quarters of the records
deserve discarding anyway. JEV's 90.1% is +16 points over chance, not 90 points of merit.

The column chance cannot explain is **how many of the 66 survived**: 52, 46, 65 and 66
against 33 for the coin flip. Removing half at random leaves ~33 of the good articles, and
the probability that all 66 survive is ~10⁻³⁴.

**The 8B reranker is the safest of all at not losing a study** — it drops 1 of 66 while
cutting 31% of the list, free, on a local GPU. With continuous scores its ranking AUC is
**0.896**, above both flagship models. It ranks well and decides poorly: to avoid losing
studies its threshold forces it to keep 177 of 257.

The AUC column is for binary decisions — it equals (sensitivity + specificity)/2 and does
not measure ranking. It is the only form comparable across all four, since DeepSeek
produces nothing but yes/no.

For scale: in `Hanlon_2022` the agreement between the **two human reviewers** was
**kappa 98%**. The distance to human performance is not a matter of fine tuning.

## Install

```bash
pip install httpx synergy-dataset
python -m synergy_dataset get -l        # downloads SYNERGY+ v3.0 to ~/.synergy_dataset_source
python metapicker/sources/synergy.py build
```

The JEV key goes in `api_key_jev.txt` (already in `.gitignore`) or in `TYPESAFE_API_KEY`.

## Use

```bash
python tests/test_jev.py && python tests/test_metrics.py     # none touch the network
python tests/test_run.py && python tests/test_deepseek.py

python metapicker/sources/synergy.py catalogue   # what exists and what each costs
python metapicker/run.py --reviews X Y Z --budget 0.10
python metapicker/run_batch.py --per-call 100    # 5x cheaper, same accuracy
python metapicker/evaluate.py --target ta --facets
python metapicker/threshold.py --target ta --quantile 0.10
python metapicker/baseline.py --target ta        # BM25, CPU

bash metapicker/serve_reranker.sh                # local reranker, GPU
python metapicker/run_qwen.py --variant qwen8b

python metapicker/deepseek.py generate           # paste files for the chat window
python metapicker/deepseek.py import-review --file results/hanlon.txt --review Hanlon_2022

python metapicker/compare.py --target ta --with-abstract-only
```

`run.py` is resumable: rerunning continues where it stopped, and `--budget` caps the run
in dollars.

## What this measures, and what it does not

**It measures** title/abstract screening: given the criteria and a title+abstract pair,
should this record go to full-text reading? That is the decision that discards ~98% of a
review's records, and where an error costs a lost study.

**It does not measure** final eligibility. That depends on the full text, which the
authors only read for the few records that passed screening. The primary target is
therefore `label_abstract_included`, not `label_included`; the gap between the two is
reported separately.

## Three things the design does on purpose

**Six nouls in one request.** The `state` is billed once, so the five PI/ECO facets cost
~36% more and deliver what JEV cannot say: *why* it excluded. JEV generates no text —
without the facets, error analysis does not exist.

**The question is "is it worth retrieving the full text?"**, not "does it meet the
criteria". The facets are asymmetric: when in doubt, true. Excluding requires evidence;
including does not.

**Raw probabilities in SQLite, never booleans.** `noul` returns 0–1. Every threshold and
every metric comes out of that without spending again.

## Declared limitations

- **The set is not what the authors screened; it is the slice that matched in OpenAlex** —
  32% in `Theobald_2021`, 56% in `Hanlon_2022`, 82% in `Deckers_2022`. And the loss is
  biased: the pass rate diverges from the published one in opposite directions (Theobald
  52% against 36% published; Hanlon 18% against 25%).
- **67% of the corpus has no abstract in OpenAlex**, and the human reviewer almost
  certainly had one. Every headline metric is reported on the with-abstract slice only.
- `Theobald_2021` screened on "title, abstract **and keywords**" — a field no model
  received.
- DeepSeek's cost was measured through the chat window, with no way to know whether input
  caching applied; the real range runs from ~US$ 0.016 (cached) to ~US$ 0.05 (full history
  uncached). Thinking tokens were **49,367**, 8.1× the visible output and 44% of its bill.
- The prompt files archived in `results/deepseek/` are the **Portuguese originals** used
  in the experiment. The generator now emits English; the archived files are kept as the
  record of what was actually sent.

## Reproducing the reranker

Most community GGUF conversions of Qwen3-Reranker are broken with llama.cpp: they are
plain text-generation conversions, missing the `cls.output.weight` tensor along with
`qwen3.classifier.output_labels` and `qwen3.pooling_type`. They return meaningless ~1e-22
scores in no particular order. Six repositories were checked; only
[Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp](https://huggingface.co/Voodisss/Qwen3-Reranker-8B-GGUF-llama_cpp)
was converted with the official `convert_hf_to_gguf.py`. The server also needs
`--pooling rank --embedding` alongside `--reranking`. See
[llama.cpp issue #16407](https://github.com/ggml-org/llama.cpp/issues/16407).

## Data and attribution

Records come from **SYNERGY+ v3.0** (ASReview), CC-BY 4.0 —
[repository](https://github.com/asreview/synergy-dataset) ·
[dataverse.nl doi:10.34894/DDCVCV](https://doi.org/10.34894/DDCVCV). Titles and abstracts
are OpenAlex objects and retain their upstream terms. The files in `results/deepseek/`
reproduce those fields so the experiment can be replicated.

The three reviews used as ground truth:

- Theobald, M. (2021). *Self-regulated learning training programs enhance university
  students' academic performance, self-regulated learning strategies, and motivation: A
  meta-analysis.* Contemporary Educational Psychology, 66, 101976.
  [10.1016/j.cedpsych.2021.101976](https://doi.org/10.1016/j.cedpsych.2021.101976)
- Hanlon, P., et al. (2022). *Frailty in people with rheumatoid arthritis: a systematic
  review of observational studies.* Wellcome Open Research, 6, 244.
  [10.12688/wellcomeopenres.17208.2](https://doi.org/10.12688/wellcomeopenres.17208.2)
- Deckers, R., & Lago, P. (2022). *Systematic literature review of domain-oriented
  specification techniques.* Journal of Systems and Software, 192, 111415.
  [10.1016/j.jss.2022.111415](https://doi.org/10.1016/j.jss.2022.111415)

All three describe title/abstract screening as a stage separate from full-text
assessment; the quotes are in `FINDINGS.md`.

Models evaluated: **Jev 1.13** (TypeSafe) · **DeepSeek V4 Flash** ·
**Qwen3-Reranker 0.6B and 8B**.

## Where things are

`FINDINGS.md` — what was measured, what was refuted, and the cost ledger.
`results/comparison.md` — the full per-review tables.
