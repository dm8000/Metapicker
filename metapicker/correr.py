#!/usr/bin/env python3
"""
Roda o JEV sobre um conjunto de triagem e grava as PROBABILIDADES CRUAS em SQLite.

TRES EXIGENCIAS, cada uma por um motivo concreto:

  1. RETOMAVEL. Chave unica (fonte, review_id, record_id, pergunta) e INSERT OR IGNORE.
     Dezenas de milhares de chamadas encontram falha transitoria; rodar de novo continua
     de onde parou em vez de pagar tudo outra vez.
  2. PROBABILIDADE, NUNCA BOOLEANO. Todo limiar, toda metrica e toda re-analise saem
     daqui sem gastar de novo. Gravar `eligible: true` joga fora exatamente a informacao
     que torna o experimento re-executavel.
  3. PARADA POR ORCAMENTO. `--orcamento` corta a rodada quando o custo acumulado passa do
     teto. Com credito limitado, um laco sem freio gasta tudo antes de alguem olhar.

    ./correr.py --fonte synergy --revisoes Donners_2021 Sep_2021 --orcamento 0.10
    ./correr.py --fonte synergy --orcamento 0.70        # ate o teto, retomavel
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from metapicker import jev, triagem  # noqa: E402

BANCO = RAIZ / "resultados" / "respostas.sqlite"

# Qual opcao de cada pergunta `choice` conta como positiva.
POSITIVO = {"escolha": "retrieve"}

ESQUEMA = """
CREATE TABLE IF NOT EXISTS respostas (
    fonte      TEXT NOT NULL,
    review_id  TEXT NOT NULL,
    record_id  TEXT NOT NULL,
    variante   TEXT NOT NULL,      -- 'abstract' | 'fulltext' | 'criterios_brutos' ...
    pergunta   TEXT NOT NULL,
    valor      REAL NOT NULL,      -- probabilidade 0..1, NUNCA booleano
    PRIMARY KEY (fonte, review_id, record_id, variante, pergunta)
);
CREATE TABLE IF NOT EXISTS chamadas (
    fonte     TEXT, review_id TEXT, record_id TEXT, variante TEXT,
    tokens    INTEGER, segundos REAL, erro TEXT, quando REAL,
    PRIMARY KEY (fonte, review_id, record_id, variante)
);
CREATE TABLE IF NOT EXISTS rotulos (
    fonte     TEXT, review_id TEXT, record_id TEXT,
    rotulo_ta INTEGER, rotulo_final INTEGER, tem_abstract INTEGER,
    PRIMARY KEY (fonte, review_id, record_id)
);
CREATE INDEX IF NOT EXISTS ix_resp ON respostas (fonte, review_id, pergunta);
"""


def conectar(caminho: Path = BANCO) -> sqlite3.Connection:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(caminho, timeout=60)
    c.executescript(ESQUEMA)
    return c


def ja_feitos(con: sqlite3.Connection, fonte: str, variante: str) -> set[tuple[str, str]]:
    return {(r, d) for r, d in con.execute(
        "SELECT DISTINCT review_id, record_id FROM chamadas "
        "WHERE fonte=? AND variante=? AND erro IS NULL", (fonte, variante))}


def correr(con: sqlite3.Connection, fonte: str, revisoes: dict, registros: list,
           *, variante: str = "abstract", perguntas: dict | None = None,
           orcamento: float = 0.0, paralelo: int = jev.PARALELO,
           lote: int = 200) -> dict:
    """
    `revisoes`: {review_id: dict da revisao}. `registros`: lista de dicts do registro
    unificado. Grava rotulos junto, para o avaliador nao depender da carga original.
    """
    perguntas = perguntas or triagem.PERGUNTAS
    feitos = ja_feitos(con, fonte, variante)
    pend = [r for r in registros if (r["review_id"], r["record_id"]) not in feitos]

    print(f"  {len(registros):,} registros · {len(feitos):,} já feitos · "
          f"{len(pend):,} pendentes".replace(",", "."), file=sys.stderr)
    if not pend:
        return {"novos": 0, "pulados": len(feitos)}

    custo0 = jev.CONTA.custo
    t0 = time.time()
    novos = parados = 0

    for ini in range(0, len(pend), lote):
        if orcamento and (jev.CONTA.custo - custo0) >= orcamento:
            parados = len(pend) - ini
            print(f"\n  PARADA POR ORÇAMENTO: US$ {jev.CONTA.custo - custo0:.4f} "
                  f"≥ US$ {orcamento:.2f}. {parados:,} registros não rodados."
                  .replace(",", "."), file=sys.stderr)
            break

        bloco = pend[ini:ini + lote]
        trabalhos = [(triagem.montar_estado(revisoes[r["review_id"]], r), perguntas)
                     for r in bloco]
        rs = jev.em_lote(trabalhos, paralelo=paralelo)

        linhas_r, linhas_c, linhas_l = [], [], []
        for reg, resp in zip(bloco, rs):
            chave = (fonte, reg["review_id"], reg["record_id"], variante)
            if resp is None:
                linhas_c.append((*chave, 0, 0.0, "falhou", time.time()))
                continue
            for q in perguntas:
                try:
                    v = jev.obter(resp, q)
                except jev.ErroJev:
                    continue
                if isinstance(v, tuple):
                    # `choice` devolve (opcao, confianca). Grava-se a DECISAO como 1/0 —
                    # a confianca nao entra, porque o ponto de usar `choice` e nao haver
                    # numero continuo que alguem thresholdeie depois.
                    v = 1.0 if v[0] == POSITIVO.get(q, "retrieve") else 0.0
                linhas_r.append((*chave[:3], variante, q, float(v)))
            linhas_c.append((*chave, int((resp.get("usage") or {}).get("input_tokens") or 0),
                             0.0, None, time.time()))
            linhas_l.append((fonte, reg["review_id"], reg["record_id"],
                             reg.get("rotulo_ta"), reg["rotulo_final"],
                             1 if (reg.get("abstract") or "").strip() else 0))
            novos += 1

        con.executemany("INSERT OR IGNORE INTO respostas VALUES (?,?,?,?,?,?)", linhas_r)
        con.executemany("INSERT OR IGNORE INTO chamadas  VALUES (?,?,?,?,?,?,?,?)", linhas_c)
        con.executemany("INSERT OR IGNORE INTO rotulos   VALUES (?,?,?,?,?,?)", linhas_l)
        con.commit()

        feito = min(ini + lote, len(pend))
        sys.stderr.write(
            f"\r  {feito:,}/{len(pend):,}".replace(",", ".")
            + f" · US$ {jev.CONTA.custo - custo0:.4f} · {(time.time()-t0)/60:.1f} min   ")
    sys.stderr.write("\n")
    return {"novos": novos, "pulados": len(feitos), "nao_rodados": parados,
            "custo": jev.CONTA.custo - custo0, "minutos": (time.time() - t0) / 60}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonte", default="synergy", choices=["synergy", "metasyn", "chan"])
    ap.add_argument("--revisoes", nargs="*", help="ids; vazio = todas")
    ap.add_argument("--limite", type=int, default=0, help="máximo de registros por revisão")
    ap.add_argument("--orcamento", type=float, default=0.0, help="teto em US$; 0 = sem teto")
    ap.add_argument("--so-holistico", action="store_true",
                    help="só a pergunta `recuperar` (~30%% menos tokens)")
    ap.add_argument("--escolha", action="store_true",
                    help="primitiva `choice`: decisão binária NATIVA, sem limiar escolhido "
                         "por nós")
    ap.add_argument("--variante", default="abstract")
    ap.add_argument("--paralelo", type=int, default=jev.PARALELO)
    a = ap.parse_args()

    if a.fonte == "synergy":
        from metapicker.fontes import synergy
        registros, revs = synergy.carregar()
    else:
        print(f"fonte '{a.fonte}' ainda não implementada", file=sys.stderr)
        return 1

    revisoes = {v.review_id: vars(v) for v in revs}
    regs = [vars(r) for r in registros]
    if a.revisoes:
        regs = [r for r in regs if r["review_id"] in set(a.revisoes)]
    if a.limite:
        por: dict[str, int] = {}
        corte = []
        for r in regs:
            k = r["review_id"]
            if por.get(k, 0) < a.limite:
                corte.append(r); por[k] = por.get(k, 0) + 1
        regs = corte
    if not regs:
        print("nenhum registro selecionado", file=sys.stderr)
        return 1

    perguntas = ({"escolha": triagem.ESCOLHA} if a.escolha
                 else triagem.so_holistico() if a.so_holistico
                 else triagem.PERGUNTAS)
    con = conectar()
    r = correr(con, a.fonte, revisoes, regs, variante=a.variante, perguntas=perguntas,
               orcamento=a.orcamento, paralelo=a.paralelo)

    print(f"\n  novos {r['novos']:,}".replace(",", ".")
          + f" · pulados {r['pulados']:,}".replace(",", ".")
          + (f" · NÃO RODADOS {r['nao_rodados']:,}".replace(",", ".")
             if r.get("nao_rodados") else ""))
    for l in jev.CONTA.linhas():
        print(l)
    return 0


if __name__ == "__main__":
    sys.exit(main())
