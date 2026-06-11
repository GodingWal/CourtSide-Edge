#!/usr/bin/env bash
# Deploy the WEB tier on the VPS (courtside-edge.com).
# Run as root from the repo root on the VPS:  bash deploy/scripts/deploy-web.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

ENV_FILE="deploy/env/web.env"
COMPOSE="deploy/docker-compose.web.yml"

if [ ! -f "$ENV_FILE" ]; then
  echo "✗ $ENV_FILE missing. Copy deploy/env/web.env.example and fill it in." >&2
  exit 1
fi

# Fail fast on unset secrets.
if grep -qE '^(API_KEY|REDIS_PASSWORD)=CHANGE_ME' "$ENV_FILE"; then
  echo "✗ Default secrets still present in $ENV_FILE. Set API_KEY and REDIS_PASSWORD." >&2
  exit 1
fi

mkdir -p data infrastructure/redis/data

echo "→ Building and starting the web tier…"
docker compose --env-file "$ENV_FILE" -f "$COMPOSE" up -d --build --remove-orphans

echo "→ Waiting for the web server health endpoint…"
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:3000/health >/dev/null 2>&1; then
    echo "✓ Web server healthy."
    break
  fi
  sleep 2
  [ "$i" = 30 ] && { echo "✗ Web server did not become healthy."; docker compose -f "$COMPOSE" logs --tail=50 web_server; exit 1; }
done

docker compose --env-file "$ENV_FILE" -f "$COMPOSE" ps
echo "✓ Web tier deployed. Run deploy/scripts/install-nginx.sh and setup-tls.sh to expose the domain."
