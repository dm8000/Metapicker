# Metapicker — memória de trabalho

O que foi **medido**, o que foi **refutado**, e o que custou. Não repete o que o código já
diz. Data de referência: 2026-09-21.

## A pergunta

O JEV serve para escolher artigos científicos? Não em abstrato — contra o julgamento
documentado de autores de revisões sistemáticas reais, sobre **o conjunto completo que
eles triaram**, não só sobre os que sobreviveram.

## Refutado

**«O SYNERGY+ não existe.»** Errado, e foi meu erro. Existe: release `synergy_plus_v3.0`,
publicado em 2026-08-27, em dataverse.nl (DOI 10.34894/DDCVCV). É recente demais para
aparecer nos índices de busca — as fontes secundárias ainda descrevem o SYNERGY v1
(26 revisões / 169.288 registros). O v3 traz datasets de 2024 e 2025
(`Monschau_2025`, `Kerschbaumer_2024a/b`, `Sanchez-Gomez_2024`, `Taschner_2024`).

**O `index.json` do GitHub é o snapshot v1.** Aponta para outra coleção de datasets (os
`Cohen_2006_*`). Carregar por ele deu 80.203 registros e 1.781 incluídos, contra os
169.288 / 2.834 publicados — a conferência de sanidade pegou antes de qualquer gasto.
A fonte certa é o pacote oficial, que baixa de dataverse.nl para
`~/.synergy_dataset_source/synergy-dataset-plus/`.

**«Precisa de um LLM para extrair os critérios de elegibilidade.»** Não precisa. Vêm
prontos em `metadata.json` → `publication.eligibility_criteria`, por revisão.

**«O MetaSyn documenta os hard negatives de cada revisão.»** Não documenta. É um pool de
candidatos compartilhado — `matched_corpus_ids` dá só os positivos casados, e o casamento
por título cobre 51,6% (67,7% no split de teste). Reconstruir o screening set por revisão
a partir dele não é executável. Por isso o MetaSyn saiu do benchmark principal e ficou só
na ablação de full text, onde as seções JATS embutidas o tornam a melhor opção.

**«O JEV não tem acesso a full text.»** Impressão errada que eu passei. O `state` aceita
qualquer texto; o limite é 32k tokens para `state` + a maior pergunta, e um artigo
biomédico típico tem 6–12k. O gargalo sempre foi o dataset, nunca o modelo.

## Medido

### JEV (docs.typesafe.ai/models, confirmado em chamada real)

| | |
|---|---|
| endpoint | `POST https://api.typesafe.ai/v1/systemone`, `jev-latest` → `jev-1.13.0` |
| limite | 64k tokens/requisição · 32k para `state` + a maior pergunta |
| preço | US$ 0,042 por milhão de tokens de **entrada**; saída grátis |
| taxa | 1.200 req/min · 250.000 tokens/s |
| **custo real medido** | **837 tokens/registro** com 6 nouls → **US$ 0,035 por mil registros** |
| **latência p50** | **0,30 s** por chamada |

Com US$ 0,70 de crédito: **~19.900 registros**.

### Teste de fumaça — o desenho das facetas se validou

Três casos construídos contra os critérios de uma revisão sobre doença de Wilson:

| caso | `recuperar` | facetas |
|---|---|---|
| ECR do tópico | 0,97 | todas ≥ 0,97 |
| revisão narrativa, **mesmo tópico** | 0,07 | `desenho`=0,03 · `comparador`=0,11 · mas `populacao`=0,65 |
| fora do tópico | 0,03 | todas ≤ 0,11 |

O caso do meio é o que importa: as facetas dizem **por que** ele excluiu — desenho errado,
não tópico errado. O JEV não gera texto, então sem as facetas essa informação não existe.

## Decisões de desenho, com o motivo

**A pergunta é «vale buscar o texto completo?», não «atende aos critérios».** Na triagem
título/abstract o revisor não decide elegibilidade — decide se vale recuperar o artigo.
Perguntar a segunda penaliza o JEV por não adivinhar o que só o texto completo revela.

