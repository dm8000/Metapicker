"""
Trava as duas armadilhas do JEV contra regressao. Nao faz chamada de rede.

  1. a resposta esta em r["answers"][qid], NAO em r[qid]
  2. noul devolve PROBABILIDADE, nao booleano
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from metapicker import jev  # noqa: E402

# Formato exato que a API devolve.
RESPOSTA = {
    "model": "jev-1.13.0",
    "answers": {
        "recuperar": {"type": "noul", "noul": 0.87},
        "populacao": {"type": "noul", "noul": 0.03},
        "dominio": {"type": "choice", "choice": "oncologia", "confidence": 0.61},
        "forca": {"type": "score", "score": 3.4, "confidence": 0.55},
    },
    "usage": {"input_tokens": 812, "output_tokens": 0},
}


def test_noul_e_float_nao_bool():
    v = jev.obter(RESPOSTA, "recuperar")
    assert isinstance(v, float), f"noul deveria ser float, veio {type(v).__name__}"
    assert not isinstance(v, bool), "noul virou booleano — a armadilha 2 voltou"
    assert v == 0.87


def test_noul_baixo_nao_e_falsy_ignorado():
    # 0,03 e um NAO forte. Se algum codigo fizer `if valor:` ele some. O teste existe
    # para documentar que o valor e continuo e que 0,03 != False.
    v = jev.obter(RESPOSTA, "populacao")
    assert 0.0 <= v <= 1.0 and v == 0.03


def test_choice_e_score_vem_com_confianca():
    op, c = jev.obter(RESPOSTA, "dominio")
    assert op == "oncologia" and c == 0.61
    n, c2 = jev.obter(RESPOSTA, "forca")
    assert n == 3.4 and c2 == 0.55


def test_resposta_nao_esta_na_raiz():
    # A armadilha 1: r[qid] nao existe. Se um dia existir, o desenho mudou.
    assert "recuperar" not in RESPOSTA, "a API passou a expor na raiz — rever obter()"


def test_pergunta_ausente_levanta_com_as_disponiveis():
    try:
        jev.obter(RESPOSTA, "inexistente")
    except jev.ErroJev as e:
        assert "recuperar" in str(e), "o erro deve listar o que veio, para diagnostico"
    else:
        raise AssertionError("pergunta ausente deveria levantar ErroJev")


def test_402_e_fatal_e_nao_vira_none_silencioso():
    """Credito esgotado tem de ABORTAR o lote. Engolir como falha comum faz o runner
    anunciar «nenhuma chamada» depois de milhares de tentativas — aconteceu de verdade."""
    import httpx

    class RespostaFalsa:
        status_code = 402
        text = '{"detail":{"error_type":"billing_error"}}'
        headers: dict = {}

    class ClienteFalso:
        def post(self, *a, **k):
            return RespostaFalsa()

    real = jev.cliente
    jev.cliente = lambda: ClienteFalso()
    try:
        try:
            jev.perguntar("x", {"q": jev.noul("y")}, tentativas=3)
        except jev.ErroFatal:
            pass
        else:
            raise AssertionError("402 deveria levantar ErroFatal")
        # e em lote: sobe, nao vira item None
        try:
            jev.em_lote([("x", {"q": jev.noul("y")})] * 4)
        except jev.ErroFatal:
            pass
        else:
            raise AssertionError("em_lote deveria propagar ErroFatal, não engolir")
    finally:
        jev.cliente = real


def test_conta_calcula_custo():
    c = jev.Conta()
    c.registrar({"input_tokens": 1_000_000, "output_tokens": 0}, 1.0)
    assert abs(c.custo - 0.042) < 1e-9, f"custo errado: {c.custo}"


def test_score_recusa_numero_invalido_de_niveis():
    try:
        jev.score("x", ["a"])
    except ValueError:
        pass
    else:
        raise AssertionError("score com 1 nivel deveria recusar")


if __name__ == "__main__":
    fs = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in fs:
        f()
        print(f"  ok  {f.__name__}")
    print(f"\n{len(fs)} testes passaram")
