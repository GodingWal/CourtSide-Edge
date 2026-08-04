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

install -m 0644 "$REPO"/infrastructure/systemd/*.service /etc/systemd/system/
install -m 0644 "$REPO"/infrastructure/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wnba-rule-learning.timer
systemctl restart wnba-web.service
/bin/bash "$REPO/infrastructure/monitor_health.sh"
