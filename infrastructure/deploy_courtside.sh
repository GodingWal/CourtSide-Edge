#!/usr/bin/env bash
set -euo pipefail

REPO=/opt/wnba/repo
cd "$REPO"

/bin/bash "$REPO/infrastructure/backup_postgres.sh"
git fetch origin main
git merge --ff-only origin/main
UV_BIN=${UV_BIN:-/root/.local/bin/uv}
if [[ ! -x "$UV_BIN" ]]; then
  echo "uv executable not found at $UV_BIN; set UV_BIN to its absolute path" >&2
  exit 1
fi
"$UV_BIN" sync --frozen

MIGRATE_ENV=${MIGRATE_ENV:-$REPO/.env.migrate}
if [[ ! -f "$MIGRATE_ENV" ]]; then
  MIGRATE_ENV="$REPO/.env"
fi
MIGRATE_UNIT="wnba-deploy-migrate-$(date +%s)"
systemd-run --quiet --wait --pipe --collect \
  --unit="$MIGRATE_UNIT" \
  --property="WorkingDirectory=$REPO" \
  --property="EnvironmentFile=$MIGRATE_ENV" \
  /opt/wnba/repo/.venv/bin/wnba db migrate

install -m 0644 "$REPO"/infrastructure/systemd/*.service /etc/systemd/system/
install -m 0644 "$REPO"/infrastructure/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wnba-rule-learning.timer
systemctl restart wnba-web.service
/bin/bash "$REPO/infrastructure/monitor_health.sh"
