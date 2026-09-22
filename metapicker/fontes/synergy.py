#!/usr/bin/env python3
"""
SYNERGY+ v3.0 — o conjunto COMPLETO de registros que os autores de cada revisao
recuperaram e triaram, com rotulo binario. E o unico dos tres datasets que e
literalmente o screening set de cada revisao, e nao um pool reconstruido.

DOIS ROTULOS, e a distincao decide o que se esta medindo:

  label_abstract_included -> passou na triagem titulo/abstract   ALVO PRIMARIO: e a
                             decisao que o JEV esta de fato tomando
  label_included          -> entrou na sintese apos o texto completo   alvo secundario

POR QUE ESTE MODULO NAO USA `python -m synergy_dataset get`. O construtor oficial
descarta os registros sem abstract no OpenAlex. Medido em duas revisoes: ele manteria
35-48% dos registros — e, pior, so 30-51% dos INCLUIDOS, que sao a verdade de referencia.
Isso quebraria o requisito central de rodar sobre o conjunto completo triado, e inflaria
a prevalencia. Os zips `works_*.zip` trazem 100% dos registros e 100% dos titulos; o
abstract e que falta em ~60%. Um registro so com titulo continua sendo um registro que os
autores triaram, entao ele entra — marcado, e as metricas saem tambem na fatia com
abstract.

    ./synergy.py construir     # labels.csv + works_*.zip -> dados/synergy/*.jsonl
    ./synergy.py conferir
    ./synergy.py catalogo
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
import os
from pathlib import Path

csv.field_size_limit(10 ** 7)

RAIZ = Path(__file__).resolve().parents[2]
CONSTRUIDO = Path(os.environ.get("METAPICKER_SYNERGY", RAIZ / "dados" / "synergy"))
ORIGEM = Path("~/.synergy_dataset_source/synergy-dataset-plus").expanduser()


@dataclass
class Registro:
    fonte: str
    review_id: str
    record_id: str
    titulo: str
    abstract: str
    ano: str
    doi: str
    pmid: str
    rotulo_ta: int | None
    rotulo_final: int


@dataclass
class Revisao:
    fonte: str
    review_id: str
    titulo: str
    pergunta: str
    criterios_brutos: str
    criterios: dict
    doi: str
    n_publicado: int
    n_incluidos_publicado: int
    tem_rotulo_ta: bool


def _rotulo(v) -> int | None:
    v = str(v or "").strip()
    if v in ("0", "0.0", "False"):
        return 0
    if v in ("1", "1.0", "True"):
        return 1
    return None


def _meta(nome: str) -> dict:
    f = ORIGEM / nome / "metadata.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _titulo_da_revisao(nome: str) -> str:
    # Vazio, e nao o proprio nome do dataset: "Cinquin 2018" no `state` e ruido —
    # o JEV leria a chave do arquivo como se fosse a pergunta da revisao.
    f = ORIGEM / nome / "metadata_publication.json"
    if not f.exists():
        return ""
    try:
        return json.loads(f.read_text()).get("title") or ""
    except Exception:
        return ""



def _texto_do_inverso(inv: dict | None) -> str:
    """OpenAlex guarda o abstract como indice invertido {palavra: [posicoes]}."""
    if not inv:
        return ""
    pos = [(i, w) for w, idxs in inv.items() for i in idxs]
    return " ".join(w for _, w in sorted(pos))


def construir(apenas: list[str] | None = None) -> None:
    """labels.csv + works_*.zip -> um JSONL por revisao, so com o que o JEV usa."""
    import zipfile
    CONSTRUIDO.mkdir(parents=True, exist_ok=True)
    nomes = sorted(d.name for d in ORIGEM.iterdir() if d.is_dir())
    if apenas:
        nomes = [n for n in nomes if n in set(apenas)]
    for i, nome in enumerate(nomes, 1):
        d = ORIGEM / nome
        zips = sorted(d.glob("works_*.zip"))
        if not (d / "labels.csv").exists() or not zips:
            continue
        saida = CONSTRUIDO / f"{nome}.jsonl"
        if saida.exists() and saida.stat().st_size > 0:
            continue
        with (d / "labels.csv").open(newline="", encoding="utf-8") as fh:
            labs = list(csv.DictReader(fh))
        por_id = {(x.get("openalex_id") or "").rsplit("/", 1)[-1].lower(): x
                  for x in labs if x.get("openalex_id")}
        works: dict[str, dict] = {}
        for z in zips:
            zf = zipfile.ZipFile(z)
            for n in zf.namelist():
                try:
                    bloco = json.loads(zf.read(n))
                except Exception:
                    continue
                for w in (bloco if isinstance(bloco, list) else [bloco]):
                    works[(w.get("id") or "").rsplit("/", 1)[-1].lower()] = w
        linhas = []
        for k, lab in por_id.items():
            w = works.get(k)
            if w is None:
                continue
            inv = (w.get("abstract_inverted_index_cleaned")
                   or w.get("abstract_inverted_index"))
            linhas.append({
                "record_id": k,
                "titulo": (w.get("title") or w.get("display_name") or "").strip(),
                "abstract": _texto_do_inverso(inv).strip(),
                "ano": str(w.get("publication_year") or ""),
                "doi": (lab.get("doi") or w.get("doi") or "").strip(),
                "pmid": (lab.get("pmid") or "").strip(),
                "rotulo_ta": _rotulo(lab.get("label_abstract_included")),
                "rotulo_final": _rotulo(lab.get("label_included")),
            })
        with saida.open("w", encoding="utf-8") as fh:
            for l in linhas:
                print(json.dumps(l, ensure_ascii=False), file=fh)
        print(f"  [{i}/{len(nomes)}] {nome}: {len(linhas):,} registros "
              f"({sum(1 for l in linhas if l['abstract'])/max(1,len(linhas)):.0%} "
              f"com abstract)".replace(",", "."), file=sys.stderr)


def carregar(apenas: list[str] | None = None
             ) -> tuple[list[Registro], list[Revisao]]:
    if not CONSTRUIDO.exists() or not any(CONSTRUIDO.glob("*.jsonl")):
        raise SystemExit(f"{CONSTRUIDO} vazio — rode primeiro:  "
                         f"./metapicker/fontes/synergy.py construir")
    regs: list[Registro] = []
    revs: list[Revisao] = []
    for f in sorted(CONSTRUIDO.glob("*.jsonl")):
        nome = f.stem
        if apenas and nome not in apenas:
            continue
        # Iterar o arquivo, nao splitlines(): splitlines() tambem quebra em
        # U+2028/U+2029, que aparecem crus dentro de titulos do OpenAlex.
        with f.open(encoding="utf-8") as fh:
            linhas = [json.loads(l) for l in fh if l.strip()]
        if not linhas:
            continue
        m = _meta(nome)
        pub = m.get("publication") or {}
        dados = m.get("data") or {}
        revs.append(Revisao(
            fonte="synergy", review_id=nome, titulo=_titulo_da_revisao(nome),
            pergunta=_titulo_da_revisao(nome),
            criterios_brutos=(pub.get("eligibility_criteria") or "").strip(),
            criterios={}, doi=str(pub.get("doi") or ""),
            n_publicado=int(dados.get("n_records") or 0),
            n_incluidos_publicado=int(dados.get("n_records_included") or 0),
            tem_rotulo_ta=any(x.get("rotulo_ta") is not None for x in linhas)))
        for x in linhas:
            if x.get("rotulo_final") is None:
                continue
            regs.append(Registro(
                fonte="synergy", review_id=nome, record_id=x["record_id"],
                titulo=x["titulo"], abstract=x["abstract"], ano=x["ano"],
                doi=x["doi"], pmid=x["pmid"],
                rotulo_ta=x.get("rotulo_ta"), rotulo_final=x["rotulo_final"]))
    return regs, revs


def conferir() -> int:
    regs, revs = carregar()
    n, inc = len(regs), sum(r.rotulo_final for r in regs)
    ta = sum(1 for r in regs if r.rotulo_ta == 1)
    print(f"revisões          {len(revs)}   "
          f"({sum(1 for v in revs if v.tem_rotulo_ta)} com rótulo de triagem T/A, "
          f"{sum(1 for v in revs if v.criterios_brutos)} com critérios)")
    print(f"registros         {n:,}".replace(",", "."))
    print(f"incluídos (final) {inc:,}".replace(",", ".") + f"   prevalência {inc/n:.2%}")
    print(f"passou T/A        {ta:,}".replace(",", "."))
    print(f"sem abstract      {sum(1 for r in regs if not r.abstract):,}".replace(",", "."))

    # A carga tem de bater com o n publicado no metadata.json de cada revisao.
    ruins = []
    por: dict[str, int] = {}
    for r in regs:
        por[r.review_id] = por.get(r.review_id, 0) + 1
    for v in revs:
        if v.n_publicado and abs(por.get(v.review_id, 0) - v.n_publicado) > \
                max(5, v.n_publicado * 0.02):
            ruins.append((v.review_id, por.get(v.review_id, 0), v.n_publicado))
    if ruins:
        print(f"\n{len(ruins)} revisões divergem do n publicado (as 5 maiores):")
        for k, a, b in sorted(ruins, key=lambda x: -abs(x[1] - x[2]))[:5]:
            print(f"   {k:32} carregado {a:6,} · publicado {b:6,}".replace(",", "."))
        print("   (o pacote filtra registros sem abstract/OA — divergência esperada)")
    print("\nsanidade de carga: OK" if n and inc else "\nsanidade: FALHOU")
    return 0 if n and inc else 1


def catalogo() -> None:
    """Tabela por revisão, para escolher o que cabe no orçamento."""
    regs, revs = carregar()
    por: dict[str, list[Registro]] = {}
    for r in regs:
        por.setdefault(r.review_id, []).append(r)
    print(f"{'revisão':34} {'n':>7} {'pos':>5} {'prev':>7} {'T/A':>6} {'s/abs':>6} "
          f"{'crit':>5} {'US$':>7}")
    acum = 0.0
    for v in sorted(revs, key=lambda x: len(por.get(x.review_id, []))):
        g = por.get(v.review_id, [])
        if not g:
            continue
        inc = sum(r.rotulo_final for r in g)
        ta = sum(1 for r in g if r.rotulo_ta == 1)
        sa = sum(1 for r in g if not r.abstract)
        custo = len(g) * 837 / 1e6 * 0.042      # 837 tok/registro, medido no smoke test
        acum += custo
        print(f"{v.review_id[:34]:34} {len(g):7,} {inc:5} {inc/len(g):6.2%} "
              f"{(ta if v.tem_rotulo_ta else 0):6} {sa/len(g):6.1%} "
              f"{'sim' if v.criterios_brutos else 'NÃO':>5} {custo:7.3f}"
              .replace(",", "."))
    print(f"\n{'TOTAL':34} {len(regs):7,} ".replace(",", ".")
          + f"{'':5} {'':7} {'':6} {'':6} {'':5} {acum:7.2f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("acao", choices=["construir", "conferir", "catalogo"])
    ap.add_argument("--apenas", nargs="*")
    a = ap.parse_args()
    if a.acao == "construir":
        construir(a.apenas or None); return 0
    return conferir() if a.acao == "conferir" else (catalogo() or 0)


if __name__ == "__main__":
    sys.exit(main())
