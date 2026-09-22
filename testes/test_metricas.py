"""
Metricas contra casos de resposta conhecida. Uma metrica errada nao avisa: ela so devolve
um numero plausivel. Estes testes existem para que o numero errado falhe alto.
"""
import math, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from metapicker import avaliar as A  # noqa: E402


def test_auc_perfeito_e_invertido():
    sc = [0.9, 0.8, 0.2, 0.1]; rot = [1, 1, 0, 0]
    assert A.auc(sc, rot) == 1.0
    assert A.auc(sc, [0, 0, 1, 1]) == 0.0


def test_auc_empate_total_e_meio():
    # Todos com o mesmo score: nenhuma informacao. Tem de dar exatamente 0,5.
    assert A.auc([0.5] * 6, [1, 0, 1, 0, 1, 0]) == 0.5


def test_auc_conhecido():
    # 2 positivos, 2 negativos; 1 par invertido de 4 -> 0,75
    assert abs(A.auc([0.9, 0.4, 0.6, 0.1], [1, 1, 0, 0]) - 0.75) < 1e-9


def test_triagem_para_recall():
    # 10 registros, 2 positivos nas posicoes 1 e 5 da ordem decrescente
    sc = [1.0, .9, .8, .7, .6, .5, .4, .3, .2, .1]
    rot = [1, 0, 0, 0, 1, 0, 0, 0, 0, 0]
    assert abs(A.triagem_para_recall(sc, rot, 0.5) - 0.1) < 1e-9   # 1 de 2 -> 1 item
    assert abs(A.triagem_para_recall(sc, rot, 1.0) - 0.5) < 1e-9   # 2 de 2 -> 5 itens


def test_wss_ranqueador_perfeito():
    # 100 registros, 5 positivos no topo. Para 95% de recall precisa de ceil(4,75)=5
    # itens = 5% da lista. WSS = (1-0,05) - 0,05 = 0,90
    sc = [1.0 - i / 100 for i in range(100)]
    rot = [1] * 5 + [0] * 95
    assert abs(A.wss(sc, rot, 0.95) - 0.90) < 1e-9


def test_wss_aleatorio_fica_perto_de_zero():
    # Ranqueador sem sinal: os positivos ficam espalhados. Para achar 95% deles e preciso
    # triar ~95% da lista, e o WSS tem de ficar perto de 0 — nao de 0,9.
    n = 1000
    sc = [1.0 - i / n for i in range(n)]
    rot = [1 if i % 50 == 0 else 0 for i in range(n)]   # 20 positivos, espacados
    w = A.wss(sc, rot, 0.95)
    assert abs(w) < 0.06, f"WSS de ranqueador sem sinal deveria ser ~0, deu {w:.3f}"


def test_limiar_para_recall_alcanca_o_alvo():
    sc = [0.99, 0.80, 0.70, 0.30, 0.10]; rot = [1, 0, 1, 0, 0]
    t = A.limiar_para_recall(sc, rot, 1.0)
    c = A.classicas(sc, rot, t)
    assert c["sensibilidade"] == 1.0, "o limiar de recall 100% tem de pegar todos"


def test_classicas_em_caso_a_mao():
    sc = [0.9, 0.8, 0.4, 0.2]; rot = [1, 0, 1, 0]
    c = A.classicas(sc, rot, 0.5)          # >= 0,5 -> positivo: 0.9(TP) 0.8(FP)
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (1, 1, 1, 1)
    assert c["sensibilidade"] == 0.5 and c["especificidade"] == 0.5
    assert c["precisao"] == 0.5 and abs(c["f1"] - 0.5) < 1e-9


def test_classe_unica_nao_quebra():
    assert math.isnan(A.auc([0.5, 0.6], [0, 0]))
    assert math.isnan(A.recall_em([0.5, 0.6], [0, 0], 0.5))


def test_diz_nao_a_tudo_tem_especificidade_perfeita():
    # O motivo de sensibilidade/especificidade nao bastarem: o classificador inutil
    # marca 100% de especificidade e 0% de sensibilidade a 1% de prevalencia.
    sc = [0.0] * 1000
    rot = [1] * 10 + [0] * 990
    c = A.classicas(sc, rot, 0.5)
    assert c["especificidade"] == 1.0 and c["sensibilidade"] == 0.0


if __name__ == "__main__":
    fs = [f for n, f in sorted(globals().items()) if n.startswith("test_")]
    for f in fs:
        f(); print(f"  ok  {f.__name__}")
    print(f"\n{len(fs)} testes passaram")
