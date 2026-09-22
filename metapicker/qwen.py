"""
Client for Qwen3-Reranker — the LOCAL baseline of the benchmark.

    POST http://127.0.0.1:10099/rerank
    {"model": ..., "query": ..., "documents": [...]}  ->  {"results":[{index, relevance_score}]}

A reranker decides nothing: it returns a `relevance_score` per (query, document) pair.
Turning that into yes/no requires a threshold, and the threshold comes from threshold.py
fitted on the reviews OUTSIDE the comparison set — never on the reviews being reported.

THE SERVER MATTERS MORE THAN THE CLIENT HERE. Two traps, both measured:

  1. the shared llama-swap instance starts without `--ubatch-size`, so the physical batch
     stays at 512 tokens and it refuses any pair above that with HTTP 500. Criteria alone
     reach 689 tokens;
  2. `--parallel` DIVIDES `--ctx-size` across slots. `--ctx-size 4096 --parallel 8` gives
     each slot 512 tokens and the server refuses with HTTP 400 exceed_context_size_error
     — a DIFFERENT error from the one above, and one that only appears once you add
     parallelism.

Use `metapicker/serve_reranker.sh`, which handles both.

THE ADAPTIVE TRUNCATION below is a safety net, NOT the normal path. If it fires, that
review's comparison is compromised — the reranker would have received less criteria text
than JEV did — so it records into `TRUNCATED` and the caller must report it loudly, not
in a footnote.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

import httpx

URL = os.environ.get("QWEN_URL", "http://127.0.0.1:10099")
MODEL = os.environ.get("QWEN_MODEL", "qwen3-reranker")
PARALLEL = int(os.environ.get("QWEN_PARALLEL", "8"))
KEY_FILE = Path("/home/phobos/LLMs/config/llama-swap.key")

# Reviews where adaptive truncation fired. Empty = the comparison is intact.
TRUNCATED: dict[str, int] = {}


class QwenError(RuntimeError):
    pass


class FatalQwenError(QwenError):
    """Pointless to retry: server down, wrong model, route missing."""


class Ledger:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.calls = self.failures = self.pairs = 0
        self.seconds = 0.0
        self.latencies: list[float] = []

    def record(self, n_pairs: int, secs: float) -> None:
        self.calls += 1
        self.pairs += n_pairs
        self.seconds += secs
        self.latencies.append(secs)

    def p50(self) -> float:
        return sorted(self.latencies)[len(self.latencies) // 2] if self.latencies else 0.0

    def lines(self, prefix: str = "  ") -> list[str]:
        if not self.calls:
            return [f"{prefix}no calls"]
        rate = self.pairs / self.seconds if self.seconds else 0.0
        L = [f"{prefix}calls        {self.calls:,}"
             + (f" ({self.failures} failed)" if self.failures else ""),
             f"{prefix}pairs        {self.pairs:,}",
             f"{prefix}latency p50  {self.p50():.2f}s per call",
             f"{prefix}throughput   {rate:.1f} pairs/s (summed latency)"]
        if TRUNCATED:
            L.append(f"{prefix}TRUNCATED in: {TRUNCATED} — comparison compromised there")
        return L


LEDGER = Ledger()
_client: httpx.Client | None = None


def _key() -> str:
    try:
        return KEY_FILE.read_text().strip()
    except Exception:
        return os.environ.get("LLAMA_SWAP_KEY", "")


def client() -> httpx.Client:
    global _client
    if _client is None:
        h = {"Content-Type": "application/json"}
        k = _key()
        if k:
            h["Authorization"] = f"Bearer {k}"
        _client = httpx.Client(headers=h, timeout=httpx.Timeout(300.0, connect=10.0),
                               limits=httpx.Limits(max_connections=PARALLEL * 2))
    return _client


def score(query: str, documents: list[str], *, label: str = "?",
          attempts: int = 3) -> list[float]:
    """
    Returns one score per document, IN INPUT ORDER — the API responds sorted by
    relevance, and restoring the original order is this function's job. Silently changing
    that would attach scores to the wrong records, an error no test would catch.
    """
    if not documents:
        return []
    docs = list(documents)
    wait = 1.0
    for t in range(attempts):
        body = {"model": MODEL, "query": query, "documents": docs}
        t0 = time.time()
        try:
            r = client().post(f"{URL}/rerank", json=body)
        except httpx.HTTPError as e:
            if t == attempts - 1:
                raise FatalQwenError(f"server down: {type(e).__name__}") from None
            time.sleep(wait); wait *= 2
            continue
        if r.status_code == 200:
            d = r.json()
            LEDGER.record(len(docs), time.time() - t0)
            out = [0.0] * len(docs)
            for x in d.get("results", []):
                out[int(x["index"])] = float(x["relevance_score"])
            return out
        txt = r.text[:200]
        if r.status_code == 500 and "too large" in txt.lower():
            # Safety net. With --ubatch-size 4096 this should NOT happen.
            cut = max(200, int(max(len(x) for x in docs) * 0.6))
            docs = [x[:cut] for x in docs]
            TRUNCATED[label] = TRUNCATED.get(label, 0) + 1
            continue
        if r.status_code in (401, 404):
            raise FatalQwenError(f"HTTP {r.status_code}: {txt}")
        if t == attempts - 1:
            raise QwenError(f"HTTP {r.status_code}: {txt}")
        time.sleep(wait); wait *= 2
    raise QwenError("ran out of attempts")


def in_batch(jobs: Iterable[tuple[str, list[str], str]], *,
             parallel: int = PARALLEL, progress=None) -> list[list[float] | None]:
    """Several (query, documents, label) in parallel, preserving INPUT ORDER."""
    jobs = list(jobs)
    out: list[list[float] | None] = [None] * len(jobs)
    fatal: list[FatalQwenError] = []
    done = 0

    def one(i: int):
        if fatal:
            return i, None
        q, docs, lab = jobs[i]
        try:
            return i, score(q, docs, label=lab)
        except FatalQwenError as e:
            fatal.append(e); return i, None
        except QwenError:
            LEDGER.failures += 1; return i, None

    with ThreadPoolExecutor(max_workers=parallel) as ex:
        for i, r in ex.map(one, range(len(jobs))):
            out[i] = r
            done += 1
            if progress and done % 5 == 0:
                progress(done, len(jobs))
    if fatal:
        raise fatal[0]
    if progress:
        progress(done, len(jobs))
    return out


def document(title: str, abstract: str) -> str:
    """Same absence marker screening.build_state uses, so both models see the SAME
    absence rather than one of them seeing an empty field."""
    a = (abstract or "").strip() or "(no abstract available for this record)"
    return f"{(title or '').strip()}\n\n{a}"


def query(review: dict) -> str:
    """Exactly what went to JEV: review title plus the criteria, intact."""
    t = (review.get("question") or review.get("title") or "").strip()
    c = (review.get("raw_criteria") or "").strip()
    return f"{t}\n\n{c}".strip() if t else c
