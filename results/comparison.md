# Model comparison — JEV × Qwen reranker × DeepSeek

Binary yes/no decision against the authors' judgement. Generated 2026-09-22.

## PRIMARY RESULT — normalised to records WITH an abstract

The premise is that the authors screened on title and abstract. But 74% of the
records they approved reach the model **with the title alone** — OpenAlex does not
have those abstracts today, and the reviewer almost certainly had them. Outside this
slice you are not measuring screening, you are measuring who guesses better from a
title.

```
====================================================================================
MODEL COMPARISON — binary decision · target: passed T/A screening
NORMALISED: records WITH an abstract only — where both sides had the same information
====================================================================================
  JEV-noul threshold  0.110  (fitted on 18 outside reviews)
  Qwen threshold      0.0001  (fitted on 18 outside reviews)

  257 records judged by all 4 (3 reviews)

────────────────────────────────────────────────────────────────────────────────────
AGAINST THE GROUND TRUTH
────────────────────────────────────────────────────────────────────────────────────

Theobald_2021   n=70   positives=37 (52.9%)
  model              kept  precision   recall     F1  overlap   kappa
  JEV (choice)         33     84.8%   75.7%  0.800   80.0%   0.601
  JEV (noul→thr)       56     62.5%   94.6%  0.753   67.1%   0.320
  Qwen (rerank)        64     57.8%  100.0%  0.733   61.4%   0.190
  DeepSeek             41     75.6%   83.8%  0.795   77.1%   0.538

Hanlon_2022   n=79   positives=19 (24.1%)
  model              kept  precision   recall     F1  overlap   kappa
  JEV (choice)         14     78.6%   57.9%  0.667   86.1%   0.581
  JEV (noul→thr)       54     35.2%  100.0%  0.521   55.7%   0.256
  Qwen (rerank)        71     26.8%  100.0%  0.422   34.2%   0.069
  DeepSeek              6     83.3%   26.3%  0.400   81.0%   0.322

Deckers_2022   n=108   positives=10 (9.3%)
  model              kept  precision   recall     F1  overlap   kappa
  JEV (choice)         61     16.4%  100.0%  0.282   52.8%   0.146
  JEV (noul→thr)       94     10.6%  100.0%  0.192   22.2%   0.030
  Qwen (rerank)        87     11.5%  100.0%  0.206   28.7%   0.048
  DeepSeek             54     18.5%  100.0%  0.312   59.3%   0.185

                            MACRO-AVERAGE across reviews                            
  model             precision   recall     F1  overlap   kappa
  JEV (choice)         59.9%   77.9%  0.583   73.0%   0.443
  JEV (noul→thr)       36.1%   98.2%  0.489   48.4%   0.202
  Qwen (rerank)        32.0%  100.0%  0.454   41.4%   0.102
  DeepSeek             59.2%   70.0%  0.502   72.5%   0.348

────────────────────────────────────────────────────────────────────────────────────
BETWEEN THE MODELS — do they agree with each other, regardless of being right?
────────────────────────────────────────────────────────────────────────────────────
  JEV (choice)     × JEV (noul→thr)   overlap  62.6%   kappa  0.317
  JEV (choice)     × Qwen (rerank)    overlap  55.6%   kappa  0.205
  JEV (choice)     × DeepSeek         overlap  89.5%   kappa  0.782
  JEV (noul→thr)   × Qwen (rerank)    overlap  81.3%   kappa  0.348
  JEV (noul→thr)   × DeepSeek         overlap  59.1%   kappa  0.274
  Qwen (rerank)    × DeepSeek         overlap  52.1%   kappa  0.172

────────────────────────────────────────────────────────────────────────────────────
WHERE THE DIFFERENCE IS
────────────────────────────────────────────────────────────────────────────────────
  all 4 say YES:    91 records (43 were positives)
  all 4 say NO:     20 records (0 were positives — LOST by everyone)

  positives found by only ONE model (this is where they differ):
    JEV (choice)        0
    JEV (noul→thr)      0
    Qwen (rerank)       2
    DeepSeek            0

  majority vote of 4:  precision 58.0% · recall 80.6% · F1 0.585 · kappa 0.438
```

## Without the normalisation, for contrast — includes the 74% title-only
```
                            MACRO-AVERAGE across reviews                            
  model             precision   recall     F1  overlap   kappa
  JEV (choice)         37.1%   88.7%  0.476   57.9%   0.244
  JEV (noul→thr)       29.6%   99.6%  0.417   38.1%   0.109
  Qwen (rerank)        27.3%   99.5%  0.391   32.2%   0.045
  DeepSeek             55.4%   76.0%  0.553   77.1%   0.408

```

## Secondary target: included in the final synthesis (normalised)
```
                            MACRO-AVERAGE across reviews                            
  model             precision   recall     F1  overlap   kappa
  JEV (choice)         22.6%   94.4%  0.341   66.5%   0.256
  JEV (noul→thr)       17.8%  100.0%  0.290   59.2%   0.190
  Qwen (rerank)        10.5%  100.0%  0.186   35.8%   0.058
  DeepSeek             36.9%   94.4%  0.433   68.2%   0.355

```

## Ranking across all 21 reviews (JEV and Qwen produce scores natively)
```
Hanlon_2022                      206    38 18.45%  0.897   41.1%    45%    66%    66%

MACRO-AVERAGE                     21               0.824   32.4%    35%    55%    79%


MACRO-AVERAGE                     21               0.775   23.3%    29%    49%    86%


MACRO-AVERAGE                     21               0.794   29.0%    32%    51%    83%

```
