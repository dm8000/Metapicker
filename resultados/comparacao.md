# Comparação dos modelos — JEV × Qwen reranker × DeepSeek

Decisão binária sim/não contra o julgamento dos autores. Gerado em 2026-09-21.

## Custo de cada método

| método | escopo | registros | tokens de entrada | por registro | saída | custo |
|---|---|---|---|---|---|---|
| JEV — noul. 6 perguntas | 21 revisões | 15.111 | 16.025.790 | 1.060 | grátis | US$ 0.6731 |
| JEV — `choice` | 3 revisões | 1.051 | 1.029.616 | 979 | grátis | US$ 0.0432 |
| Qwen reranker | 21 revisões | 15111 | 5216307 | 345 | — | US$ 0 (local) |
| Qwen reranker | 3 revisões | 1051 | 581302 | 553 | — | US$ 0 (local) |
| DeepSeek (chat) | 3 revisões | 1051 | 113845 | 108 | 6112 | US$ 0 (chat) |

Os tokens do JEV são medidos pela API; os do Qwen e do DeepSeek foram contados com o
tokenizador do próprio Qwen3 via `/tokenize` — não são estimativas.

## RESULTADO PRINCIPAL — normalizado: só registros COM abstract

A premissa da comparação é que os autores triaram por título e abstract. Mas 74% dos
registros que eles aprovaram chegam ao modelo **só com o título** — o OpenAlex não tem
esses abstracts hoje, e o revisor quase certamente os tinha. Fora desta fatia não se
mede triagem, mede-se quem adivinha melhor a partir de um título.

```
====================================================================================
COMPARAÇÃO DOS MODELOS — decisão binária · alvo: passou triagem T/A
NORMALIZADO: só registros COM abstract — onde os dois lados tinham a mesma informação
====================================================================================
  limiar do JEV-noul  0.110  (ajustado em 18 revisões de fora)
  limiar do Qwen      0.0001  (ajustado em 18 revisões de fora)

  257 registros julgados por todos os 4 (3 revisões)

────────────────────────────────────────────────────────────────────────────────────
CONTRA A VERDADE DE REFERÊNCIA
────────────────────────────────────────────────────────────────────────────────────

Theobald_2021   n=70   positivos=37 (52.9%)
  modelo            escolheu  precisão   recall     F1   acordo   kappa
  JEV (choice)            33    84.8%   75.7%  0.800   80.0%   0.601
  JEV (noul→lim)          56    62.5%   94.6%  0.753   67.1%   0.320
  Qwen (rerank)           64    57.8%  100.0%  0.733   61.4%   0.190
  DeepSeek                41    75.6%   83.8%  0.795   77.1%   0.538

Hanlon_2022   n=79   positivos=19 (24.1%)
  modelo            escolheu  precisão   recall     F1   acordo   kappa
  JEV (choice)            14    78.6%   57.9%  0.667   86.1%   0.581
  JEV (noul→lim)          54    35.2%  100.0%  0.521   55.7%   0.256
  Qwen (rerank)           71    26.8%  100.0%  0.422   34.2%   0.069
  DeepSeek                 6    83.3%   26.3%  0.400   81.0%   0.322

Deckers_2022   n=108   positivos=10 (9.3%)
  modelo            escolheu  precisão   recall     F1   acordo   kappa
  JEV (choice)            61    16.4%  100.0%  0.282   52.8%   0.146
  JEV (noul→lim)          94    10.6%  100.0%  0.192   22.2%   0.030
  Qwen (rerank)           87    11.5%  100.0%  0.206   28.7%   0.048
  DeepSeek                54    18.5%  100.0%  0.312   59.3%   0.185

                           MACRO-MÉDIA entre as revisões                            
  modelo            precisão   recall     F1   acordo   kappa
  JEV (choice)        59.9%   77.9%  0.583   73.0%   0.443
  JEV (noul→lim)      36.1%   98.2%  0.489   48.4%   0.202
  Qwen (rerank)       32.0%  100.0%  0.454   41.4%   0.102
  DeepSeek            59.2%   70.0%  0.502   72.5%   0.348

────────────────────────────────────────────────────────────────────────────────────
ENTRE OS MODELOS — concordam entre si, independentemente de acertar?
────────────────────────────────────────────────────────────────────────────────────
  JEV (choice)     × JEV (noul→lim)   acordo  62.6%   kappa  0.317
  JEV (choice)     × Qwen (rerank)    acordo  55.6%   kappa  0.205
  JEV (choice)     × DeepSeek         acordo  89.5%   kappa  0.782
  JEV (noul→lim)   × Qwen (rerank)    acordo  81.3%   kappa  0.348
  JEV (noul→lim)   × DeepSeek         acordo  59.1%   kappa  0.274
  Qwen (rerank)    × DeepSeek         acordo  52.1%   kappa  0.172

────────────────────────────────────────────────────────────────────────────────────
ONDE ESTÁ A DIFERENÇA
────────────────────────────────────────────────────────────────────────────────────
  os 4 dizem SIM:     91 registros (43 eram positivos)
  os 4 dizem NÃO:     20 registros (0 eram positivos — PERDIDOS por todos)

  positivos que só UM encontrou (é aqui que os modelos diferem):
    JEV (choice)        0
    JEV (noul→lim)      0
    Qwen (rerank)       2
    DeepSeek            0

  voto majoritário dos 4:  precisão 58.0% · recall 80.6% · F1 0.585 · kappa 0.438
```

## Sem a normalização, para contraste — inclui os 74% só-título
```
                           MACRO-MÉDIA entre as revisões                            
  modelo            precisão   recall     F1   acordo   kappa
  JEV (choice)        37.1%   88.7%  0.476   57.9%   0.244
  JEV (noul→lim)      29.6%   99.6%  0.417   38.1%   0.109
  Qwen (rerank)       27.3%   99.5%  0.391   32.2%   0.045
  DeepSeek            55.4%   76.0%  0.553   77.1%   0.408

```

## Alvo secundário: incluído na síntese final (normalizado)
```
                           MACRO-MÉDIA entre as revisões                            
  modelo            precisão   recall     F1   acordo   kappa
  JEV (choice)        22.6%   94.4%  0.341   66.5%   0.256
  JEV (noul→lim)      17.8%  100.0%  0.290   59.2%   0.190
  Qwen (rerank)       10.5%  100.0%  0.186   35.8%   0.058
  DeepSeek            36.9%   94.4%  0.433   68.2%   0.355

```

## Ordenação nas 21 revisões, normalizado (JEV e Qwen produzem score nativamente)
```
Deckers_2022                     108    10 9.26%  0.829   47.8%    30%    50%    47%
Hanlon_2022                       79    19 24.05%  0.934   36.8%    37%    68%    58%
Theobald_2021                     70    37 52.86%  0.832    9.3%    19%    35%    96%

MACRO-MÉDIA                       21               0.858   39.2%    38%    60%    68%

Theobald_2021                     70    37 52.86%  0.853    5.0%    16%    35%    91%

MACRO-MÉDIA                       21               0.803   29.9%    32%    52%    76%

```
