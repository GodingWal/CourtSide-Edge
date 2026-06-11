#!/usr/bin/env bash
# Deploy the AGENT tier on the vast.ai box.
# Run as root from the repo root on the vast.ai box:  bash deploy/scripts/deploy-agents.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="deploy/env/agents.env"
COMPOSE="deploy/docker-compose.agents.yml"

if [ ! -f "$ENV_FILE" ]; then
  echo "✗ $ENV_FILE missing. Copy deploy/env/agents.env.example and fill it in." >&2
  exit 1
fi

if grep -qE '^(API_KEY|REDIS_PASSWORD)=CHANGE_ME' "$ENV_FILE"; then
  echo "✗ Default secrets still present in $ENV_FILE. They must match the VPS web.env." >&2
  exit 1
fi

# If the Docker daemon isn't usable (e.g. vast.ai instances are unprivileged
# containers where dockerd cannot start), run the agents natively instead.
if ! docker info >/dev/null 2>&1; then
  echo "→ Docker daemon unavailable; falling back to native runner."
  exec bash deploy/scripts/run-agents-native.sh restart
fi

# Sanity-check Redis reachability before bringing up 18 agents.
REDIS_HOST="$(grep -E '^REDIS_HOST=' "$ENV_FILE" | cut -d= -f2-)"
REDIS_PORT="$(grep -E '^REDIS_PORT=' "$ENV_FILE" | cut -d= -f2-)"
REDIS_PASSWORD="$(grep -E '^REDIS_PASSWORD=' "$ENV_FILE" | cut -d= -f2-)"
echo "→ Checking Redis at ${REDIS_HOST}:${REDIS_PORT}…"
if docker run --rm redis:alpine redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" -a "$REDIS_PASSWORD" ping 2>/dev/null | grep -q PONG; then
  echo "✓ Redis reachable."
else
  echo "✗ Cannot reach Redis on the VPS. Check REDIS_HOST/PASSWORD and the VPS firewall (allow this box's IP to :6379)." >&2
  exit 1
fi

echo "→ Building and starting the agent tier…"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" up -d --build --remove-orphans
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" ps
echo "✓ Agent tier deployed."
