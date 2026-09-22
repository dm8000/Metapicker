# Metapicker

Benchmark do **JEV** (TypeSafe, *System One model*) em **triagem de revisões
sistemáticas**: dados os critérios de elegibilidade de uma revisão real e o conjunto
completo de registros que os autores recuperaram, o JEV separa o que vale recuperar do
que não vale?

O requisito que define o desenho: a avaliação roda sobre **o conjunto completo triado
pelos autores**, não sobre os estudos incluídos. Sem os excluídos documentados não há
especificidade a medir.

## Instalação

```bash
pip install httpx synergy-dataset
python -m synergy_dataset get -l -o dados/synergy_plus -v publication_year
```

A chave do JEV vai em `api_key_jev.txt` (já no `.gitignore`) ou em `TYPESAFE_API_KEY`.

## Uso

```bash
python testes/test_jev.py && python testes/test_metricas.py   # nenhum toca a rede

python metapicker/fontes/synergy.py catalogo      # o que existe e quanto custa cada um
python metapicker/correr.py --revisoes X Y Z --orcamento 0.10
python metapicker/avaliar.py --alvo ta --facetas
python metapicker/limiar.py  --alvo ta
python metapicker/baseline.py --alvo ta           # BM25, CPU
```

`correr.py` é retomável: rodar de novo continua de onde parou, e `--orcamento` corta a
rodada no teto em dólares.

## O que este benchmark mede, e o que não mede

**Mede** a triagem título/abstract: dados os critérios e o par título+abstract, este
registro deve ir para leitura de texto completo? É a decisão que descarta ~98% dos
registros de uma revisão, e onde um erro custa um estudo perdido.

**Não mede** a elegibilidade final. Essa depende do texto completo, e os autores só o
leram para os poucos que passaram na triagem. O alvo primário é por isso
`label_abstract_included`, não `label_included`; a diferença entre os dois é reportada
separada e mede o desconto.

## Três coisas que o desenho faz de propósito

**Seis nouls na mesma requisição.** O `state` é cobrado uma vez, então as cinco facetas
PI/ECO custam ~36% a mais e entregam o que o JEV não sabe dizer: *por que* excluiu. O JEV
não gera texto — sem as facetas, a análise de erro não existe.

**A pergunta é «vale buscar o texto completo?»**, não «atende aos critérios». As facetas
são assimétricas: na dúvida, verdadeiro. Excluir exige evidência; incluir não.

**Probabilidade crua no SQLite, nunca booleano.** `noul` devolve 0–1. Todo limiar e toda
métrica saem daí sem gastar de novo.

## Métricas

`AUC-ROC` (livre de limiar) · `WSS@95` (trabalho poupado a 95% de recall — a métrica da
área) · `recall@10%/20%/50%` · quanto da lista é preciso triar para achar **todos**.
Sensibilidade, especificidade, precisão e F1 saem no limiar de operação, escolhido
**fora-da-dobra** por leave-one-review-out. A 0,8% de prevalência, um classificador que
diz "não" a tudo tem 99,2% de acurácia — daí as quatro clássicas não bastarem sozinhas.

Tudo por revisão, depois **macro-média**. Micro-média deixaria a maior revisão decidir.

## Estado

Ver `ACHADOS.md` — o que foi medido, o que foi refutado, e a contabilidade de custo.

## Resultado

Ver `resultados/comparacao.md` (comparação dos métodos) e `ACHADOS.md` (o que foi medido,
o que foi refutado, contabilidade de custo). Resumo, sobre 257 registros com abstract de
3 revisões:

| método | restaram | da lista | % acerto do que entrou | % acerto do que saiu | perdeu | AUC | custo |
|---|---|---|---|---|---|---|---|
| JEV `choice` em lote | 115 | 45% | 45,2% | 90,1% | 14 | 0,729 | US$ 0,0086 |
| DeepSeek V4 Flash | 101 | 39% | 45,5% | 87,2% | 20 | 0,705 | US$ 0,0315 |
| Qwen3-Reranker 0.6B (local) | 222 | 86% | 29,7% | 100,0% | 0 | 0,592 | US$ 0 |
| *(os autores)* | *66* | *26%* | *100%* | *100%* | *0* | — | — |

**«Restaram»** é a coluna que mais separa os três, porque triagem serve para sobrar menos
artigo. O Qwen devolve 222 dos 257 — tira 14% da lista, e quem recebesse a saída dele leria
86% do que leria sem ele. Seu 100% de acerto no que descartou e seu zero de perdidos são o
mesmo fato visto de outro ângulo: ele quase não descarta. DeepSeek e JEV cortam de verdade
(39% e 45% da lista), e pagam com 20 e 14 estudos perdidos. Nenhum chega aos 26% do humano.

A AUC das três é de decisão binária — vale (sensibilidade + especificidade)/2, não mede
ordenação. O Qwen com score contínuo chega a **AUC 0,853**, acima dos outros dois: ele
ordena bem e decide mal.

Para calibrar: em `Hanlon_2022` a concordância entre os **dois revisores humanos** foi de
**kappa 98%**. A distância até o patamar humano não é de ajuste fino.

## Limitações declaradas

- **O conjunto não é o que os autores triaram, é a fatia que casou no OpenAlex** — 32% em
  `Theobald_2021`, 56% em `Hanlon_2022`, 82% em `Deckers_2022`. E a perda é enviesada: a
  taxa de passagem diverge do paper em direções opostas (Theobald 52% contra 36%
  publicados; Hanlon 18% contra 25%).
- **67% do corpus não tem abstract no OpenAlex**, e o revisor humano quase certamente o
  tinha. Toda métrica principal é reportada só na fatia com abstract.
- `Theobald_2021` triou por "título, abstract **e palavras-chave**" — um campo que não foi
  enviado a nenhum dos modelos.
- O custo do DeepSeek foi medido pela janela do chat, sem saber se houve cache de entrada;
  o intervalo real vai de ~US$ 0,016 (com cache) a ~US$ 0,05 (histórico integral sem).

## Dados e atribuição

Os registros vêm do **SYNERGY+ v3.0** (ASReview), CC-BY 4.0 —
[repositório](https://github.com/asreview/synergy-dataset) ·
[dataverse.nl doi:10.34894/DDCVCV](https://doi.org/10.34894/DDCVCV). Títulos e abstracts
são objetos OpenAlex e mantêm os termos de origem. Os arquivos em `resultados/deepseek/`
reproduzem esses campos para permitir a réplica do experimento.

Os três papers usados como verdade de referência:
Theobald (2021) [10.1016/j.cedpsych.2021.101976](https://doi.org/10.1016/j.cedpsych.2021.101976) ·
Hanlon et al. (2022) [10.12688/wellcomeopenres.17208.2](https://doi.org/10.12688/wellcomeopenres.17208.2) ·
Deckers & Lago (2022) [10.1016/j.jss.2022.111415](https://doi.org/10.1016/j.jss.2022.111415).

Modelos avaliados: **Jev 1.13** (TypeSafe) · **DeepSeek V4 Flash** ·
**Qwen3-Reranker 0.6B**.
