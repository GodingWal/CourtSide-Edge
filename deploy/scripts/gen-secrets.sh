#!/usr/bin/env bash
# Generate deploy/env/web.env and deploy/env/agents.env with a fresh, matched
# API_KEY and REDIS_PASSWORD. Run once, then copy agents.env to the vast.ai box.
# Refuses to overwrite existing env files unless FORCE=1.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/deploy/env"

WEB="web.env"
AGENTS="agents.env"

if { [ -f "$WEB" ] || [ -f "$AGENTS" ]; } && [ "${FORCE:-0}" != "1" ]; then
  echo "✗ $WEB or $AGENTS already exists. Re-run with FORCE=1 to overwrite." >&2
  exit 1
fi

API_KEY="$(openssl rand -hex 32)"
REDIS_PASSWORD="$(openssl rand -hex 24)"
VPS_IP="${VPS_IP:-76.13.100.125}"
HERMES_API_KEY="${HERMES_API_KEY:-}"

sed -e "s|^API_KEY=.*|API_KEY=${API_KEY}|" \
    -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${REDIS_PASSWORD}|" \
    -e "s|^REDIS_URL=.*|REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379|" \
    -e "s|^HERMES_API_KEY=.*|HERMES_API_KEY=${HERMES_API_KEY}|" \
    web.env.example > "$WEB"

sed -e "s|^API_KEY=.*|API_KEY=${API_KEY}|" \
    -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=${REDIS_PASSWORD}|" \
    -e "s|^REDIS_HOST=.*|REDIS_HOST=${VPS_IP}|" \
    -e "s|^HERMES_API_KEY=.*|HERMES_API_KEY=${HERMES_API_KEY}|" \
    agents.env.example > "$AGENTS"

chmod 600 "$WEB" "$AGENTS"
echo "✓ Wrote deploy/env/web.env and deploy/env/agents.env with matched secrets."
echo "  Keep web.env on the VPS; copy agents.env to the vast.ai box."
