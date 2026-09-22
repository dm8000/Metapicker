#!/usr/bin/env python3
"""
Roda o Qwen3-Reranker nas revisoes e grava os scores CRUS no mesmo SQLite do JEV.

Roda nas 21, e nao so nas 3 do conjunto de comparacao, por dois motivos: da o conjunto de
ajuste do limiar (as 18 de fora) e entrega a comparacao JEV x Qwen sobre o benchmark
completo. E local e gratis — o unico custo e tempo de parede.

    ./correr_qwen.py              # as 21
    ./correr_qwen.py --revisoes Deckers_2022
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from metapicker import correr, qwen  # noqa: E402
from metapicker.fontes import synergy  # noqa: E402

# As 21 do benchmark do JEV — o mesmo conjunto, para a comparacao ser sobre os mesmos
# registros e nao sobre amostras diferentes.
AS_21 = """Hanlon_2022 Brons_2024 Theobald_2021 Kapuka_2021 Quevedo_2023 Zinsser_2022
Deckers_2022 Oliveira_2021 Toffalini_2021 Abgaz_2023 van_der_Valk_2021 Clark_2021
Wijnen_2024 Sanchez-Acedo_2023 Maciel_2024 Noetel_2021 Sanchez-Gomez_2024 Fong_2021
Tumkaya_2018 Cinquin_2018 Mejean_2024""".split()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--revisoes", nargs="*", default=AS_21)
    ap.add_argument("--bloco", type=int, default=64,
                    help="documentos por requisicao; o servidor tem 8 slots de 4096 tok")
    ap.add_argument("--paralelo", type=int, default=qwen.PARALELO)
    a = ap.parse_args()

    con = correr.conectar()
    feitos = {(r, d) for r, d in con.execute(
        "SELECT DISTINCT review_id, record_id FROM respostas "
        "WHERE fonte='synergy' AND variante='qwen'")}
    t0 = time.time()
    total = 0

    for n, rev_id in enumerate(a.revisoes, 1):
        regs, revs = synergy.carregar([rev_id])
        if not revs:
            print(f"  [{n}/{len(a.revisoes)}] {rev_id}: ausente", file=sys.stderr); continue
        pend = [r for r in regs if (rev_id, r.record_id) not in feitos]
        if not pend:
            print(f"  [{n}/{len(a.revisoes)}] {rev_id}: já feito", file=sys.stderr); continue
        q = qwen.consulta(vars(revs[0]))
        blocos = [pend[i:i + a.bloco] for i in range(0, len(pend), a.bloco)]
        trabalhos = [(q, [qwen.documento(r.titulo, r.abstract) for r in b], rev_id)
                     for b in blocos]
        rs = qwen.em_lote(trabalhos, paralelo=a.paralelo)

        linhas, rot = [], []
        for bloco, scores in zip(blocos, rs):
            if scores is None:
                continue
            for r, s in zip(bloco, scores):
                linhas.append(("synergy", rev_id, r.record_id, "qwen", "relevancia",
                               float(s)))
                rot.append(("synergy", rev_id, r.record_id, r.rotulo_ta, r.rotulo_final,
                            1 if (r.abstract or "").strip() else 0))
        con.executemany("INSERT OR IGNORE INTO respostas VALUES (?,?,?,?,?,?)", linhas)
        con.executemany("INSERT OR IGNORE INTO rotulos VALUES (?,?,?,?,?,?)", rot)
        con.commit()
        total += len(linhas)
        print(f"  [{n}/{len(a.revisoes)}] {rev_id}: {len(linhas):,} · "
              f"{(time.time()-t0)/60:.1f} min".replace(",", "."), file=sys.stderr)

    print(f"\n  {total:,} scores gravados em {(time.time()-t0)/60:.1f} min"
          .replace(",", "."))
    for l in qwen.CONTA.linhas():
        print(l)
    if qwen.TRUNCOU:
        print(f"\n  ATENÇÃO — truncou em {qwen.TRUNCOU}: a comparação nessas revisões")
        print("  está comprometida, o Qwen recebeu menos critério que o JEV.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
