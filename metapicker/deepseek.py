#!/usr/bin/env python3
"""
DeepSeek pela janela do chat: gera lotes prontos para colar, e importa a resposta de volta.

POR QUE FATIADO. Nenhuma revisao cabe numa colagem so — a menor (Hanlon_2022, 206
registros) da 40k tokens. 100 registros por lote ficam em ~20k, folgados para a janela do
chat, e a resposta sao 100 linhas curtas.

O PROMPT CARREGA A REGRA ASSIMETRICA: excluir exige evidencia, incluir nao. E a mesma
regra que as facetas do JEV usam e que o `choice` vai usar. Sem ela o DeepSeek esta
jogando outro jogo — julgando elegibilidade final em vez de triagem — e a comparacao dos
tres nao mede a mesma coisa.

A VALIDACAO DA IMPORTACAO E O QUE PROTEGE O BENCHMARK. A falha provavel aqui nao e um erro
ruidoso, e uma resposta truncada que parece completa: o modelo responde 60 das 100 linhas,
o arquivo parece legitimo, e 40 registros entram como ausentes sem ninguem notar. Por isso
nada entra parcial — ou o lote inteiro e valido, ou e recusado com o motivo.

    ./deepseek.py gerar
    ./deepseek.py importar --lote Hanlon_2022-lote-1
    ./deepseek.py estado
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from metapicker.fontes import synergy  # noqa: E402

SAIDA = RAIZ / "resultados" / "deepseek"
BANCO = RAIZ / "resultados" / "respostas.sqlite"

# As tres revisoes do conjunto de comparacao, escolhidas por CONTRASTE no desempenho do
# JEV: melhor caso, segundo melhor, segundo pior. Se os tres modelos concordarem no
# Theobald e divergirem no Deckers, a dificuldade e da revisao; se um salvar o Deckers, e
# diferenca de modelo.
REVISOES = ["Theobald_2021", "Hanlon_2022", "Deckers_2022"]
POR_LOTE = 100

CABECALHO = """\
# Triagem de revisão sistemática — {rev}, lote {n} de {tot}

Você é um revisor fazendo **triagem de título e abstract** para a revisão sistemática
abaixo. Para cada registro, decida se ele deve ser **recuperado para leitura de texto
completo**.

Esta não é a decisão final de elegibilidade. A pergunta é: vale a pena buscar o artigo
inteiro para avaliá-lo?

**Regra da triagem — importante:** excluir exige evidência; incluir não. Se o título e o
abstract não trazem informação suficiente para descartar o registro com segurança, o
veredicto é **SIM**. Muitos registros não têm abstract; a ausência de informação não é
motivo de exclusão.

---

## Revisão

{titulo}

## Critérios de elegibilidade

{criterios}

---

## Registros ({qtd})

"""

RODAPE = """
---

## Formato da resposta

Responda **apenas** com uma linha por registro, exatamente neste formato:

```
1: SIM
2: NAO
3: SIM
```

