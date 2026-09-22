#!/usr/bin/env python3
"""
Limiar de operacao escolhido FORA-DA-DOBRA, por leave-one-review-out.

POR QUE ISSO NAO E OPCIONAL. Escolher o limiar que maximiza uma metrica e reportar a
metrica no MESMO conjunto infla o resultado sem avisar. O numero sai bonito, ninguem ve o
vazamento, e a conclusao e falsa. Aqui o limiar de cada revisao e ajustado nas OUTRAS, e
a diferenca entre o honesto e o inflado e reportada em pontos percentuais — se a
diferenca for grande, o limiar nao generaliza e o resultado nao vale.

A triagem e orientada a RECALL: perder um estudo e o erro que importa, e um falso
positivo custa apenas a leitura de um texto completo. Por isso o criterio padrao de ajuste
e "o maior limiar que ainda atinge 95% de sensibilidade", nao "o que maximiza acuracia".

    ./limiar.py --fonte synergy --alvo ta
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from metapicker import avaliar as A  # noqa: E402

BANCO = RAIZ / "resultados" / "respostas.sqlite"


def limiar_ajustado(treino: list[tuple[list[float], list[int]]],
                    alvo_recall: float = 0.95, quantil: float = 0.0) -> float:
    """
    Limiar de cada revisao de treino que atinge o recall alvo, agregado por QUANTIL.

    `quantil=0` e o minimo. Parecia o certo — preserva recall na revisao nova — e com 3
    revisoes custava 3,7 pp de especificidade. Com 21 custa 40,5 pp: o minimo e fixado
    pela revisao mais extrema do conjunto, entao quanto MAIS revisoes de treino, mais
    conservador o limiar fica. E uma regra que piora com dados, e por isso o quantil e
    parametro e o relatorio compara as duas.
    """
    ts = sorted(A.limiar_para_recall(sc, rot, alvo_recall)
                for sc, rot in treino if sum(rot))
    if not ts:
        return 0.5
    i = min(len(ts) - 1, max(0, int(round(quantil * (len(ts) - 1)))))
    return ts[i]


def fora_da_dobra(por: dict[str, tuple[list[float], list[int]]],
                  alvo_recall: float = 0.95, quantil: float = 0.0) -> dict:
    revs = [r for r in por if sum(por[r][1]) > 0]
    if len(revs) < 2:
        return {"erro": f"só {len(revs)} revisão(ões) com positivos — "
                        "leave-one-out precisa de ao menos 2"}
    linhas, sens_f, esp_f, sens_i, esp_i, limiares = [], [], [], [], [], []
    for r in revs:
        treino = [por[o] for o in revs if o != r]
        t_fora = limiar_ajustado(treino, alvo_recall, quantil)
        sc, rot = por[r]
        c_fora = A.classicas(sc, rot, t_fora)
        t_dentro = A.limiar_para_recall(sc, rot, alvo_recall)   # vazamento proposital
        c_dentro = A.classicas(sc, rot, t_dentro)
        linhas.append((r, len(sc), sum(rot), t_fora, c_fora, t_dentro, c_dentro))
        limiares.append(t_fora)
        for lst, v in ((sens_f, c_fora["sensibilidade"]), (esp_f, c_fora["especificidade"]),
                       (sens_i, c_dentro["sensibilidade"]), (esp_i, c_dentro["especificidade"])):
            if not math.isnan(v):
                lst.append(v)
    med = lambda x: sum(x) / len(x) if x else float("nan")  # noqa: E731
    return {"linhas": linhas, "limiares": limiares,
            "sens_fora": med(sens_f), "esp_fora": med(esp_f),
            "sens_dentro": med(sens_i), "esp_dentro": med(esp_i)}


def relatar(res: dict, alvo_recall: float) -> list[str]:
    if "erro" in res:
        return [f"  {res['erro']}"]
    L = [f"{'revisão':28} {'n':>7} {'pos':>5} {'limiar':>7} {'sens':>7} {'espec':>7} "
         f"{'FN':>4}",
         "  (limiar ajustado nas OUTRAS revisões — nunca na própria)"]
    for r, n, pos, t, c, _, _ in sorted(res["linhas"], key=lambda x: -x[1]):
        L.append(f"{r[:28]:28} {n:7,} {pos:5} {t:7.3f} {c['sensibilidade']:7.1%} "
                 f"{c['especificidade']:7.1%} {c['fn']:4}".replace(",", "."))
    d_sens = (res["sens_dentro"] - res["sens_fora"]) * 100
    d_esp = (res["esp_dentro"] - res["esp_fora"]) * 100
    L += ["",
          f"  FORA-DA-DOBRA (honesto)   sensibilidade {res['sens_fora']:6.1%}   "
          f"especificidade {res['esp_fora']:6.1%}",
          f"  ajustado nos próprios dados  sensibilidade {res['sens_dentro']:6.1%}   "
          f"especificidade {res['esp_dentro']:6.1%}",
          f"  vazamento evitado: {d_sens:+.1f} pp de sensibilidade, "
          f"{d_esp:+.1f} pp de especificidade",
          "",
          f"  alvo de ajuste: {alvo_recall:.0%} de sensibilidade. Se a sensibilidade",
          f"  fora-da-dobra ficar bem abaixo disso, o limiar não generaliza entre",
          f"  revisões — e um limiar único para todas não serve.", ""]
    return L


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonte", default="synergy")
    ap.add_argument("--alvo", default="ta", choices=["ta", "final"])
    ap.add_argument("--variante", default="abstract")
    ap.add_argument("--pergunta", default="recuperar")
    ap.add_argument("--recall", type=float, default=0.95)
    ap.add_argument("--quantil", type=float, default=0.0,
                    help="0=mínimo (conservador), 0.1=10º percentil, 0.5=mediana")
    ap.add_argument("--banco", type=Path, default=BANCO)
    a = ap.parse_args()

    con = sqlite3.connect(a.banco)
    por = A.carregar(con, a.fonte, a.pergunta, a.variante, a.alvo)
    if not por:
        print("nenhum dado para esses filtros", file=sys.stderr)
        return 1
    print("=" * 78)
    print(f"LIMIAR FORA-DA-DOBRA · {a.fonte} · «{a.pergunta}» · alvo="
          + ("passou T/A" if a.alvo == "ta" else "incluído na síntese"))
    print("=" * 78)
    print("\n".join(relatar(fora_da_dobra(por, a.recall, a.quantil), a.recall)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
