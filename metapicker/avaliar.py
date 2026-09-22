#!/usr/bin/env python3
"""
Metricas da triagem, lidas das probabilidades cruas no SQLite. Nao chama a API.

POR REVISAO, DEPOIS MACRO-MEDIA. Nunca juntar os registros num balaio so: as revisoes
variam de centenas a dezenas de milhares de registros e de 0,2% a 27% de prevalencia.
Micro-media deixaria a maior revisao decidir o numero sozinha.

AS METRICAS QUE DECIDEM, e por que as quatro classicas nao bastam: a 0,8% de prevalencia,
um classificador que diz "nao" a tudo tem 99,2% de acuracia e especificidade 100%.

  AUC-ROC        livre de limiar — a comparacao primaria entre variantes e preditores
  WSS@95         Work Saved over Sampling a 95% de recall: quanto da triagem se poupa
                 ainda achando 95% dos incluidos. E a metrica corrente da area, e a
                 unica que responde "isso pouparia trabalho de verdade?"
  Recall@budget  recall tendo triado 10%, 20%, 50% dos registros
  Recall 100%    quanto da lista e preciso triar para achar TODOS. Numa revisao
                 sistematica, o estudo perdido e o erro que importa.

Sensibilidade, especificidade, precisao e F1 saem no limiar de operacao, escolhido
FORA-DA-DOBRA por leave-one-review-out (limiar.py).

    ./avaliar.py --fonte synergy --alvo ta
    ./avaliar.py --fonte synergy --alvo final --pergunta recuperar
"""

from __future__ import annotations

import argparse
import bisect
import math
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
BANCO = RAIZ / "resultados" / "respostas.sqlite"


# ------------------------------------------------------------------------- metricas

def auc(sc: list[float], rot: list[int]) -> float:
    """AUC-ROC por contagem de pares, com meio-ponto para empate."""
    pos = [s for s, r in zip(sc, rot) if r]
    neg = sorted(s for s, r in zip(sc, rot) if not r)
    if not pos or not neg:
        return float("nan")
    t = 0.0
    for p in pos:
        lo = bisect.bisect_left(neg, p)
        t += lo + (bisect.bisect_right(neg, p) - lo) / 2
    return t / (len(pos) * len(neg))


def _ordenado(sc: list[float], rot: list[int]) -> list[int]:
    """Rotulos na ordem decrescente de score — como o revisor triaria a lista."""
    return [r for _, r in sorted(zip(sc, rot), key=lambda x: -x[0])]


def recall_em(sc: list[float], rot: list[int], fracao: float) -> float:
    o = _ordenado(sc, rot)
    tot = sum(o)
    if not tot:
        return float("nan")
    k = max(1, int(round(len(o) * fracao)))
    return sum(o[:k]) / tot


def triagem_para_recall(sc: list[float], rot: list[int], alvo: float) -> float:
    """Fracao da lista que e preciso triar para atingir `alvo` de recall."""
    o = _ordenado(sc, rot)
    tot = sum(o)
    if not tot:
        return float("nan")
    precisa = math.ceil(alvo * tot)
    achados = 0
    for i, r in enumerate(o, 1):
        achados += r
        if achados >= precisa:
            return i / len(o)
    return 1.0


def wss(sc: list[float], rot: list[int], alvo: float = 0.95) -> float:
    """
    Work Saved over Sampling. `1 - fracao_triada` e o trabalho poupado bruto; subtrair
    `1 - alvo` desconta o que a amostragem aleatoria ja daria de graca.
    """
    f = triagem_para_recall(sc, rot, alvo)
    return float("nan") if math.isnan(f) else (1 - f) - (1 - alvo)


def confusao(sc: list[float], rot: list[int], limiar: float) -> tuple[int, int, int, int]:
    tp = sum(1 for s, r in zip(sc, rot) if s >= limiar and r)
    fp = sum(1 for s, r in zip(sc, rot) if s >= limiar and not r)
    fn = sum(1 for s, r in zip(sc, rot) if s < limiar and r)
    tn = sum(1 for s, r in zip(sc, rot) if s < limiar and not r)
    return tp, fp, fn, tn


