#!/usr/bin/env bash
# Produce an encrypted-at-rest-by-host-permissions, compressed logical backup of the WNBA
# schema. The backup remains local first; a separate off-host copy is required before this
# is considered disaster recovery.
set -euo pipefail

BACKUP_DIR=${WNBA_BACKUP_DIR:-/var/lib/wnba/backups}
CONTAINER=${WNBA_POSTGRES_CONTAINER:-wnba-postgres}
DATABASE=${WNBA_POSTGRES_DATABASE:-courtside}
USER_NAME=${WNBA_POSTGRES_USER:-courtside}
RETENTION_DAYS=${WNBA_BACKUP_RETENTION_DAYS:-14}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TARGET="$BACKUP_DIR/wnba-$STAMP.dump"

install -d -m 0700 "$BACKUP_DIR"
umask 077

docker exec "$CONTAINER" pg_dump \
    --username "$USER_NAME" \
    --dbname "$DATABASE" \
    --schema wnba \
    --format custom \
    --compress 9 > "$TARGET"

test -s "$TARGET"
sha256sum "$TARGET" > "$TARGET.sha256"

# Verify inside the Postgres container so the host does not need client packages installed.
# With no filename pg_restore reads the custom archive from standard input.
docker exec -i "$CONTAINER" pg_restore --list < "$TARGET" >/dev/null

if [[ -n "${WNBA_OFFSITE_BACKUP_COMMAND:-}" ]]; then
    read -r -a offsite_command <<< "$WNBA_OFFSITE_BACKUP_COMMAND"
    "${offsite_command[@]}" "$TARGET" "$TARGET.sha256"
    touch "$BACKUP_DIR/.last-offsite-success"
fi

find "$BACKUP_DIR" -type f \( -name 'wnba-*.dump' -o -name 'wnba-*.dump.sha256' \) \
    -mtime "+$RETENTION_DAYS" -delete

printf 'created %s (%s bytes)\n' "$TARGET" "$(stat -c %s "$TARGET")"
