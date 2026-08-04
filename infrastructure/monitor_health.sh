#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=${WNBA_BACKUP_DIR:-/var/lib/wnba/backups}
MAX_BACKUP_AGE_SECONDS=${WNBA_MAX_BACKUP_AGE_SECONDS:-129600}
MAX_DISK_PERCENT=${WNBA_MAX_DISK_PERCENT:-80}
failures=()

curl --fail --silent --show-error --max-time 15 \
    https://courtside-edge.com/api/health >/dev/null || failures+=(api_health)

for unit in wnba-archiver.timer wnba-injuries.timer wnba-roles.timer wnba-effects.timer \
    wnba-matchups.timer wnba-forecast.timer wnba-stats.timer wnba-settlement.timer \
    wnba-backup.timer wnba-liveness.timer wnba-rule-learning.timer; do
    systemctl is-active --quiet "$unit" || failures+=("$unit")
done

latest=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'wnba-*.dump' -printf '%T@\n' \
    | sort -nr | head -n 1 || true)
if [[ -z "$latest" ]] || (( $(date +%s) - ${latest%.*} > MAX_BACKUP_AGE_SECONDS )); then
    failures+=(backup_age)
fi

disk_percent=$(df --output=pcent / | tail -n 1 | tr -dc '0-9')
(( disk_percent < MAX_DISK_PERCENT )) || failures+=(disk_usage)
docker exec wnba-postgres pg_isready --username courtside --dbname courtside >/dev/null \
    || failures+=(postgres)

if (( ${#failures[@]} > 0 )); then
    message="CourtSide health failures: ${failures[*]}"
    logger --tag courtside-monitor --priority user.err "$message"
    if [[ -n "${WNBA_ALERT_WEBHOOK_URL:-}" ]]; then
        escaped=${message//\"/\\\"}
        curl --fail --silent --show-error --max-time 15 -H 'content-type: application/json' \
            --data "{\"text\":\"$escaped\"}" "$WNBA_ALERT_WEBHOOK_URL" >/dev/null
    fi
    printf '%s\n' "$message" >&2
    exit 1
fi

printf 'health checks passed backup_age_ok=true disk_percent=%s\n' "$disk_percent"
