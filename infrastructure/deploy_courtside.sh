#!/usr/bin/env bash
set -euo pipefail

REPO=/opt/wnba/repo
cd "$REPO"

/bin/bash "$REPO/infrastructure/backup_postgres.sh"
git fetch origin main
git merge --ff-only origin/main
/usr/local/bin/uv sync --frozen

set -a
. "$REPO/.env.migrate"
set +a
/opt/wnba/repo/.venv/bin/wnba db migrate

systemctl daemon-reload
systemctl restart wnba-web.service
/bin/bash "$REPO/infrastructure/monitor_health.sh"
