#!/usr/bin/env python3
"""
Comparacao de 3 vias — JEV x Qwen reranker x DeepSeek — na MESMA decisao binaria.

Por que binaria: o DeepSeek pela janela do chat so sabe responder sim/nao. Forcar os tres
ao denominador comum e o enquadramento justo, e o custo esta declarado — a ordenacao era
onde o JEV era forte (AUC 0,920), e a decisao binaria descarta essa vantagem.

COMO CADA UM VIRA SIM/NAO, e o cuidado em cada caso:

  DeepSeek   ja e binario.
  JEV        primitiva `choice`: a decisao e do modelo, nao de um limiar meu.
  Qwen       so produz score. Limiar ajustado nas revisoes FORA do conjunto de comparacao
             — conjunto inteiramente disjunto, nao leave-one-out.
  JEV-noul   quarta coluna, thresholdeada como o Qwen. Nao e redundancia: medida contra o
             `choice`, ela diz quanto daquele limiar era arbitrio nosso.

    ./comparar.py --alvo ta
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from metapicker import avaliar as A, limiar as L  # noqa: E402

BANCO = RAIZ / "resultados" / "respostas.sqlite"
COMPARACAO = ["Theobald_2021", "Hanlon_2022", "Deckers_2022"]


def limiar_de_fora(con, pergunta: str, variante: str, alvo: str,
                   excluir: list[str]) -> float:
    """Ajusta nas revisoes que NAO estao no conjunto de comparacao."""
    por = A.carregar(con, "synergy", pergunta, variante, alvo)
    fora = [por[r] for r in por if r not in excluir and sum(por[r][1])]
    if not fora:
        raise SystemExit(f"sem revisões de fora para ajustar «{pergunta}»")
    return L.limiar_ajustado(fora, 0.95, 0.10), len(fora)


def decisoes(con, alvo: str, so_com_abstract: bool = False) -> tuple[dict, dict]:
    """
    {modelo: {(rev, rec): 0|1}} e {(rev, rec): rotulo}.

    `so_com_abstract` restringe aos registros que TEM abstract. E a normalizacao que torna
    a comparacao legitima: a premissa e que os autores triaram por titulo e abstract, mas
    74% dos registros que eles aprovaram chegam ao modelo so com o titulo — o OpenAlex nao
    tem esses abstracts hoje, e o revisor quase certamente os tinha em 2021. Comparar na
    fatia completa mede quem adivinha melhor a partir de um titulo, nao quem tria melhor.
    """
    col = "rotulo_ta" if alvo == "ta" else "rotulo_final"
    t_noul, n_noul = limiar_de_fora(con, "recuperar", "abstract", alvo, COMPARACAO)
    t_qwen, n_qwen = limiar_de_fora(con, "relevancia", "qwen", alvo, COMPARACAO)
    print(f"  limiar do JEV-noul  {t_noul:.3f}  (ajustado em {n_noul} revisões de fora)")
    print(f"  limiar do Qwen      {t_qwen:.4f}  (ajustado em {n_qwen} revisões de fora)")

    fontes = {"JEV (choice)":   ("escolha", "escolha", None),
              "JEV (noul→lim)": ("recuperar", "abstract", t_noul),
              "Qwen (rerank)":  ("relevancia", "qwen", t_qwen),
              "DeepSeek":       ("entra", "deepseek", None)}
    mods: dict[str, dict] = {}
    for nome, (perg, var, lim) in fontes.items():
        d = {}
        for rev, rec, v in con.execute(
                "SELECT review_id, record_id, valor FROM respostas WHERE fonte='synergy' "
                "AND pergunta=? AND variante=?", (perg, var)):
            if rev in COMPARACAO:
                d[(rev, rec)] = int(v >= lim) if lim is not None else int(v)
        mods[nome] = d
    filtro = " AND tem_abstract=1" if so_com_abstract else ""
    rot = {(r, d): v for r, d, v in con.execute(
        f"SELECT review_id, record_id, {col} FROM rotulos WHERE fonte='synergy' "
        f"AND {col} IS NOT NULL{filtro}") if r in COMPARACAO}
    return mods, rot


def metricas(dec: dict, rot: dict, chaves: list) -> dict:
    tp = sum(1 for k in chaves if dec[k] and rot[k])
    fp = sum(1 for k in chaves if dec[k] and not rot[k])
    fn = sum(1 for k in chaves if not dec[k] and rot[k])
    tn = len(chaves) - tp - fp - fn
    n = len(chaves)
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    f1 = 2 * prec * rec / (prec + rec) if prec + rec and prec == prec and rec == rec else 0.0
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (tn + fn) * (tn + fp)) / n ** 2
    return {"prec": prec, "rec": rec, "f1": f1, "acordo": po,
            "kappa": (po - pe) / (1 - pe) if pe < 1 else 0.0,
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "escolheu": tp + fp}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--alvo", default="ta", choices=["ta", "final"])
    ap.add_argument("--banco", type=Path, default=BANCO)
    ap.add_argument("--so-com-abstract", action="store_true",
                    help="normaliza: só registros que TÊM abstract, onde a premissa «os "
                         "autores triaram por título e abstract» se sustenta")
    a = ap.parse_args()
    con = sqlite3.connect(a.banco)

    print("=" * 84)
    print("COMPARAÇÃO DOS MODELOS — decisão binária · alvo: "
          + ("passou triagem T/A" if a.alvo == "ta" else "incluído na síntese"))
    if a.so_com_abstract:
        print("NORMALIZADO: só registros COM abstract — onde os dois lados tinham a "
              "mesma informação")
    print("=" * 84)
    mods, rot = decisoes(con, a.alvo, a.so_com_abstract)

    faltando = [m for m, d in mods.items() if not d]
    if faltando:
        print(f"\n  AINDA SEM DADOS: {', '.join(faltando)}")
    mods = {m: d for m, d in mods.items() if d}
    if not mods:
        return 1

    # So os registros que TODOS julgaram — comparar em conjuntos diferentes nao compara.
    comum = sorted(set.intersection(*(set(d) for d in mods.values())) & set(rot))
    print(f"\n  {len(comum):,} registros julgados por todos os {len(mods)} "
          f"({len(set(r for r, _ in comum))} revisões)".replace(",", "."))
    if not comum:
        return 1

    print("\n" + "─" * 84)
    print("CONTRA A VERDADE DE REFERÊNCIA")
    print("─" * 84)
    for rev in COMPARACAO:
        ks = [k for k in comum if k[0] == rev]
        if not ks:
            continue
        pos = sum(rot[k] for k in ks)
        print(f"\n{rev}   n={len(ks)}   positivos={pos} ({pos/len(ks):.1%})")
        print(f"  {'modelo':16} {'escolheu':>9} {'precisão':>9} {'recall':>8} "
              f"{'F1':>6} {'acordo':>8} {'kappa':>7}")
        for m, d in mods.items():
            x = metricas(d, rot, ks)
            print(f"  {m:16} {x['escolheu']:9} {x['prec']:8.1%} {x['rec']:7.1%} "
                  f"{x['f1']:6.3f} {x['acordo']:7.1%} {x['kappa']:7.3f}")

    print(f"\n{'MACRO-MÉDIA entre as revisões':^84}")
    print(f"  {'modelo':16} {'precisão':>9} {'recall':>8} {'F1':>6} {'acordo':>8} "
          f"{'kappa':>7}")
    revs = [r for r in COMPARACAO if any(k[0] == r for k in comum)]
    for m, d in mods.items():
        xs = [metricas(d, rot, [k for k in comum if k[0] == r]) for r in revs]
        md = lambda c: sum(x[c] for x in xs if x[c] == x[c]) / len(xs)  # noqa: E731
        print(f"  {m:16} {md('prec'):8.1%} {md('rec'):7.1%} {md('f1'):6.3f} "
              f"{md('acordo'):7.1%} {md('kappa'):7.3f}")

    print("\n" + "─" * 84)
    print("ENTRE OS MODELOS — concordam entre si, independentemente de acertar?")
    print("─" * 84)
    nomes = list(mods)
    for i, x in enumerate(nomes):
        for y in nomes[i + 1:]:
            ac = sum(mods[x][k] == mods[y][k] for k in comum) / len(comum)
            px, py = (sum(mods[n][k] for k in comum) / len(comum) for n in (x, y))
            pe = px * py + (1 - px) * (1 - py)
            print(f"  {x:16} × {y:16} acordo {ac:6.1%}   kappa "
                  f"{((ac - pe) / (1 - pe) if pe < 1 else 0):6.3f}")

    print("\n" + "─" * 84)
    print("ONDE ESTÁ A DIFERENÇA")
    print("─" * 84)
    todos_sim = [k for k in comum if all(mods[m][k] for m in nomes)]
    todos_nao = [k for k in comum if not any(mods[m][k] for m in nomes)]
    pos = [k for k in comum if rot[k]]
    print(f"  os {len(nomes)} dizem SIM:  {len(todos_sim):5} registros "
          f"({sum(rot[k] for k in todos_sim)} eram positivos)")
    print(f"  os {len(nomes)} dizem NÃO:  {len(todos_nao):5} registros "
          f"({sum(rot[k] for k in todos_nao)} eram positivos — PERDIDOS por todos)")
    print(f"\n  positivos que só UM encontrou (é aqui que os modelos diferem):")
    for m in nomes:
        so = [k for k in pos if mods[m][k] and not any(mods[o][k] for o in nomes if o != m)]
        print(f"    {m:16} {len(so):4}")

    # Voto majoritario, de graca
    if len(nomes) >= 3:
        voto = {k: int(sum(mods[m][k] for m in nomes) * 2 > len(nomes)) for k in comum}
        xs = [metricas(voto, rot, [k for k in comum if k[0] == r]) for r in revs]
        md = lambda c: sum(x[c] for x in xs if x[c] == x[c]) / len(xs)  # noqa: E731
        print(f"\n  voto majoritário dos {len(nomes)}:  precisão {md('prec'):.1%} · "
              f"recall {md('rec'):.1%} · F1 {md('f1'):.3f} · kappa {md('kappa'):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
