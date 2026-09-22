"""
Importador do DeepSeek. A falha que importa nesta frente NAO e ruidosa: e uma resposta
truncada que parece completa — o modelo responde 60 das 100 linhas, o arquivo parece
legitimo, e 40 registros somem sem ninguem notar. Cada teste abaixo e uma forma de isso
acontecer.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metapicker import deepseek as D  # noqa: E402


def _recusa(texto, n, trecho):
    try:
        D.analisar(texto, n)
    except ValueError as e:
        assert trecho in str(e).lower(), f"motivo errado: {e}"
    else:
        raise AssertionError(f"deveria recusar ({trecho})")


def test_aceita_resposta_bem_formada():
    r = D.analisar("1: SIM\n2: NAO\n3: SIM", 3)
    assert r == {1: 1, 2: 0, 3: 1}


def test_aceita_variacoes_de_formato():
    # cercas de codigo, [n], acentos, S/N, espacos — o chat formata de jeitos diferentes
    r = D.analisar("```\n[1]: SIM\n2. NÃO\n3) S\n 4 - N \n```", 4)
    assert r == {1: 1, 2: 0, 3: 1, 4: 0}


def test_recusa_truncada():
    # O caso perigoso: 60 linhas perfeitas de 100.
    _recusa("\n".join(f"{i}: SIM" for i in range(1, 61)), 100, "truncada")


def test_recusa_numero_repetido():
    _recusa("1: SIM\n2: NAO\n2: SIM\n3: SIM", 3, "duas vezes")


def test_recusa_numero_fora_do_intervalo():
    _recusa("1: SIM\n2: NAO\n7: SIM", 3, "fora do intervalo")


def test_recusa_veredicto_inventado():
    _recusa("1: SIM\n2: TALVEZ\n3: SIM", 3, "não reconhecida")


def test_recusa_texto_explicativo():
    # O modelo "ajudando": explica antes de responder.
    _recusa("Claro! Aqui está a análise:\n1: SIM\n2: NAO\n3: SIM", 3, "não reconhecida")


def test_recusa_vazio():
    _recusa("", 10, "truncada")


def test_nao_confunde_nao_com_sim():
    # A armadilha do acento: NAO, NÃO e N tem de dar 0, nunca 1.
    for neg in ("NAO", "NÃO", "nao", "não", "N", "n", "no", "NO"):
        assert D.analisar(f"1: {neg}", 1) == {1: 0}, f"{neg} virou SIM"
    for pos in ("SIM", "sim", "S", "s", "YES", "yes"):
        assert D.analisar(f"1: {pos}", 1) == {1: 1}, f"{pos} virou NAO"


def test_ida_e_volta_com_o_mapa():
    """Gerar -> responder -> importar tem de casar numero com record_id."""
    import json
    f = next(D.SAIDA.glob("*-lote-1.json"), None)
    if f is None:
        print("     (sem lotes gerados — pulando)"); return
    m = json.loads(f.read_text())
    n = len(m["mapa"])
    v = D.analisar("\n".join(f"{i}: {'SIM' if i % 3 == 0 else 'NAO'}"
                             for i in range(1, n + 1)), n)
    assert len(v) == n
    ids = {m["mapa"][str(k)] for k in v}
    assert len(ids) == n, "record_id repetido no mapa"


if __name__ == "__main__":
    fs = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in fs:
        f(); print(f"  ok  {f.__name__}")
    print(f"\n{len(fs)} testes passaram")