**As facetas são assimétricas: na dúvida, verdadeiro.** É a regra da triagem real —
excluir exige evidência, incluir não. Uma faceta simétrica transformaria "o abstract não
diz" em exclusão, que é o erro que perde estudo.

**Grava-se a probabilidade, nunca o booleano.** Todo limiar, toda métrica e toda
re-análise saem do SQLite sem gastar de novo.

**Menos revisões, cada uma completa** — e não uma amostra de registros dentro de cada
revisão. Com crédito limitado é o único corte que não quebra o requisito central de rodar
sobre o conjunto completo que os autores triaram.

**Alvo primário é `label_abstract_included`** (passou na triagem), não `label_included`
(entrou na síntese). A diferença entre os dois mede quanto do "erro" do JEV é na verdade
exclusão por texto completo, que ele não tinha como saber.

## Restrições da máquina (2026-09-21)

GPU RTX 3090 em 23.764/24.576 MiB — ocupada, nada local pesado, e o sub-agente de código
do `CLAUDE.md` não roda. RAM 21 GB livres. **Disco em 99%, 11 GB livres** — daí baixar só
campos mínimos. `~/.cache/uv` (20 GB) e `~/.cache/pip` (8,4 GB) são folga disponível se
faltar espaço.

## Contabilidade

| etapa | chamadas | tokens | custo |
|---|---|---|---|
| teste de fumaça | 3 | 2.510 | US$ 0,0001 |
| piloto (3 revisões completas) | 2.633 | 2.691.336 | US$ 0,1130 |
| rodada principal (18 revisões) | 12.478 | 13.334.454 | US$ 0,5600 |
| **acumulado** | **15.114** | **16.028.300** | **US$ 0,6731** |

Custo real: **1.022 tokens/registro** com 6 nouls — 22% acima dos 837 do teste de fumaça,
porque os critérios reais são mais longos que os do caso construído. US$ 0,043 por mil
registros. Tempo de parede: 2.633 registros em **1m42s** com 8 em paralelo.

## Resultado do piloto (2026-09-21)

Três revisões **completas** — Tumkaya_2018 (721 reg., 18,9% passou T/A), Cinquin_2018
(908, 9,1%), Mejean_2024 (1.004, 19,6%). Contraste de prevalência e de cobertura de
abstract (33% a 68% sem abstract).

### O portão: o JEV ganha do BM25, e não é perto

| preditor | AUC | WSS@95 | R@10% | R@20% |
|---|---|---|---|---|
| BM25 (critérios × título+abstract) | 0,588 | 1,5% | 16% | 30% |
| JEV — noul `recuperar` holístico | 0,826 | 28,9% | 35% | 57% |
| **JEV — agregado das facetas (mín)** | **0,857** | **37,3%** | 37% | 61% |
| JEV — agregado das facetas (produto) | 0,854 | 35,0% | 38% | 63% |

+0,27 de AUC e +36 pp de WSS sobre o BM25. Não é casamento de tópico: o JEV está lendo
critério.

### As facetas ganham do holístico

AUC 0,857 contra 0,826, WSS 37,3% contra 28,9%. **O julgamento decomposto bate a pergunta
direta** — e sai na mesma requisição, sem custo adicional de estado. Era o preditor
concorrente que o desenho previa; ele venceu.

### O limiar generaliza entre revisões

Fora-da-dobra (leave-one-review-out): sensibilidade **95,4%**, especificidade 35,0%.
Ajustado nos próprios dados: 95,4% e 38,7%. **Vazamento de só 3,7 pp de especificidade e
0,0 pp de sensibilidade** — um limiar único serve para revisões que ele nunca viu.

### O alvo final é MAIS FÁCIL que o alvo de triagem

| alvo | AUC | WSS@95 | triar para achar 100% |
|---|---|---|---|
| passou triagem T/A | 0,826 | 28,9% | 89% |
| incluído na síntese final | **0,859** | **51,8%** | **61%** |

Contraintuitivo e consistente nas três revisões. A leitura: a triagem T/A humana é mais
*ruidosa* que a decisão final — o revisor deixa passar muita coisa no título/abstract que
depois exclui no texto completo. O JEV concorda mais com o julgamento ponderado do que
com o apressado. Isso inverte a suposição do plano de que cobrar pelo alvo final seria
injusto com o JEV: na prática é o alvo em que ele vai melhor.

