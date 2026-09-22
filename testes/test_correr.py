"""
Runner de ponta a ponta com a API SIMULADA. Nenhuma chamada de rede, nenhum centavo.

Existe porque um bug no runner nao aparece como erro: ele aparece como credito gasto.
Cobre as tres exigencias — retomada, probabilidade crua, parada por orcamento.
"""
import sqlite3, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metapicker import correr, jev, triagem  # noqa: E402

CHAMADAS = []


def falso_perguntar(estado, perguntas, **kw):
    """Devolve o formato exato da API. Score alto so quando o REGISTRO casa — olhar o
    estado inteiro nao serve, porque o titulo da revisao esta em todos eles."""
    CHAMADAS.append(estado)
    casa = "wilson" in (estado["record"]["title"] or "").lower()
    jev.CONTA.registrar({"input_tokens": 800, "output_tokens": 0}, 0.3)
    return {"model": "jev-1.13.0",
            "answers": {q: {"type": "noul", "noul": 0.9 if casa else 0.1}
                        for q in perguntas},
            "usage": {"input_tokens": 800, "output_tokens": 0}}


def montar(n=10):
    rev = {"r1": {"titulo": "Therapies for Wilson disease", "criterios_brutos": "controlled studies"}}
    regs = [{"review_id": "r1", "record_id": f"W{i}",
             "titulo": "Wilson disease trial" if i % 2 == 0 else "Unrelated topic",
             "abstract": "abstract text", "ano": "2020",
             "rotulo_ta": i % 2, "rotulo_final": i % 2} for i in range(n)]
    return rev, regs


def _con():
    return correr.conectar(Path(tempfile.mkdtemp()) / "t.sqlite")


def test_grava_probabilidade_e_nao_booleano():
    jev.perguntar, real = falso_perguntar, jev.perguntar
    try:
        con = _con(); rev, regs = montar(6)
        correr.correr(con, "synergy", rev, regs)
        vals = [v for (v,) in con.execute("SELECT valor FROM respostas")]
        assert vals and all(isinstance(v, float) for v in vals)
        assert set(vals) == {0.9, 0.1}, f"esperava probabilidades, veio {set(vals)}"
        assert len(vals) == 6 * len(triagem.IDS), "faltou pergunta gravada"
    finally:
        jev.perguntar = real


def test_retoma_sem_repetir_chamada():
    jev.perguntar, real = falso_perguntar, jev.perguntar
    try:
        con = _con(); rev, regs = montar(8)
        CHAMADAS.clear()
        correr.correr(con, "synergy", rev, regs)
        primeira = len(CHAMADAS)
        r2 = correr.correr(con, "synergy", rev, regs)      # de novo, mesmos dados
        assert len(CHAMADAS) == primeira, "retomada refez chamadas — gastaria de novo"
        assert r2["novos"] == 0 and r2["pulados"] == 8
    finally:
        jev.perguntar = real


def test_retoma_so_o_que_falta():
    jev.perguntar, real = falso_perguntar, jev.perguntar
    try:
        con = _con(); rev, regs = montar(10)
        correr.correr(con, "synergy", rev[:] if isinstance(rev, list) else rev, regs[:4])
        CHAMADAS.clear()
        r = correr.correr(con, "synergy", rev, regs)
        assert r["novos"] == 6 and len(CHAMADAS) == 6, \
            f"deveria chamar só os 6 que faltam, chamou {len(CHAMADAS)}"
    finally:
        jev.perguntar = real


def test_parada_por_orcamento():
    jev.perguntar, real = falso_perguntar, jev.perguntar
    try:
        con = _con(); rev, regs = montar(500)
        jev.CONTA.zerar()
        # 800 tok/chamada = US$ 0,0000336. Teto de US$ 0,0001 ~ 3 chamadas -> corta no
        # primeiro lote de 2.
        r = correr.correr(con, "synergy", rev, regs, orcamento=0.0001, lote=2)
        assert r["nao_rodados"] > 0, "o teto de orçamento não cortou a rodada"
        assert r["novos"] < 500, "rodou tudo apesar do teto"
    finally:
        jev.perguntar = real


def test_rotulos_ficam_no_banco():
    jev.perguntar, real = falso_perguntar, jev.perguntar
    try:
        con = _con(); rev, regs = montar(4)
        correr.correr(con, "synergy", rev, regs)
        n = con.execute("SELECT COUNT(*) FROM rotulos").fetchone()[0]
        assert n == 4, "o avaliador depende dos rótulos gravados junto"
        ta = con.execute("SELECT SUM(rotulo_ta) FROM rotulos").fetchone()[0]
        assert ta == 2
    finally:
        jev.perguntar = real


def test_falha_nao_some_da_contagem():
    def falha(estado, perguntas, **kw):
        raise jev.ErroJev("simulada")
    jev.perguntar, real = falha, jev.perguntar
    try:
        con = _con(); rev, regs = montar(3)
        r = correr.correr(con, "synergy", rev, regs)
        assert r["novos"] == 0
        n = con.execute("SELECT COUNT(*) FROM chamadas WHERE erro IS NOT NULL").fetchone()[0]
        assert n == 3, "falha tem de ficar registrada, não sumir"
    finally:
        jev.perguntar = real


if __name__ == "__main__":
    fs = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in fs:
        jev.CONTA.zerar(); f(); print(f"  ok  {f.__name__}")
    print(f"\n{len(fs)} testes passaram")
