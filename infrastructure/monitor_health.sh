#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=${WNBA_BACKUP_DIR:-/var/lib/wnba/backups}
MAX_BACKUP_AGE_SECONDS=${WNBA_MAX_BACKUP_AGE_SECONDS:-129600}
MAX_DISK_PERCENT=${WNBA_MAX_DISK_PERCENT:-80}
# Local-only backups are not disaster recovery (see backup_postgres.sh). The offsite copy is
# required unless explicitly waived for a host that genuinely has none.
ALLOW_LOCAL_ONLY_BACKUPS=${WNBA_ALLOW_LOCAL_ONLY_BACKUPS:-0}
# Optional external dead-man's switch (e.g. healthchecks.io). Pinged only after every check
# passes, so a dead VPS alerts by silence rather than relying on its own webhook.
PING_URL=${WNBA_HEALTHCHECK_PING_URL:-}
failures=()

curl --fail --silent --show-error --max-time 15 \
    https://courtside-edge.com/api/health >/dev/null || failures+=(api_health)

for unit in wnba-archiver.timer wnba-injuries.timer wnba-roles.timer wnba-effects.timer \
    wnba-matchups.timer wnba-forecast.timer wnba-stats.timer wnba-settlement.timer \
    wnba-backup.timer wnba-liveness.timer wnba-rule-learning.timer wnba-pat-research.timer; do
    systemctl is-active --quiet "$unit" || failures+=("$unit")
done

latest=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'wnba-*.dump' -printf '%T@\n' \
    | sort -nr | head -n 1 || true)
if [[ -z "$latest" ]] || (( $(date +%s) - ${latest%.*} > MAX_BACKUP_AGE_SECONDS )); then
    failures+=(backup_age)
fi

if [[ "$ALLOW_LOCAL_ONLY_BACKUPS" != "1" ]]; then
    offsite_marker="$BACKUP_DIR/.last-offsite-success"
    if [[ ! -f "$offsite_marker" ]]; then
        failures+=(offsite_backup_missing)
    elif (( $(date +%s) - $(stat -c %Y "$offsite_marker") > MAX_BACKUP_AGE_SECONDS )); then
        failures+=(offsite_backup_age)
    fi
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

if [[ -n "$PING_URL" ]]; then
    curl --fail --silent --show-error --max-time 10 "$PING_URL" >/dev/null || \
        logger --tag courtside-monitor --priority user.warning "dead-man ping failed"
fi

printf 'health checks passed backup_age_ok=true disk_percent=%s\n' "$disk_percent"