def classicas(sc: list[float], rot: list[int], limiar: float) -> dict:
    tp, fp, fn, tn = confusao(sc, rot, limiar)
    sens = tp / (tp + fn) if tp + fn else float("nan")
    esp = tn / (tn + fp) if tn + fp else float("nan")
    prec = tp / (tp + fp) if tp + fp else float("nan")
    f1 = (2 * prec * sens / (prec + sens)
          if prec + sens and not math.isnan(prec) and not math.isnan(sens) else 0.0)
    return {"sensibilidade": sens, "especificidade": esp, "precisao": prec, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def limiar_para_recall(sc: list[float], rot: list[int], alvo: float = 0.95) -> float:
    """Maior limiar que ainda atinge `alvo` de sensibilidade."""
    pares = sorted(((s, r) for s, r in zip(sc, rot)), key=lambda x: -x[0])
    tot = sum(r for _, r in pares)
    if not tot:
        return 0.0
    precisa = math.ceil(alvo * tot)
    achados = 0
    for s, r in pares:
        achados += r
        if achados >= precisa:
            return s
    return 0.0


# --------------------------------------------------------------------------- leitura

def carregar(con: sqlite3.Connection, fonte: str, pergunta: str, variante: str,
             alvo: str, so_com_abstract: bool = False
             ) -> dict[str, tuple[list[float], list[int]]]:
    col = "rotulo_ta" if alvo == "ta" else "rotulo_final"
    # 61% do corpus nao tem abstract no OpenAlex, e o revisor humano quase certamente o
    # tinha. Restringir e a normalizacao que torna a medida comparavel ao julgamento dele.
    extra = " AND l.tem_abstract=1" if so_com_abstract else ""
    q = f"""SELECT p.review_id, p.valor, l.{col}
            FROM respostas p JOIN rotulos l
              ON p.fonte=l.fonte AND p.review_id=l.review_id AND p.record_id=l.record_id
            WHERE p.fonte=? AND p.pergunta=? AND p.variante=? AND l.{col} IS NOT NULL{extra}"""
    por: dict[str, tuple[list[float], list[int]]] = {}
    for rev, v, r in con.execute(q, (fonte, pergunta, variante)):
        s, t = por.setdefault(rev, ([], []))
        s.append(float(v)); t.append(int(r))
    return por


def combinar_facetas(con: sqlite3.Connection, fonte: str, variante: str, alvo: str,
                     modo: str = "min") -> dict[str, tuple[list[float], list[int]]]:
    """Agregado das 5 facetas como preditor concorrente do holistico."""
    from metapicker.triagem import FACETAS
    col = "rotulo_ta" if alvo == "ta" else "rotulo_final"
    q = f"""SELECT p.review_id, p.record_id, p.pergunta, p.valor, l.{col}
            FROM respostas p JOIN rotulos l
              ON p.fonte=l.fonte AND p.review_id=l.review_id AND p.record_id=l.record_id
            WHERE p.fonte=? AND p.variante=? AND l.{col} IS NOT NULL
              AND p.pergunta IN ({','.join('?' * len(FACETAS))})"""
    acc: dict[tuple[str, str], tuple[list[float], int]] = {}
    for rev, rec, _, v, r in con.execute(q, (fonte, variante, *FACETAS)):
        vs, _ = acc.setdefault((rev, rec), ([], int(r)))
        vs.append(float(v))
    por: dict[str, tuple[list[float], list[int]]] = {}
    for (rev, _), (vs, r) in acc.items():
        if len(vs) < len(FACETAS):
            continue
        v = min(vs) if modo == "min" else math.prod(vs)
        s, t = por.setdefault(rev, ([], []))
        s.append(v); t.append(r)
    return por


# ------------------------------------------------------------------------- relatorio

def relatar(por: dict, nome: str, limiar: float | None = None) -> list[str]:
    L = [f"── {nome} " + "─" * max(0, 62 - len(nome)), ""]
    L.append(f"{'revisão':28} {'n':>7} {'pos':>5} {'prev':>6} {'AUC':>6} "
             f"{'WSS@95':>7} {'R@10%':>6} {'R@20%':>6} {'100%':>6}")
    ag = {k: [] for k in ("auc", "wss", "r10", "r20", "t100")}
    for rev in sorted(por, key=lambda r: -len(por[r][0])):
        sc, rot = por[rev]
        n, pos = len(sc), sum(rot)
        if pos == 0 or pos == n:
            L.append(f"{rev[:28]:28} {n:7,} {pos:5}   — sem as duas classes"
                     .replace(",", "."))
            continue
        a, w = auc(sc, rot), wss(sc, rot)
        r10, r20 = recall_em(sc, rot, 0.10), recall_em(sc, rot, 0.20)
        t100 = triagem_para_recall(sc, rot, 1.0)
        for k, v in zip(ag, (a, w, r10, r20, t100)):
            if not math.isnan(v):
                ag[k].append(v)
        L.append(f"{rev[:28]:28} {n:7,} {pos:5} {pos/n:5.2%} {a:6.3f} "
                 f"{w:7.1%} {r10:6.0%} {r20:6.0%} {t100:6.0%}".replace(",", "."))

    def m(k):
        return sum(ag[k]) / len(ag[k]) if ag[k] else float("nan")
    L += ["", f"{'MACRO-MÉDIA':28} {len(ag['auc']):7} {'':5} {'':6} {m('auc'):6.3f} "
              f"{m('wss'):7.1%} {m('r10'):6.0%} {m('r20'):6.0%} {m('t100'):6.0%}", ""]

    if limiar is not None:
        tot = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
        sens, esps, precs, f1s = [], [], [], []
        for rev in por:
            sc, rot = por[rev]
            if not sum(rot):
                continue
            c = classicas(sc, rot, limiar)
            for k in tot:
                tot[k] += c[k]
            for lst, k in ((sens, "sensibilidade"), (esps, "especificidade"),
                           (precs, "precisao"), (f1s, "f1")):
                if not math.isnan(c[k]):
                    lst.append(c[k])
        med = lambda x: sum(x) / len(x) if x else float("nan")  # noqa: E731
        L += [f"no limiar {limiar:.3f} (macro-média entre revisões):",
              f"   sensibilidade {med(sens):6.1%}    especificidade {med(esps):6.1%}",
              f"   precisão      {med(precs):6.1%}    F1             {med(f1s):6.3f}",
              f"   totais: TP={tot['tp']} FP={tot['fp']} FN={tot['fn']} TN={tot['tn']}",
              ""]
    return L


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonte", default="synergy")
    ap.add_argument("--alvo", default="ta", choices=["ta", "final"])
    ap.add_argument("--variante", default="abstract")
    ap.add_argument("--pergunta", default="recuperar")
    ap.add_argument("--limiar", type=float, default=None)
    ap.add_argument("--facetas", action="store_true", help="também o agregado das facetas")
    ap.add_argument("--banco", type=Path, default=BANCO)
    ap.add_argument("--so-com-abstract", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(a.banco)
    alvo_nome = "passou triagem T/A" if a.alvo == "ta" else "incluído na síntese"
    L = ["=" * 78,
         f"JEV — triagem título/abstract · fonte={a.fonte} · variante={a.variante}",
         f"alvo: {alvo_nome}", "=" * 78, ""]

    por = carregar(con, a.fonte, a.pergunta, a.variante, a.alvo, a.so_com_abstract)
    if not por:
        print("nenhum dado para esses filtros", file=sys.stderr)
        return 1
    L += relatar(por, f"noul «{a.pergunta}» (holístico)", a.limiar)

    if a.facetas:
        for modo in ("min", "produto"):
            f = combinar_facetas(con, a.fonte, a.variante, a.alvo, modo)
            if f:
                L += relatar(f, f"agregado das facetas ({modo})", a.limiar)

    print("\n".join(L))
    return 0


if __name__ == "__main__":
    sys.exit(main())
