"""
Monta o `state` e as perguntas que vao ao JEV para UM registro de triagem.

O ENQUADRAMENTO DA PERGUNTA E O CENTRO DO DESENHO. Na triagem titulo/abstract a decisao
do revisor NAO e "este estudo atende aos criterios" — isso so o texto completo responde.
E "vale buscar o texto completo deste registro?". Perguntar a primeira penaliza o JEV por
nao adivinhar o que ele nao tem como ver, e mede a coisa errada.

SEIS NOULS NA MESMA REQUISICAO. O `state` e cobrado uma vez so, entao as facetas saem
quase de graca e entregam tres coisas:

  1. dois preditores concorrentes — o holistico `recuperar`, e o agregado das facetas
     (minimo e produto). Qual ganha e um achado sobre COMO o JEV decide;
  2. a analise de erro. O JEV nao gera texto: nao ha justificativa para ler. "O JEV falha
     mais em Populacao ou em Desfecho?" so tem resposta se a faceta for perguntada;
  3. falsificacao barata — se as cinco facetas derem AUC ~0,5 e o holistico nao, o JEV
     esta casando TOPICO, nao criterio.

Perguntas em ingles: a documentacao do JEV diz que ingles da precisao otima, e os
corpora sao todos em ingles.
"""

from __future__ import annotations

from typing import Any

from . import jev

# Aproximacao de tokens: ~4 chars por token em ingles. Serve para truncar ANTES de
# gastar uma chamada que voltaria 422 por estourar os 32k do `state`.
CHARS_POR_TOKEN = 4
TETO_ESTADO_CHARS = jev.TETO_ESTADO_TOKENS * CHARS_POR_TOKEN - 4_000  # folga p/ perguntas


def _corta(t: str, n: int) -> str:
    t = (t or "").strip()
    return t if len(t) <= n else t[:n].rsplit(" ", 1)[0] + " […]"


# ------------------------------------------------------------------------ perguntas

RECUPERAR = jev.noul(
    "Given the review's eligibility criteria, should this record be retrieved for "
    "full-text assessment? Screening is recall-oriented: retrieve the record when it "
    "plausibly meets the criteria, or when the title and abstract do not contain enough "
    "information to rule it out.",
    verdadeiro="retrieve this record for full-text assessment",
    falso="safely exclude this record on title and abstract alone")

FACETAS = {
    "populacao": jev.noul(
        "Does the population studied in this record match the review's target "
        "population?",
        verdadeiro="the population matches, or the abstract does not rule it out",
        falso="the abstract makes clear the population does not match"),
    "intervencao": jev.noul(
        "Does this record study the intervention or exposure the review asks about?",
        verdadeiro="the intervention/exposure matches, or is not ruled out",
        falso="the abstract makes clear the intervention/exposure does not match"),
    "comparador": jev.noul(
        "Does this record include the comparator the review requires?",
        verdadeiro="the comparator is present, or the review requires none",
        falso="the abstract makes clear the required comparator is absent"),
    "desfecho": jev.noul(
        "Does this record report at least one of the outcomes the review asks about?",
        verdadeiro="at least one required outcome is reported, or is not ruled out",
        falso="the abstract makes clear none of the required outcomes is reported"),
    "desenho": jev.noul(
        "Is this record an eligible study design for the review?",
        verdadeiro="the study design is eligible, or cannot be determined from the "
                   "abstract",
        falso="the abstract makes clear the design is ineligible (e.g. it is a review, "
              "editorial, case report, or protocol when those are excluded)"),
}

PERGUNTAS: dict[str, dict] = {"recuperar": RECUPERAR, **FACETAS}
IDS = list(PERGUNTAS)

# As facetas sao deliberadamente assimetricas: na duvida, VERDADEIRO. E a regra da
# triagem real — excluir exige evidencia, incluir nao. Uma faceta simetrica transformaria
# "o abstract nao diz" em exclusao, que e o erro que perde estudo.


# ESCOLHA NATIVA. `noul` devolve probabilidade, e virar sim/nao exige um limiar que EU
# escolho — arbitrio meu dentro do resultado do modelo. `choice` devolve a opcao
# escolhida: a decisao e do JEV. E o que torna a comparacao com o DeepSeek, que so sabe
# responder binario, uma comparacao entre modelos e nao entre modelo e limiar.
#
# O enquadramento e o MESMO das facetas e do prompt do DeepSeek — assimetrico, com a
# ausencia de informacao caindo para `retrieve`. Os tres tem de estar jogando o mesmo jogo.
ESCOLHA = jev.choice(
    "Screening title and abstract for a systematic review. Should this record be "
    "retrieved for full-text assessment against the eligibility criteria? Screening is "
    "recall-oriented: excluding requires evidence, including does not. Many records have "
    "no abstract; absence of information is not grounds for exclusion.",
    {"retrieve": "retrieve the full text of this record for assessment",
     "exclude": "discard this record on title and abstract alone"})


def so_holistico() -> dict[str, dict]:
    """Variante barata: so a pergunta que decide. ~30% menos tokens por registro."""
    return {"recuperar": RECUPERAR}


# --------------------------------------------------------------------------- estado

def montar_estado(revisao: dict[str, Any], registro: dict[str, Any], *,
                  secoes: list[dict] | None = None) -> dict:
    """
    `state` como objeto JSON — a documentacao do JEV recomenda estrutura sobre string
    solta quando a decisao compara partes distintas.

    `secoes` (opcional) sao as secoes de texto completo, para a ablacao. Quando vem,
    entram truncadas priorizando Metodos e Resultados, que e onde a elegibilidade se
    decide.
    """
    crit = revisao.get("criterios") or {}
    pergunta = _corta(revisao.get("pergunta") or revisao.get("titulo") or "", 1_200)
    estado: dict[str, Any] = {
        "eligibility_criteria": (
            {k: _corta(v, 1_500) for k, v in crit.items() if v}
            or _corta(revisao.get("criterios_brutos") or "", 6_000)),
        "record": {
            "title": _corta(registro.get("titulo") or "", 1_000),
            "abstract": _corta(registro.get("abstract") or "", 6_000),
            "year": registro.get("ano") or "",
        },
    }
    if pergunta:
        # Chave omitida quando nao ha titulo publicado, e nao preenchida com vazio: uma
        # chave vazia no estado sugere ao modelo que a informacao existe e e nula.
        estado = {"review_question": pergunta, **estado}
    if not estado["record"]["abstract"]:
        # Declarado no proprio estado: o JEV precisa saber que o abstract nao existe, e
        # nao inferir ausencia de informacao como ausencia de elegibilidade.
        estado["record"]["abstract"] = "(no abstract available for this record)"

    if secoes:
        prioridade = ("method", "material", "result", "design", "participant",
                      "population", "intervention", "outcome")
        def peso(s: dict) -> int:
            h = (s.get("heading") or "").lower()
            return 0 if any(p in h for p in prioridade) else 1
        orden = sorted(secoes, key=peso)
        orcamento = TETO_ESTADO_CHARS - len(str(estado))
        corpo, usado = [], 0
        for s in orden:
            t = (s.get("text") or "").strip()
            if not t or usado + len(t) > orcamento:
                continue
            corpo.append({"heading": s.get("heading") or "", "text": t})
            usado += len(t)
        if corpo:
            estado["record"]["full_text_sections"] = corpo

    return estado


def tamanho_estimado(estado: dict) -> int:
    """Tokens aproximados do estado. Usado para prever custo antes de gastar."""
    return len(str(estado)) // CHARS_POR_TOKEN