Regras da resposta:
- exatamente **{qtd} linhas**, numeradas de **1 a {qtd}**, em ordem;
- só `SIM` ou `NAO`;
- **nenhum** texto antes, depois, ou entre as linhas — sem explicação, sem resumo.
"""


def _registros_da_revisao(rev_id: str):
    regs, revs = synergy.carregar([rev_id])
    return regs, vars(revs[0])


def gerar(revisoes: list[str], por_lote: int) -> None:
    SAIDA.mkdir(parents=True, exist_ok=True)
    total_lotes = total_regs = 0
    for rev_id in revisoes:
        regs, rev = _registros_da_revisao(rev_id)
        lotes = [regs[i:i + por_lote] for i in range(0, len(regs), por_lote)]
        for n, bloco in enumerate(lotes, 1):
            corpo = [CABECALHO.format(
                rev=rev_id, n=n, tot=len(lotes),
                titulo=rev["titulo"] or rev_id,
                criterios=rev["criterios_brutos"] or "(não informados na fonte)",
                qtd=len(bloco))]
            for i, r in enumerate(bloco, 1):
                a = (r.abstract or "").strip() or "(sem abstract)"
                corpo.append(f"**[{i}]** {r.titulo or '(sem título)'}\n\n{a}\n")
            corpo.append(RODAPE.format(qtd=len(bloco)))

            base = SAIDA / f"{rev_id}-lote-{n}"
            base.with_suffix(".md").write_text("\n".join(corpo), encoding="utf-8")
            base.with_suffix(".json").write_text(json.dumps({
                "review_id": rev_id, "lote": n, "de": len(lotes),
                "mapa": {str(i): r.record_id for i, r in enumerate(bloco, 1)},
            }, ensure_ascii=False, indent=1), encoding="utf-8")
            tok = len(base.with_suffix(".md").read_text()) // 4
            print(f"  {base.name}.md   {len(bloco):3} registros · ~{tok:,} tokens"
                  .replace(",", "."))
            total_lotes += 1; total_regs += len(bloco)
    print(f"\n  {total_lotes} lotes · {total_regs:,} registros · em {SAIDA}"
          .replace(",", "."))
    print(f"  responda cada um salvando a resposta em <nome>.resposta.txt")


LINHA = re.compile(r"^\s*\[?(\d+)\]?\s*[:.\)\-]\s*(SIM|NAO|NÃO|YES|NO|S|N)\s*$",
                   re.IGNORECASE)


def analisar(texto: str, esperados: int) -> dict[int, int]:
    """
    Converte a resposta colada em {numero: 0|1}. LEVANTA em qualquer inconsistencia — a
    falha que importa aqui e silenciosa, entao nada entra parcial.
    """
    vistos: dict[int, int] = {}
    ruins: list[str] = []
    for linha in texto.splitlines():
        if not linha.strip() or linha.strip().startswith("```"):
            continue
        m = LINHA.match(linha)
        if not m:
            ruins.append(linha.strip()[:60]); continue
        num = int(m.group(1))
        val = 0 if m.group(2).upper().rstrip("Ã").startswith(("N",)) else 1
        if num in vistos:
            raise ValueError(f"número {num} aparece duas vezes")
        if not 1 <= num <= esperados:
            raise ValueError(f"número {num} fora do intervalo 1–{esperados}")
        vistos[num] = val
    if ruins:
        raise ValueError(f"{len(ruins)} linha(s) não reconhecida(s), ex.: {ruins[:3]}")
    faltam = sorted(set(range(1, esperados + 1)) - set(vistos))
    if faltam:
        raise ValueError(f"faltam {len(faltam)} de {esperados} "
                         f"(primeiros: {faltam[:8]}) — resposta provavelmente truncada")
    return vistos


def importar_revisao(arquivo: Path, rev_id: str) -> int:
    """
    Importa a resposta de uma revisao INTEIRA, colada num arquivo so.

    A numeracao REINICIA a cada lote — o chat responde 1..100, depois 1..100 de novo — e
    o arquivo pode trazer texto do modelo antes das respostas. Entao: extrai as linhas de
    veredicto na ordem, quebra em corridas onde o numero volta a 1, e exige que as
    corridas casem UMA A UMA com os tamanhos dos lotes gerados.

    Sem essa exigencia um lote a mais ou a menos deslocaria todos os record_id seguintes,
    e o benchmark sairia silenciosamente errado.
    """
    lotes = sorted(SAIDA.glob(f"{rev_id}-lote-*.json"),
                   key=lambda x: int(x.stem.rsplit("-", 1)[-1]))
    if not lotes:
        print(f"sem lotes de {rev_id} — rode `gerar`", file=sys.stderr); return 1
    mapas = [json.loads(x.read_text())["mapa"] for x in lotes]

    pares, lixo = [], 0
    for l in arquivo.read_text(encoding="utf-8").splitlines():
        m = LINHA.match(l)
        if m:
            pares.append((int(m.group(1)), m.group(2)))
        elif l.strip() and not l.strip().startswith("```"):
            lixo += 1

    corridas, atual = [], []
    for n, v in pares:
        if n == 1 and atual:
            corridas.append(atual); atual = []
        atual.append((n, v))
    if atual:
        corridas.append(atual)

    esperado = [len(m) for m in mapas]
    obtido = [len(c) for c in corridas]
    if obtido != esperado:
        print(f"RECUSADO — {rev_id}: corridas {obtido}, lotes esperados {esperado}.\n"
              f"  nada foi importado.", file=sys.stderr); return 2
    for i, c in enumerate(corridas, 1):
        if [n for n, _ in c] != list(range(1, len(c) + 1)):
            print(f"RECUSADO — {rev_id} lote {i}: numeração não é 1..{len(c)}.\n"
                  f"  nada foi importado.", file=sys.stderr); return 2

    con = sqlite3.connect(BANCO)
    linhas, sim = [], 0
    for mapa, corrida in zip(mapas, corridas):
        for n, v in corrida:
            val = 0.0 if v.upper().rstrip("Ã").startswith("N") else 1.0
            sim += int(val)
            linhas.append(("synergy", rev_id, mapa[str(n)], "deepseek", "entra", val))
    if len({l[2] for l in linhas}) != len(linhas):
        print(f"RECUSADO — {rev_id}: record_id repetido.", file=sys.stderr); return 2
    con.executemany("INSERT OR REPLACE INTO respostas VALUES (?,?,?,?,?,?)", linhas)
    con.commit()
    print(f"  {rev_id}: {len(linhas)} importados em {len(corridas)} lotes · "
          f"{sim} SIM ({sim/len(linhas):.0%}) · {len(linhas)-sim} NAO"
          + (f" · {lixo} linha(s) de texto ignorada(s)" if lixo else ""))
    return 0


def importar(nome: str) -> int:
    base = SAIDA / nome
    meta_f, resp_f = base.with_suffix(".json"), Path(f"{base}.resposta.txt")
    if not meta_f.exists():
        print(f"sem {meta_f.name} — rode `gerar` antes", file=sys.stderr); return 1
    if not resp_f.exists():
        print(f"sem {resp_f.name}", file=sys.stderr); return 1
    meta = json.loads(meta_f.read_text())
    mapa = meta["mapa"]
    try:
        vered = analisar(resp_f.read_text(encoding="utf-8"), len(mapa))
    except ValueError as e:
        print(f"RECUSADO — {e}\n  nada foi importado.", file=sys.stderr); return 2

    con = sqlite3.connect(BANCO)
    linhas = [("synergy", meta["review_id"], mapa[str(k)], "deepseek", "entra", float(v))
              for k, v in sorted(vered.items())]
    con.executemany("INSERT OR REPLACE INTO respostas VALUES (?,?,?,?,?,?)", linhas)
    con.commit()
    sim = sum(v for v in vered.values())
    print(f"  {nome}: {len(linhas)} importados · {sim} SIM ({sim/len(linhas):.0%}) · "
          f"{len(linhas)-sim} NAO")
    return 0


def estado() -> None:
    con = sqlite3.connect(BANCO)
    feito = dict(con.execute(
        "SELECT review_id, COUNT(*) FROM respostas WHERE variante='deepseek' "
        "GROUP BY review_id"))
    print(f"{'lote':28} {'registros':>10} {'importado':>10}")
    for f in sorted(SAIDA.glob("*.json")):
        m = json.loads(f.read_text())
        resp = Path(str(f)[:-5] + ".resposta.txt")
        print(f"{f.stem:28} {len(m['mapa']):10} "
              f"{'sim' if resp.exists() else 'FALTA':>10}")
    print()
    for rev, n in sorted(feito.items()):
        print(f"  {rev}: {n} registros no banco")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("acao", choices=["gerar", "importar", "importar-revisao",
                                     "estado"])
    ap.add_argument("--arquivo", type=Path)
    ap.add_argument("--revisao")
    ap.add_argument("--lote", help="nome do lote, ex.: Hanlon_2022-lote-1")
    ap.add_argument("--revisoes", nargs="*", default=REVISOES)
    ap.add_argument("--por-lote", type=int, default=POR_LOTE)
    a = ap.parse_args()
    if a.acao == "gerar":
        gerar(a.revisoes, a.por_lote); return 0
    if a.acao == "estado":
        estado(); return 0
    if a.acao == "importar-revisao":
        if not (a.arquivo and a.revisao):
            print("--arquivo e --revisao são obrigatórios", file=sys.stderr); return 1
        return importar_revisao(a.arquivo, a.revisao)
    if not a.lote:
        print("--lote é obrigatório", file=sys.stderr); return 1
    return importar(a.lote)


if __name__ == "__main__":
    sys.exit(main())
