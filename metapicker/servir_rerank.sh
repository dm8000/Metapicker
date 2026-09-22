#!/usr/bin/env bash
# Sobe uma instancia PROPRIA do Qwen3-Reranker 0.6B para o benchmark, na GPU.
#
# POR QUE NAO USAR A DO LLAMA-SWAP (porta 10012). Duas razoes independentes:
#
#   1. o perfil compartilhado sobe SEM --ubatch-size, entao o batch fisico fica no default
#      de 512 tokens e o servidor recusa com HTTP 500 qualquer par (query+documento) acima
#      disso. Os criterios de elegibilidade vao de 66 a 689 tokens: 6 das 21 revisoes
#      estouram 512 ja na media. Truncar o criterio faria o Qwen receber MENOS do que o JEV
#      recebeu, e a comparacao deixaria de valer;
#   2. aquela instancia e filha do llama-swap. Mata-la a mao deixa o supervisor
#      inconsistente, e `LLMs/config/` e infraestrutura compartilhada que este projeto nao
#      toca.
#
# Esta sobe ao lado, na 10099, e morre quando o benchmark acaba.

set -euo pipefail

PORTA="${PORTA:-10099}"
NGL="${NGL:-99}"
MODELO="${MODELO:-/home/phobos/LLMs/models/small/qwen3-reranker-0.6b-q8_0.gguf}"
SRV="${SRV:-/home/phobos/LLMs/llama.cpp/build/bin/llama-server}"
LOG="${LOG:-/tmp/claude-1000/-home-phobos/rerank-$PORTA.log}"
SLOTS="${SLOTS:-8}"
# ARMADILHA MEDIDA: --parallel DIVIDE o --ctx-size entre os slots. Com
# `--ctx-size 4096 --parallel 8` cada slot fica com 512 tokens e o servidor recusa com
# HTTP 400 exceed_context_size_error — que NAO e o mesmo erro do --ubatch-size (HTTP 500
# "too large to process"), e so aparece quando se acrescenta paralelismo.
# O par mais pesado do corpus e ~1.200 tokens (criterio de 689 + registro de 509).
CTX="${CTX:-$((4096 * SLOTS))}"

[ -x "$SRV" ]   || { echo "sem llama-server em $SRV" >&2; exit 1; }
[ -f "$MODELO" ] || { echo "sem modelo em $MODELO" >&2; exit 1; }

if curl -sf "http://127.0.0.1:$PORTA/health" --max-time 2 >/dev/null 2>&1; then
    echo "já há servidor vivo na $PORTA — reaproveitando"
    exit 0
fi
if ss -ltn 2>/dev/null | grep -q ":$PORTA "; then
    echo "porta $PORTA ocupada por outra coisa" >&2; exit 1
fi

mkdir -p "$(dirname "$LOG")"
echo "subindo rerank na $PORTA (ngl=$NGL)…" >&2
nohup "$SRV" \
    --host 127.0.0.1 --port "$PORTA" -ngl "$NGL" \
    --model "$MODELO" \
    --reranking --ctx-size "$CTX" --ubatch-size 4096 --batch-size 4096 \
    --parallel "$SLOTS" \
    > "$LOG" 2>&1 &

for _ in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:$PORTA/health" --max-time 2 >/dev/null 2>&1; then
        echo "pronto em http://127.0.0.1:$PORTA  (log: $LOG)"
        # PORTAO DA GPU: um -ngl 99 que caiu silenciosamente para CPU roda igual, so
        # devagar, e o unico sinal seria um numero que ninguem conferiu.
        VRAM=$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null \
               | grep -c MiB || true)
        echo "processos com VRAM alocada: ${VRAM:-0}"
        grep -iE "offloaded|CUDA|n_gpu_layers" "$LOG" | tail -3 || true
        exit 0
    fi
    sleep 1
done
echo "não subiu em 90s — ver $LOG" >&2; tail -20 "$LOG" >&2; exit 1
