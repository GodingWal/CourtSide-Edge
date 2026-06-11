#!/usr/bin/env bash
# Run the compute-tier agents natively (no Docker) — for hosts like vast.ai
# instances where the Docker daemon can't run (the instance itself is a
# container). Mirrors docker-compose.agents.yml: the 18 stateless agents that
# talk to the VPS over Redis/HTTPS. Ledger agents (0,13,14,15,16,20) stay on
# the VPS.
#
# Usage (from repo root):  bash deploy/scripts/run-agents-native.sh {start|stop|status|restart}
# Env comes from deploy/env/agents.env. Logs in ./logs/, PIDs in ./run/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="deploy/env/agents.env"
VENV=".venv-agents"
LOG_DIR="logs"
PID_DIR="run"

AGENTS=(
  12_alpha_sandbox
  1_market_scraper
  2_news_sentinel
  9_news_sentiment
  3_projection_engine
  10_game_total_projector
  11_market_value_detector
  5_referee_engine
  6_steam_detector
  2.5_game_flow_oracle
  7_correlation_guard
  8_bankroll_sizer
  4_execution_oracle
  17_velocity_agent
  18_liquidity_oracle
  19_sharp_profiler
  21_rotation_tracker
  22_data_watchdog
  23_game_session_manager
)

load_env() {
  [ -f "$ENV_FILE" ] || { echo "✗ $ENV_FILE missing." >&2; exit 1; }
  set -a; . "$ENV_FILE"; set +a
}

ensure_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    echo "→ Creating venv and installing requirements (one-time)…"
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --no-cache-dir -q -r requirements.txt
  fi
}

pid_of() { cat "$PID_DIR/$1.pid" 2>/dev/null || true; }
is_running() { local p; p="$(pid_of "$1")"; [ -n "$p" ] && kill -0 "$p" 2>/dev/null; }

start() {
  load_env
  ensure_venv
  mkdir -p "$LOG_DIR" "$PID_DIR"

  echo "→ Checking Redis at ${REDIS_HOST}:${REDIS_PORT}…"
  "$VENV/bin/python" - <<PY
import os, sys, redis
r = redis.Redis(host=os.environ['REDIS_HOST'], port=int(os.environ['REDIS_PORT']),
                password=os.environ.get('REDIS_PASSWORD') or None, socket_timeout=5)
try:
    r.ping(); print('✓ Redis reachable.')
except Exception as e:
    print(f'✗ Redis unreachable: {e}', file=sys.stderr); sys.exit(1)
PY

  for a in "${AGENTS[@]}"; do
    if is_running "$a"; then
      echo "  · $a already running (pid $(pid_of "$a"))"
      continue
    fi
    PYTHONPATH="$REPO_ROOT" nohup "$VENV/bin/python" "agents/$a/main.py" \
      >> "$LOG_DIR/$a.log" 2>&1 &
    echo $! > "$PID_DIR/$a.pid"
    echo "  ✓ started $a (pid $!)"
  done
  echo "✓ Agent tier running natively. Logs: $LOG_DIR/, status: $0 status"
}

stop() {
  for a in "${AGENTS[@]}"; do
    if is_running "$a"; then
      kill "$(pid_of "$a")" 2>/dev/null || true
      echo "  ✓ stopped $a"
    fi
    rm -f "$PID_DIR/$a.pid"
  done
}

status() {
  local up=0 down=0
  for a in "${AGENTS[@]}"; do
    if is_running "$a"; then
      echo "  UP   $a (pid $(pid_of "$a"))"; up=$((up+1))
    else
      echo "  DOWN $a"; down=$((down+1))
    fi
  done
  echo "── $up up / $down down ──"
  [ "$down" -eq 0 ]
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; start ;;
  status) status ;;
  *) echo "usage: $0 {start|stop|status|restart}" >&2; exit 2 ;;
esac
