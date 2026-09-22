#!/usr/bin/env python3
"""
Linha de base BM25: ranqueia os registros pela similaridade entre os criterios de
elegibilidade e titulo+abstract. Mesma informacao que o JEV recebe, sem modelo nenhum.

POR QUE ELA E OBRIGATORIA. Uma AUC de 0,85 nao se sabe se e boa. Se o BM25 — que e
contagem de palavra — chegar perto, o JEV nao esta lendo criterio, esta casando topico,
e nao ha noticia. Roda em CPU: a GPU desta maquina esta ocupada.

Implementacao propria de BM25 para nao acrescentar dependencia por vinte linhas.

    ./baseline.py --fonte synergy
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from collections import Counter
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from metapicker import avaliar as A  # noqa: E402

PALAVRA = re.compile(r"[a-z0-9]+")
# Lista minima: so o que distorce BM25 por frequencia bruta. Nao e limpeza linguistica.
VAZIAS = set("""a an the of in on for to and or with without by from as at is are was
were be been being this that these those we our it its their they he she which who whom
not no nor if then than so such but had has have do does did can could would should may
might must will shall""".split())


def fichas(t: str) -> list[str]:
    return [w for w in PALAVRA.findall((t or "").lower())
            if w not in VAZIAS and len(w) > 2]


def bm25(consulta: list[str], docs: list[list[str]], k1: float = 1.5,
         b: float = 0.75) -> list[float]:
    n = len(docs)
    if not n:
        return []
    tam = [len(d) for d in docs]
    medio = sum(tam) / n or 1.0
    df = Counter()
    for d in docs:
        df.update(set(d))
    idf = {w: math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5)) for w in set(consulta)
           if w in df}
    saida = []
    for d, ld in zip(docs, tam):
        tf = Counter(d)
        s = 0.0
        for w, iw in idf.items():
            f = tf.get(w, 0)
            if f:
                s += iw * f * (k1 + 1) / (f + k1 * (1 - b + b * ld / medio))
        saida.append(s)
    return saida


def pontuar(revisao: dict, registros: list[dict]) -> list[float]:
    crit = revisao.get("criterios_brutos") or revisao.get("titulo") or ""
    consulta = fichas(f"{revisao.get('titulo','')} {crit}")
    docs = [fichas(f"{r.get('titulo','')} {r.get('abstract','')}") for r in registros]
    return bm25(consulta, docs)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonte", default="synergy")
    ap.add_argument("--alvo", default="ta", choices=["ta", "final"])
    ap.add_argument("--revisoes", nargs="*")
    a = ap.parse_args()

    if a.fonte != "synergy":
        print("só synergy por ora", file=sys.stderr); return 1
    from metapicker.fontes import synergy
    registros, revs = synergy.carregar(a.revisoes or None)
    porrev: dict[str, list] = {}
    for r in registros:
        porrev.setdefault(r.review_id, []).append(vars(r))

    campo = "rotulo_ta" if a.alvo == "ta" else "rotulo_final"
    por: dict[str, tuple[list[float], list[int]]] = {}
    for v in revs:
        g = porrev.get(v.review_id) or []
        rot = [x[campo] for x in g]
        if any(r is None for r in rot) or not sum(r for r in rot if r):
            continue
        por[v.review_id] = (pontuar(vars(v), g), [int(r) for r in rot])

    print("=" * 78)
    print(f"LINHA DE BASE BM25 · {a.fonte} · alvo="
          + ("passou T/A" if a.alvo == "ta" else "incluído na síntese"))
    print("=" * 78, "")
    print("\n".join(A.relatar(por, "BM25 (critérios × título+abstract)")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
