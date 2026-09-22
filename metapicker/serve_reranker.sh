#!/usr/bin/env bash
# Starts a DEDICATED Qwen3-Reranker instance for the benchmark, on the GPU.
#
# WHY NOT USE THE LLAMA-SWAP ONE (port 10012). Two independent reasons:
#
#   1. the shared profile starts WITHOUT --ubatch-size, so the physical batch stays at
#      the default 512 tokens and the server refuses, with HTTP 500, any (query+document)
#      pair above that. Eligibility criteria run from 66 to 689 tokens: 6 of the 21
#      reviews exceed 512 on the average record alone. Truncating the criteria would mean
#      the reranker received LESS than JEV did, and the comparison would stop being valid;
#   2. that instance is a child of llama-swap. Killing it by hand leaves the supervisor
#      inconsistent, and `LLMs/config/` is shared infrastructure this project does not
#      touch.
#
# This one starts alongside, on 10099, and dies when the benchmark is over.

set -euo pipefail

PORT="${PORT:-10099}"
NGL="${NGL:-99}"
MODEL="${MODEL:-/home/phobos/LLMs/models/small/qwen3-reranker-0.6b-q8_0.gguf}"
SRV="${SRV:-/home/phobos/LLMs/llama.cpp/build/bin/llama-server}"
LOG="${LOG:-/tmp/claude-1000/-home-phobos/rerank-$PORT.log}"
SLOTS="${SLOTS:-8}"
# MEASURED TRAP: --parallel DIVIDES --ctx-size across slots. With
# `--ctx-size 4096 --parallel 8` each slot gets 512 tokens and the server refuses with
# HTTP 400 exceed_context_size_error — which is NOT the --ubatch-size error (HTTP 500
# "too large to process"), and only appears once you add parallelism.
# The heaviest pair in the corpus is ~1,200 tokens (689 of criteria + 509 of record).
CTX="${CTX:-$((4096 * SLOTS))}"

[ -x "$SRV" ]   || { echo "no llama-server at $SRV" >&2; exit 1; }
[ -f "$MODEL" ] || { echo "no model at $MODEL" >&2; exit 1; }

if curl -sf "http://127.0.0.1:$PORT/health" --max-time 2 >/dev/null 2>&1; then
    echo "a server is already alive on $PORT — reusing it"
    exit 0
fi
if ss -ltn 2>/dev/null | grep -q ":$PORT "; then
    echo "port $PORT is taken by something else" >&2; exit 1
fi

mkdir -p "$(dirname "$LOG")"
echo "starting reranker on $PORT (ngl=$NGL)…" >&2
# --pooling rank --embedding are required alongside --reranking: without them llama.cpp
# does not read the yes/no classifier logits and returns meaningless ~1e-22 scores.
nohup "$SRV" \
    --host 127.0.0.1 --port "$PORT" -ngl "$NGL" \
    --model "$MODEL" \
    --reranking --pooling rank --embedding \
    --ctx-size "$CTX" --ubatch-size 4096 --batch-size 4096 \
    --parallel "$SLOTS" \
    > "$LOG" 2>&1 &

for _ in $(seq 1 90); do
    if curl -sf "http://127.0.0.1:$PORT/health" --max-time 2 >/dev/null 2>&1; then
        echo "ready at http://127.0.0.1:$PORT  (log: $LOG)"
        # GPU GATE: an -ngl 99 that silently fell back to CPU runs identically, just
        # slowly, and the only signal would be a number nobody checked.
        VRAM=$(nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader 2>/dev/null \
               | grep -c MiB || true)
        echo "processes holding VRAM: ${VRAM:-0}"
        grep -iE "offloaded|CUDA|n_ctx_slot" "$LOG" | tail -3 || true
        exit 0
    fi
    sleep 1
done
echo "did not come up within 90s — see $LOG" >&2; tail -20 "$LOG" >&2; exit 1