### O que isso significa em trabalho poupado

No limiar fora-da-dobra, a 95% de sensibilidade, o JEV descarta ~35% dos registros. Pelo
WSS@95 do melhor preditor, 37,3% da triagem some mantendo 95% dos estudos. Não é
automação da triagem — é um terço do trabalho a menos, com a perda declarada.

## Rodada principal — 21 revisões completas (2026-09-21)

15.111 registros, cada revisão inteira. Prevalência de 0,62% a 14,2%; 37% a 80% sem
abstract. Tempo: 8 min para 12.478 registros. Relatório completo em
`resultados/relatorio.md`.

### Resultado central — alvo: incluído na síntese

| preditor | AUC | WSS@95 | R@10% | R@20% | triar p/ achar 100% |
|---|---|---|---|---|---|
| BM25 (linha de base) | 0,651 | 15,1% | 22% | 38% | 88% |
| JEV — noul `recuperar` holístico | 0,905 | 59,3% | 60% | 79% | 47% |
| JEV — facetas (mínimo) | 0,907 | 58,3% | 61% | 81% | 48% |
| **JEV — facetas (produto)** | **0,920** | **61,1%** | **65%** | **83%** | **45%** |

**+0,27 de AUC e +46 pp de WSS sobre o BM25.** Triando 20% da lista pela ordem do JEV
acha-se 83% dos estudos incluídos; triando 10%, 65%.

### O n=3 do piloto enganou sobre qual agregado vence

No piloto, facetas-mínimo ganhava (0,857 vs 0,854 do produto). Com 21 revisões é o
**produto** que vence (0,920 vs 0,907), e o mínimo perde até do holístico em WSS. A
ordem entre os três preditores não era estável em n=3 — registrado porque é exatamente o
tipo de conclusão que o piloto não sustentava e que teria virado achado se não fosse
refeita.

### A inversão dos alvos se confirmou em n=21

| alvo | AUC | WSS@95 |
|---|---|---|
| incluído na síntese final | **0,920** | **61,1%** |
| passou triagem T/A | 0,824 | 32,4% |

O JEV concorda muito mais com a decisão final ponderada do que com a triagem T/A humana,
mesmo vendo só título e abstract. A triagem T/A é a tarefa mais ruidosa das duas, não a
mais fácil. Por isso `label_included` é o alvo primário do relatório.

### A regra do limiar piorava com mais dados — corrigida

`limiar_ajustado` agregava por MÍNIMO entre as revisões de treino. Com 3 revisões o
vazamento era 3,7 pp; com 21 virou **40,5 pp de especificidade**, porque o mínimo é fixado
pela revisão mais extrema e fica mais conservador quanto mais revisões entram. Regra que
piora com dados. Trocada por quantil parametrizável:

| agregação | sensibilidade (fora-da-dobra) | especificidade | vazamento |
|---|---|---|---|
| mínimo (quantil 0) | 99,0% | 27,1% | 40,5 pp |
| **10º percentil** | **96,6%** | **51,1%** | **16,5 pp** |
| 25º percentil | 94,5% | 60,2% | 7,4 pp |

**Ponto de operação recomendado: 10º percentil.** Fora-da-dobra, mantém 96,6% dos estudos
incluídos descartando 51,1% da lista de triagem — acima do alvo de 95% de sensibilidade,
em revisões que o limiar nunca viu.

### Onde o JEV falha

`Sanchez-Acedo_2023` é o pior caso (AUC 0,757–0,794, WSS 20–27%) e `Mejean_2024` o
segundo (0,819–0,865). Ambos com critérios de exclusão formulados como lista de motivos de
rejeição, não como condições de inclusão — hipótese a testar, não conclusão.

## Concordância — a pergunta que as métricas de ordenação NÃO respondem

AUC, WSS@95 e R@k medem **ordenação**: se os incluídos sobem ao topo. Nenhuma delas
envolve limiar, então nenhuma delas diz se o JEV *decide* como o revisor. Calculado
depois, fora-da-dobra, sobre as mesmas 21 revisões:

