#!/usr/bin/env bash
# Set up a LOCAL Hermes inference server on the vast.ai GPU box (no Docker —
# Ollama runs as a plain binary, which works in vast's unprivileged containers).
#
# Default model: hermes3 (NousResearch Hermes 3 8B instruct, ~4.7GB) —
# sized for this instance's ~31GB free disk. With ~50GB+ free disk, run:
#   HERMES_MODEL=hermes3:70b bash deploy/scripts/setup-local-llm.sh
#
# The agents' HermesClient defaults to http://localhost:11434/v1 and the
# hermes3 model, so after this script + an agent restart, agents 2 and 9
# use the local GPU model automatically.
#
# Usage (from repo root):  bash deploy/scripts/setup-local-llm.sh [start|stop|status]
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

MODEL="${HERMES_MODEL:-hermes3}"
LOG_DIR="logs"
PID_FILE="run/ollama.pid"
OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"

is_up() { curl -fsS "http://${OLLAMA_HOST}/api/tags" >/dev/null 2>&1; }

install_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    echo "✓ ollama already installed: $(ollama --version 2>/dev/null | head -1)"
  else
    echo "→ Installing ollama…"
    curl -fsSL https://ollama.com/install.sh | sh
  fi
}

start() {
  install_ollama
  mkdir -p "$LOG_DIR" "$(dirname "$PID_FILE")"

  if is_up; then
    echo "✓ ollama server already running."
  else
    echo "→ Starting ollama server…"
    OLLAMA_HOST="$OLLAMA_HOST" nohup ollama serve >> "$LOG_DIR/ollama.log" 2>&1 &
    echo $! > "$PID_FILE"
    for i in $(seq 1 30); do is_up && break; sleep 1; done
    is_up || { echo "✗ ollama did not come up; see $LOG_DIR/ollama.log" >&2; exit 1; }
    echo "✓ ollama server up at http://${OLLAMA_HOST}"
  fi

  echo "→ Pulling model ${MODEL} (one-time download)…"
  ollama pull "$MODEL"

  echo "→ Smoke test…"
  curl -fsS "http://${OLLAMA_HOST}/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\": \"${MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"Reply with the word READY\"}], \"max_tokens\": 8}" \
    | head -c 400; echo
  echo "✓ Local Hermes (${MODEL}) serving at http://${OLLAMA_HOST}/v1"
  echo "  Restart the agents to pick it up: bash deploy/scripts/run-agents-native.sh restart"
}

stop() {
  if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    kill "$(cat "$PID_FILE")" && echo "✓ ollama stopped."
  else
    pkill -f 'ollama serve' 2>/dev/null && echo "✓ ollama stopped." || echo "· ollama not running."
  fi
  rm -f "$PID_FILE"
}

status() {
  if is_up; then
    echo "UP — models:"; curl -fsS "http://${OLLAMA_HOST}/api/tags" | head -c 500; echo
  else
    echo "DOWN"
    exit 1
  fi
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
