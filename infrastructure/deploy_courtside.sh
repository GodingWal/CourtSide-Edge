#!/usr/bin/env bash
set -euo pipefail

REPO=/opt/wnba/repo
cd "$REPO"

if [[ "${COURTSIDE_DEPLOY_REEXEC:-0}" != "1" ]]; then
  /bin/bash "$REPO/infrastructure/backup_postgres.sh"
fi
previous_revision=$(git rev-parse HEAD)
git fetch origin main
git merge --ff-only origin/main
# Bash may have read the old script before the merge replaced it. Re-exec once so deployment
# changes in the release govern that same release; the guard prevents a duplicate backup/loop.
if [[ "${COURTSIDE_DEPLOY_REEXEC:-0}" != "1" ]] && \
    ! git diff --quiet "$previous_revision" HEAD -- infrastructure/deploy_courtside.sh; then
  export COURTSIDE_DEPLOY_REEXEC=1
  exec /bin/bash "$REPO/infrastructure/deploy_courtside.sh"
fi

# uv must live on a system path, not in root's home directory. Install it once with:
#   curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
UV_BIN=${UV_BIN:-$(command -v uv || true)}
if [[ -z "$UV_BIN" || ! -x "$UV_BIN" ]]; then
  echo "uv executable not found on PATH; install it to /usr/local/bin or set UV_BIN" >&2
  exit 1
fi
"$UV_BIN" sync --frozen

# Fail closed: the migration-only least-privilege credential is required. Falling back to
# the general .env would silently run migrations with the web role.
MIGRATE_ENV=${MIGRATE_ENV:-$REPO/.env.migrate}
if [[ ! -f "$MIGRATE_ENV" ]]; then
  echo "migration environment file not found at $MIGRATE_ENV; refusing to migrate with the web credential" >&2
  exit 1
fi
MIGRATE_UNIT="wnba-deploy-migrate-$(date +%s)"
systemd-run --quiet --wait --pipe --collect \
  --unit="$MIGRATE_UNIT" \
  --property="WorkingDirectory=$REPO" \
  --property="EnvironmentFile=$MIGRATE_ENV" \
  /opt/wnba/repo/.venv/bin/wnba db migrate

getent group wnba >/dev/null || groupadd --system wnba
id wnba >/dev/null 2>&1 || \
  useradd --system --gid wnba --home-dir /var/lib/wnba --shell /usr/sbin/nologin wnba
install -d -o wnba -g wnba -m 0750 /var/cache/wnba
chgrp wnba "$REPO/.env"
chmod 0640 "$REPO/.env"

install -m 0644 "$REPO"/infrastructure/systemd/*.service /etc/systemd/system/
install -m 0644 "$REPO"/infrastructure/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload

# Enable every timer shipped by the repo. A fresh host rebuilt with this script must come
# up complete; enabling only a subset leaves the monitor permanently red.
for timer_file in "$REPO"/infrastructure/systemd/wnba-*.timer; do
  systemctl enable --now "$(basename "$timer_file")"
done

systemctl restart wnba-web.service
for _attempt in {1..15}; do
  if curl --fail --silent --max-time 3 http://127.0.0.1:8090/api/health >/dev/null; then
    break
  fi
  sleep 2
done
curl --fail --silent --show-error --max-time 5 \
  http://127.0.0.1:8090/api/health >/dev/null
# A failed oneshot remains failed until reset even after its code is repaired. Clear only the
# smoke-tested units, then exercise each production path immediately. These jobs are safe and
# idempotent; a deploy that cannot forecast, settle, fit trust, or simulate is not healthy.
systemctl reset-failed wnba-forecast.service wnba-settlement.service \
  wnba-trust-fit.service wnba-game-simulation.service || true
systemctl restart wnba-forecast.service
systemctl restart wnba-settlement.service
systemctl restart wnba-trust-fit.service
systemctl restart wnba-game-simulation.service
/bin/bash "$REPO/infrastructure/monitor_health.sh"
