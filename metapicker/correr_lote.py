#!/usr/bin/env python3
"""
JEV em LOTE: N registros por chamada, uma pergunta `choice` por registro.

POR QUE ISTO EXISTE. A rodada original mandava UM registro por chamada, seguindo o
exemplo de fan-out da documentacao — que e um item com muitas perguntas. Medido: 88% da
conta do JEV era cabecalho repetido (criterios 460.628 tokens, instrucoes e estrutura
~469.000) contra 99.724 tokens dos registros em si. Erro de desenho, nao limitacao da API.

O `state` aceita array e as perguntas sao avaliadas em paralelo contra ele, entao uma
pergunta pode endereçar o item `n`. A instrucao da tarefa vive no estado UMA vez; cada
pergunta fica minima.

O RISCO E CONTAMINACAO ENTRE ITENS: com 100 registros no array, ancorar "considerando
apenas o registro n" e mais dificil do que com 5. Por isso este script grava numa variante
propria, para o julgamento em lote poder ser comparado item a item com o um-a-um que ja
esta no banco.

    ./correr_lote.py --por-lote 100
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from metapicker import correr, jev  # noqa: E402
from metapicker.fontes import synergy  # noqa: E402

COMP = ["Theobald_2021", "Hanlon_2022", "Deckers_2022"]

INSTRUCAO = (
    "Screening titles and abstracts for a systematic review. For each record in the "
    "records array, decide whether it should be retrieved for full-text assessment "
    "against the eligibility criteria. Screening is recall-oriented: excluding requires "
    "evidence, including does not. Many records have no abstract; absence of information "
    "is not grounds for exclusion. Judge each record on its own, independently of the "
    "others.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--revisoes", nargs="*", default=COMP)
    ap.add_argument("--por-lote", type=int, default=100)
    ap.add_argument("--variante", default="lote")
    ap.add_argument("--orcamento", type=float, default=0.05)
    a = ap.parse_args()

    con = correr.conectar()
    feitos = {(r, d) for r, d in con.execute(
        "SELECT DISTINCT review_id, record_id FROM respostas WHERE fonte='synergy' "
        "AND variante=?", (a.variante,))}
    t0 = time.time()
    custo0 = jev.CONTA.custo
    total = 0

    for rev_id in a.revisoes:
        regs, revs = synergy.carregar([rev_id])
        pend = [r for r in regs if (rev_id, r.record_id) not in feitos]
        if not pend:
            print(f"  {rev_id}: já feito", file=sys.stderr); continue
        rev = vars(revs[0])
        blocos = [pend[i:i + a.por_lote] for i in range(0, len(pend), a.por_lote)]

        trabalhos = []
        for bloco in blocos:
            estado = {
                "task": INSTRUCAO,
                "review": rev.get("titulo") or rev_id,
                "eligibility_criteria": rev.get("criterios_brutos") or "",
                "records": [{"n": i,
                             "title": (r.titulo or "").strip(),
                             "abstract": (r.abstract or "").strip()
                                         or "(no abstract available for this record)"}
                            for i, r in enumerate(bloco, 1)],
            }
            perguntas = {f"r{i}": jev.choice(
                f"Record {i} in the records array.",
                {"retrieve": "retrieve this record for full-text assessment",
                 "exclude": "discard this record on title and abstract alone"})
                for i in range(1, len(bloco) + 1)}
            trabalhos.append((estado, perguntas))

        rs = jev.em_lote(trabalhos, paralelo=4)
        linhas = []
        for bloco, resp in zip(blocos, rs):
            if resp is None:
                continue
            for i, r in enumerate(bloco, 1):
                try:
                    op, _ = jev.obter(resp, f"r{i}")
                except jev.ErroJev:
                    continue
                linhas.append(("synergy", rev_id, r.record_id, a.variante, "escolha",
                               1.0 if op == "retrieve" else 0.0))
        con.executemany("INSERT OR IGNORE INTO respostas VALUES (?,?,?,?,?,?)", linhas)
        con.commit()
        total += len(linhas)
        print(f"  {rev_id}: {len(linhas)}/{len(pend)} em {len(blocos)} chamadas · "
              f"US$ {jev.CONTA.custo - custo0:.4f}", file=sys.stderr)
        if a.orcamento and jev.CONTA.custo - custo0 >= a.orcamento:
            print("  PARADA POR ORÇAMENTO", file=sys.stderr); break

    print(f"\n  {total:,} registros · {(time.time()-t0)/60:.1f} min".replace(",", "."))
    for l in jev.CONTA.linhas():
        print(l)
    if jev.CONTA.chamadas:
        print(f"  {jev.CONTA.entrada/max(1,total):.0f} tokens por registro "
              f"(um-a-um custou 979)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
