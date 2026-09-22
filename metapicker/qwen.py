"""
Cliente do Qwen3-Reranker 0.6B — a linha de base LOCAL do benchmark.

    POST http://127.0.0.1:10099/rerank
    {"model": ..., "query": ..., "documents": [...]}  ->  {"results":[{index, relevance_score}]}

Um reranker nao decide nada: devolve `relevance_score` por par (query, documento). Virar
sim/nao exige limiar, e o limiar sai de `limiar.py` ajustado nas revisoes FORA do conjunto
de comparacao — nunca nas proprias.

O SERVIDOR IMPORTA MAIS QUE O CLIENTE AQUI. A instancia compartilhada do llama-swap
(porta 10012) sobe sem `--ubatch-size` e recusa com HTTP 500 qualquer par acima de 512
tokens de batch fisico. Os criterios vao ate 689 tokens sozinhos. Use
`metapicker/servir_rerank.sh`, que sobe na 10099 com `--ubatch-size 4096`.

A TRUNCAGEM ADAPTATIVA abaixo e rede de seguranca, NAO caminho normal. Se ela disparar, a
comparacao daquela revisao esta comprometida — o Qwen teria recebido menos do que o JEV
recebeu — entao ela grava em `TRUNCOU` e quem chama tem de reportar alto, nao no rodape.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

import httpx

URL = os.environ.get("QWEN_URL", "http://127.0.0.1:10099")
MODELO = os.environ.get("QWEN_MODELO", "qwen3-reranker-0.6b")
PARALELO = int(os.environ.get("QWEN_PARALELO", "8"))
CHAVE_ARQ = Path("/home/phobos/LLMs/config/llama-swap.key")

# Revisoes em que a truncagem adaptativa disparou. Vazio = comparacao integra.
TRUNCOU: dict[str, int] = {}


class ErroQwen(RuntimeError):
    pass


class ErroFatalQwen(ErroQwen):
    """Nao adianta repetir: servidor fora do ar, modelo errado, rota inexistente."""


class Conta:
    def __init__(self) -> None:
        self.zerar()

    def zerar(self) -> None:
        self.chamadas = self.falhas = self.pares = 0
        self.segundos = 0.0
        self.latencias: list[float] = []

    def registrar(self, n_pares: int, seg: float) -> None:
        self.chamadas += 1
        self.pares += n_pares
        self.segundos += seg
        self.latencias.append(seg)

    def p50(self) -> float:
        return sorted(self.latencias)[len(self.latencias) // 2] if self.latencias else 0.0

    def linhas(self, prefixo: str = "  ") -> list[str]:
        if not self.chamadas:
            return [f"{prefixo}nenhuma chamada"]
        vazao = self.pares / self.segundos if self.segundos else 0.0
        L = [f"{prefixo}chamadas    {self.chamadas:,}".replace(",", ".")
             + (f" ({self.falhas} falharam)" if self.falhas else ""),
             f"{prefixo}pares       {self.pares:,}".replace(",", "."),
             f"{prefixo}latência p50 {self.p50():.2f}s por chamada",
             f"{prefixo}vazão       {vazao:.1f} pares/s (soma das latências)"]
        if TRUNCOU:
            L.append(f"{prefixo}TRUNCOU em: {TRUNCOU} — comparação comprometida nessas")
        return L


CONTA = Conta()
_cliente: httpx.Client | None = None


def _chave() -> str:
    try:
        return CHAVE_ARQ.read_text().strip()
    except Exception:
        return os.environ.get("LLAMA_SWAP_KEY", "")


def cliente() -> httpx.Client:
    global _cliente
    if _cliente is None:
        h = {"Content-Type": "application/json"}
        k = _chave()
        if k:
            h["Authorization"] = f"Bearer {k}"
        _cliente = httpx.Client(headers=h, timeout=httpx.Timeout(300.0, connect=10.0),
                                limits=httpx.Limits(max_connections=PARALELO * 2))
    return _cliente


def pontuar(query: str, documentos: list[str], *, rotulo: str = "?",
            tentativas: int = 3) -> list[float]:
    """
    Devolve um score por documento, NA ORDEM DE ENTRADA — a API responde ordenada por
    relevancia, e reordenar de volta e responsabilidade daqui. Trocar isso silenciosamente
    associaria score ao registro errado, que e um erro que nao aparece em teste nenhum.
    """
    if not documentos:
        return []
    docs = list(documentos)
    espera = 1.0
    for t in range(tentativas):
        corpo = {"model": MODELO, "query": query, "documents": docs}
        t0 = time.time()
        try:
            r = cliente().post(f"{URL}/rerank", json=corpo)
        except httpx.HTTPError as e:
            if t == tentativas - 1:
                raise ErroFatalQwen(f"servidor fora do ar: {type(e).__name__}") from None
            time.sleep(espera); espera *= 2
            continue
        if r.status_code == 200:
            d = r.json()
            CONTA.registrar(len(docs), time.time() - t0)
            saida = [0.0] * len(docs)
            for x in d.get("results", []):
                saida[int(x["index"])] = float(x["relevance_score"])
            return saida
        txt = r.text[:200]
        if r.status_code == 500 and "too large" in txt.lower():
            # Rede de seguranca. Com --ubatch-size 4096 isto NAO deve acontecer.
            corte = max(200, int(max(len(x) for x in docs) * 0.6))
            docs = [x[:corte] for x in docs]
            TRUNCOU[rotulo] = TRUNCOU.get(rotulo, 0) + 1
            continue
        if r.status_code in (401, 404):
            raise ErroFatalQwen(f"HTTP {r.status_code}: {txt}")
        if t == tentativas - 1:
            raise ErroQwen(f"HTTP {r.status_code}: {txt}")
        time.sleep(espera); espera *= 2
    raise ErroQwen("esgotou as tentativas")


def em_lote(trabalhos: Iterable[tuple[str, list[str], str]], *,
            paralelo: int = PARALELO, progresso=None) -> list[list[float] | None]:
    """Varios (query, documentos, rotulo) em paralelo, preservando a ORDEM de entrada."""
    trabalhos = list(trabalhos)
    saida: list[list[float] | None] = [None] * len(trabalhos)
    fatal: list[ErroFatalQwen] = []
    feitos = 0

    def um(i: int):
        if fatal:
            return i, None
        q, docs, rot = trabalhos[i]
        try:
            return i, pontuar(q, docs, rotulo=rot)
        except ErroFatalQwen as e:
            fatal.append(e); return i, None
        except ErroQwen:
            CONTA.falhas += 1; return i, None

    with ThreadPoolExecutor(max_workers=paralelo) as ex:
        for i, r in ex.map(um, range(len(trabalhos))):
            saida[i] = r
            feitos += 1
            if progresso and feitos % 5 == 0:
                progresso(feitos, len(trabalhos))
    if fatal:
        raise fatal[0]
    if progresso:
        progresso(feitos, len(trabalhos))
    return saida


def documento(titulo: str, abstract: str) -> str:
    """Mesmo marcador de ausencia que triagem.montar_estado usa, para os dois modelos
    verem a MESMA ausencia e nao um deles ver um campo vazio."""
    a = (abstract or "").strip() or "(no abstract available for this record)"
    return f"{(titulo or '').strip()}\n\n{a}"


def consulta(revisao: dict) -> str:
    """Exatamente o que foi ao JEV: titulo da revisao + criterios integros."""
    t = (revisao.get("pergunta") or revisao.get("titulo") or "").strip()
    c = (revisao.get("criterios_brutos") or "").strip()
    return f"{t}\n\n{c}".strip() if t else c
