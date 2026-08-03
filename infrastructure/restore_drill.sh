#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR=${WNBA_BACKUP_DIR:-/var/lib/wnba/backups}
CONTAINER=${WNBA_POSTGRES_CONTAINER:-wnba-postgres}
ADMIN_USER=${WNBA_POSTGRES_USER:-courtside}
SOURCE_DATABASE=${WNBA_POSTGRES_DATABASE:-courtside}
DRILL_DATABASE="courtside_restore_drill_$(date -u +%Y%m%d%H%M%S)"
LATEST=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name 'wnba-*.dump' -printf '%T@ %p\n' \
    | sort -nr | head -n 1 | cut -d' ' -f2-)

test -n "$LATEST"
sha256sum --check "$LATEST.sha256"
docker exec -i "$CONTAINER" pg_restore --list < "$LATEST" >/dev/null

cleanup() {
    docker exec "$CONTAINER" dropdb --username "$ADMIN_USER" --if-exists "$DRILL_DATABASE"
}
trap cleanup EXIT

docker exec "$CONTAINER" createdb --username "$ADMIN_USER" "$DRILL_DATABASE"
docker exec -i "$CONTAINER" pg_restore --username "$ADMIN_USER" --dbname "$DRILL_DATABASE" \
    --no-owner --no-privileges < "$LATEST"

production_lines=$(docker exec "$CONTAINER" psql --username "$ADMIN_USER" \
    --dbname "$SOURCE_DATABASE" --tuples-only --no-align \
    --command 'SELECT count(*) FROM wnba.player_game_lines')
restored_lines=$(docker exec "$CONTAINER" psql --username "$ADMIN_USER" \
    --dbname "$DRILL_DATABASE" --tuples-only --no-align \
    --command 'SELECT count(*) FROM wnba.player_game_lines')
test "$production_lines" = "$restored_lines"

printf 'restore drill passed database=%s player_game_lines=%s backup=%s\n' \
    "$DRILL_DATABASE" "$restored_lines" "$LATEST"