| | limiar de recall (0,14) | limiar de concordância (0,82) |
|---|---|---|
| concordância bruta | 55,3% | 92,2% |
| **kappa de Cohen** | **0,106** (ligeira) | **0,410** (moderada) |
| sensibilidade | 96,6% | 53,2% |
| especificidade | 51,1% | 95,1% |
| TP / FP / FN | 854 / 6.709 / 48 | 400 / 594 / 502 |

O limiar 0,82 saiu idêntico nas 21 dobras — estável, não ajustado por revisão.

**O melhor kappa alcançável é 0,410.** Concordância entre dois revisores humanos numa
triagem costuma ficar em 0,5–0,8: como decisor autônomo o JEV fica abaixo de um segundo
revisor. E o trade-off não tem meio-termo bom — ou acha 96,6% dos estudos mandando
recuperar metade da lista, ou concorda razoavelmente e perde 502 dos 902 incluídos.

### A conclusão correta, mais estreita que a anterior

**O JEV é um bom ordenador e um mau decisor autônomo.** A AUC de 0,920 é real e útil:
triar 20% da lista na ordem dele acha 83% dos estudos, o que muda a ordem em que um humano
lê e permite cortar a cauda com perda declarada. O que não se sustenta é usá-lo para dizer
sim ou não no lugar do revisor.

Registrado porque a tabela principal, sozinha, sugere a conclusão mais larga. Ela mede
ordenação; a decisão está medida aqui.

## «Dos artigos que o JEV escolheu, quantos os autores também escolheram?»

Precisão contra `label_included`, no limiar 0,82 (o de máxima concordância, idêntico nas
21 dobras). Mais o enquadramento sem limiar: se o JEV tivesse de escolher exatamente o
mesmo NÚMERO de artigos que os autores, quantos seriam os mesmos (top-k).

| | macro-média (21 revisões) | agrupado |
|---|---|---|
| precisão | **46,1%** | 40,2% |
| top-k | **47,5%** | 44,7% |

**Não é problema de volume.** No limiar 0,82 o JEV escolhe 994 artigos contra os 902 dos
autores — quase o mesmo tamanho de lista — mas só **400 são os mesmos**. Ele escolhe
*outros*, não mais nem menos.

Variação entre revisões: de **20,4%** (`Sanchez-Acedo_2023`) a **87,5%** (`Hanlon_2022`).
Não existe "o JEV acerta X%"; existe um intervalo que depende da revisão.

### Parte do buraco era cegueira, não erro

| fatia | registros | precisão (macro) | top-k |
|---|---|---|---|
| **com abstract** | 5.887 | **58,2%** | 55,3% |
| só título | 9.224 | 41,3% | 47,5% |

Ter abstract vale **+17 pp de precisão** — 67% do corpus é só título. E o comportamento
inverte de sinal: com abstract o JEV escolhe 251 onde os autores escolheram 400 (seleciona
de menos); só com título escolhe 743 contra 502 (seleciona demais). Cego, fica permissivo.

**Resposta curta:** na melhor condição disponível (registro com abstract), JEV e autores
coincidem em ~58% dos artigos escolhidos; no conjunto todo, ~46%. É um segundo leitor que
concorda em pouco mais da metade das escolhas — serve para ordenar e sinalizar, não para
decidir.

## CORREÇÃO DE ENQUADRAMENTO: o alvo justo é a triagem T/A

Os autores tiveram exatamente um momento em que só dispunham de título e abstract — a
triagem T/A. É contra AQUELA decisão que o JEV deve ser cobrado. Medir contra
`label_included` o penaliza por exclusões que só apareceram na leitura do texto completo,
informação que ele nunca teve. Era o argumento do plano; o resultado de AUC deslocou o
enquadramento e ele precisou ser retomado.

Mesma medida, limiar de máxima concordância recalculado para o alvo (0,69):

| alvo | precisão (macro) | top-k | precisão **com abstract** |
|---|---|---|---|
| incluído na síntese | 46,1% | 47,5% | 58,2% |
| **passou na triagem T/A** | **52,6%** | **54,7%** | **69,3%** |

