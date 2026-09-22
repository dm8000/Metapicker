"""
Cliente do JEV (TypeSafe) — um *System One model*: avalia perguntas TIPADAS contra um
estado e devolve resultado estruturado. Nao gera texto.

    POST https://api.typesafe.ai/v1/systemone
    Authorization: Bearer <chave>
    {"state": ..., "model": "jev-latest", "questions": {"id": {"type": "noul", ...}}}

Adaptado do cliente ja rodado em producao no WiseOak. Ele codifica DUAS ARMADILHAS que
custaram erro real:

  1. a resposta esta em `r["answers"][qid]`, NAO em `r[qid]`;
  2. `noul` devolve PROBABILIDADE (0..1), nao booleano. Tratar como bool faz tudo virar
     "sim" e o relatorio sai com confianca 0,00 em todos os itens sem ninguem notar.

`obter()` existe justamente para que nenhum script volte a desempacotar isso na mao, e
testes/test_jev.py trava as duas contra regressao.

LIMITES MEDIDOS (docs.typesafe.ai/models):
  64k tokens por requisicao · 32k para o `state` + a maior pergunta
  US$ 0,042 por milhao de tokens de ENTRADA; saida gratis
  1.200 req/min e 250.000 tokens/s

A CHAVE nunca aparece em log, excecao ou arquivo de saida. E lida de
`api_key_jev.txt` (ja no .gitignore) ou de TYPESAFE_API_KEY.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterable

import httpx

RAIZ = Path(__file__).resolve().parents[1]
URL = os.environ.get("JEV_URL", "https://api.typesafe.ai/v1/systemone")
MODELO = os.environ.get("JEV_MODELO", "jev-latest")

# 1.200 req/min = 20/s. 8 em paralelo fica confortavelmente abaixo e ja torna o tempo de
# parede tolerayel nos nossos tamanhos (169k registros no SYNERGY).
PARALELO = int(os.environ.get("JEV_PARALELO", "8"))

# Limite duro da API para `state` + a maior pergunta. Usado por triagem.py para truncar
# full text antes de gastar uma chamada que voltaria 422.
TETO_ESTADO_TOKENS = 32_000

PRECO_ENTRADA_POR_MILHAO = 0.042


class ErroJev(RuntimeError):
    pass


class ErroFatal(ErroJev):
    """Erro que NAO adianta repetir: credito esgotado (402), chave invalida (401),
    requisicao malformada (422). Sobe por cima do laco de lote em vez de virar mais um
    item None — engolir isso faz o runner anunciar "nenhuma chamada" depois de milhares
    de tentativas, que foi exatamente o que aconteceu quando o credito acabou."""


class Conta:
    """Acumula tokens, chamadas, custo e latencia. Nao e thread-local de proposito:
    em_lote roda em varias threads e o total tem de ser do lote inteiro."""

    def __init__(self) -> None:
        self.zerar()

    def zerar(self) -> None:
        self.chamadas = self.falhas = 0
        self.entrada = self.saida = 0
        self.segundos = 0.0
        self.latencias: list[float] = []

    def registrar(self, uso: dict, seg: float) -> None:
        self.chamadas += 1
        self.entrada += int(uso.get("input_tokens") or 0)
        self.saida += int(uso.get("output_tokens") or 0)
        self.segundos += seg
        self.latencias.append(seg)

    @property
    def custo(self) -> float:
        return self.entrada / 1e6 * PRECO_ENTRADA_POR_MILHAO

    def p50(self) -> float:
        return sorted(self.latencias)[len(self.latencias) // 2] if self.latencias else 0.0

    def linhas(self, prefixo: str = "  ") -> list[str]:
        if not self.chamadas:
            return [f"{prefixo}nenhuma chamada"]
        return [
            f"{prefixo}chamadas          {self.chamadas:,}".replace(",", ".")
            + (f" ({self.falhas} falharam)" if self.falhas else ""),
            f"{prefixo}tokens de entrada {self.entrada:,}".replace(",", "."),
            f"{prefixo}custo             US$ {self.custo:.4f}",
            f"{prefixo}latência p50      {self.p50():.2f}s por chamada",
            f"{prefixo}soma das latências {self.segundos/60:.1f} min "
            f"(tempo de parede é menor: {PARALELO} em paralelo)",
        ]


CONTA = Conta()


def _chave() -> str:
    try:
        return (RAIZ / "api_key_jev.txt").read_text().strip()
    except Exception:
        c = os.environ.get("TYPESAFE_API_KEY")
        if not c:
            raise ErroJev("sem chave: nem api_key_jev.txt nem TYPESAFE_API_KEY") from None
        return c


_cliente: httpx.Client | None = None


def cliente() -> httpx.Client:
    global _cliente
    if _cliente is None:
        _cliente = httpx.Client(
            headers={"Authorization": f"Bearer {_chave()}",
                     "Content-Type": "application/json"},
            timeout=httpx.Timeout(120.0, connect=15.0),
            limits=httpx.Limits(max_connections=PARALELO * 2))
    return _cliente


# ---------------------------------------------------------------- construir perguntas

def noul(instrucoes: str, *, verdadeiro: str | None = None,
         falso: str | None = None) -> dict:
    """Pergunta de valor-verdade. A resposta volta como PROBABILIDADE em 0..1."""
    q: dict = {"type": "noul", "instructions": instrucoes}
    if verdadeiro or falso:
        q["criteria"] = {"true": verdadeiro or "yes", "false": falso or "no"}
    return q


def choice(instrucoes: str, opcoes: dict[str, str]) -> dict:
    """Escolha entre opcoes nomeadas. Maximo 255."""
    return {"type": "choice", "instructions": instrucoes, "criteria": opcoes}


def score(instrucoes: str, niveis: list[str]) -> dict:
    """Nota contra uma rubrica. A API exige de 2 a 10 niveis."""
    if not 2 <= len(niveis) <= 10:
        raise ValueError(f"score exige de 2 a 10 niveis, recebi {len(niveis)}")
    return {"type": "score", "instructions": instrucoes, "criteria": niveis}


# ------------------------------------------------------------------------- perguntar

def perguntar(estado: Any, perguntas: dict[str, dict], *,
              modelo: str = MODELO, tentativas: int = 4) -> dict:
    """
    Uma requisicao: varias perguntas contra o MESMO estado, avaliadas em paralelo pelo
    servico — e o estado e cobrado UMA vez. Devolve o JSON cru; use `obter()`.

    429 e 529 sao recuo exponencial, como a documentacao manda. A mensagem de erro NUNCA
    inclui o corpo da requisicao, para a chave e o estado nao vazarem para o log.
    """
    corpo = {"state": estado, "model": modelo, "questions": perguntas}
    espera = 1.0
    for t in range(tentativas):
        try:
            r = cliente().post(URL, json=corpo)
        except httpx.HTTPError as e:
            if t == tentativas - 1:
                raise ErroJev(f"falha de rede: {type(e).__name__}") from None
            time.sleep(espera); espera *= 2
            continue
        if r.status_code == 200:
            d = r.json()
            CONTA.registrar(d.get("usage") or {}, r.elapsed.total_seconds())
            return d
        if r.status_code in (429, 529) and t < tentativas - 1:
            time.sleep(float(r.headers.get("retry-after") or espera)); espera *= 2
            continue
        if r.status_code in (401, 402, 422):
            raise ErroFatal(f"HTTP {r.status_code}: {r.text[:300]}")
        raise ErroJev(f"HTTP {r.status_code}: {r.text[:300]}")
    raise ErroJev("esgotou as tentativas")


def obter(resposta: dict, qid: str) -> Any:
    """
    Desempacota UMA resposta.

      noul   -> float 0..1 (PROBABILIDADE, nao booleano)
      choice -> (opcao, confianca)
      score  -> (nota, confianca)
    """
    a = (resposta.get("answers") or {}).get(qid)
    if a is None:
        raise ErroJev(f"pergunta '{qid}' ausente; vieram "
                      f"{sorted((resposta.get('answers') or {}).keys())}")
    tipo = a.get("type")
    if tipo == "noul":
        return float(a["noul"])
    if tipo == "choice":
        return a["choice"], float(a.get("confidence") or 0.0)
    if tipo == "score":
        return a["score"], float(a.get("confidence") or 0.0)
    raise ErroJev(f"tipo de resposta desconhecido: {tipo!r}")


def em_lote(trabalhos: Iterable[tuple[Any, dict[str, dict]]], *,
            paralelo: int = PARALELO, modelo: str = MODELO,
            progresso=None) -> list[dict | None]:
    """
    Varios estados em paralelo, preservando a ORDEM de entrada. Item que falhou vira
    None — nunca some da lista, porque item ausente viraria erro na contagem.
    """
    trabalhos = list(trabalhos)
    saida: list[dict | None] = [None] * len(trabalhos)
    feitos = 0

    fatal: list[ErroFatal] = []

    def um(i: int) -> tuple[int, dict | None]:
        if fatal:
            return i, None
        estado, qs = trabalhos[i]
        try:
            return i, perguntar(estado, qs, modelo=modelo)
        except ErroFatal as e:
            fatal.append(e)
            return i, None
        except ErroJev:
            CONTA.falhas += 1
            return i, None

    with ThreadPoolExecutor(max_workers=paralelo) as ex:
        for i, r in ex.map(um, range(len(trabalhos))):
            saida[i] = r
            feitos += 1
            if progresso and feitos % 25 == 0:
                progresso(feitos, len(trabalhos))
    if fatal:
        raise fatal[0]
    if progresso:
        progresso(feitos, len(trabalhos))
    return saida