O volume também passa a bater: JEV escolhe 2.897, autores escolheram 3.029 (contra
994 × 902 no alvo final).

**O número justo é 69,3%**: precisão quando os dois lados tinham a mesma informação — um
registro com abstract, contra a decisão que os autores tomaram vendo só título e abstract.
Com ~50% de recall.

### Ordenação e decisão não andam juntas aqui

A AUC é MAIOR no alvo final (0,920) que no T/A (0,824), enquanto a precisão é maior no
T/A. Não é contradição: os positivos de T/A são 3.029 contra 902, muitos limítrofes, então
ordenar a lista inteira fica mais difícil ao mesmo tempo que a decisão binária fica mais
fácil de acertar. Reportar só uma das duas famílias de métrica dá a impressão errada sobre
a outra.

### Leitura final

De cada 10 artigos que o JEV aponta, ~7 os autores também teriam mandado para leitura de
texto completo — e ele deixa passar metade dos que eles mandariam. Segundo leitor, não
revisor.

## Respondendo «o input foi o critério de inclusão e exclusão?»

Foi: o campo `publication.eligibility_criteria` do `metadata.json`, texto livre, **inteiro
e sem truncagem** (nenhuma das 21 passa dos 6.000 chars do limite), mais o título da
revisão e o título+abstract+ano do registro.

O problema é que esse campo é prosa extraída do artigo, e o que há nele varia muito:

| conteúdo do campo | n | precisão média |
|---|---|---|
| inclusão **e** exclusão | 14 | 56,9% |
| só inclusão | 3 | 54,4% |
| só exclusão | 1 | 41,3% |
| **nenhum dos dois** | 3 | **34,2%** |

Lendo os piores casos, três padrões distintos — e nenhum é "o JEV não entendeu critério":

- **`Sanchez-Acedo_2023` (41,0%): não são critérios.** É a descrição do processo — o
  software usado, o filtro de idioma, e "avaliando o abstract para determinar se se
  relacionam à área sob investigação". Qual área nunca é dita no campo.
- **`Kapuka_2021` (44,1%): fragmento.** Começa no meio da frase: "*which* explicitly
  provided information about…", sem sujeito.
- **`Deckers_2022` (14,9%): critérios não-julgáveis por abstract.** "(I1) publicações
  com revisão por pares… (I3) disponíveis online em texto completo". Um abstract não diz
  se o artigo está disponível em texto completo. Só um dos critérios é de conteúdo.
- **`Clark_2021` (8,7%): critérios ótimos, prevalência impossível.** Idade <18, categoria
  clínica listada, desfecho de aprendizagem de palavras, ortografia manipulada. Mas são
  11 positivos em 801 (1,4%): a precisão despenca em qualquer limiar com recall decente.

`Theobald_2021` (85,9%) é o contraste: critérios longos, de conteúdo, com exclusões
explícitas.

## DEFEITO MEU: 2 das 21 revisões rodaram sem o título da revisão

O piloto rodou enquanto o download do SYNERGY+ ainda corria. Carimbos de tempo:

| revisão | `metadata_publication.json` chegou | chamada | título no estado |
|---|---|---|---|
| Mejean_2024 | 15:46:33 | 15:47:44 | sim |
| Cinquin_2018 | 15:49:02 | 15:47:14 | **não** (1m48s tarde) |
| Tumkaya_2018 | 15:55:38 | 15:48:22 | **não** (7m16s tarde) |

Cinquin (33,6%) e Tumkaya (50,6%) ficam abaixo da média de 52,6%, então **o número global
está subestimado**. Não corrigido: o crédito acabou antes. Custaria US$ 0,07 e viraria uma
ablação do valor do título da revisão.

## Fim do crédito, e um defeito do runner

HTTP 402 `billing_error` após US$ 0,673. As 15.111 chamadas da rodada principal estão
íntegras (zero falhas); a tentativa de reparo ficou isolada na variante `com_titulo`.

O runner engoliu o 402 como falha comum e anunciou "nenhuma chamada" depois de 1.629
tentativas. Corrigido: 401/402/422 agora sobem como `ErroFatal` e abortam o lote, com
teste de regressão.
