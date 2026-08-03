#!/usr/bin/env bash
# Read-only inventory of the CourtSide Edge VPS.
#
# This script CREATES NOTHING, MODIFIES NOTHING and DELETES NOTHING. It only reads and
# reports. Run it before any migration or teardown.
#
# The question it exists to answer: what irreplaceable data is on this box? Historical
# player-prop odds cannot be bought retroactively at any price. If this server has been
# archiving them, that archive is the single most valuable asset in the project -- worth
# considerably more than the application code sitting next to it.
#
# Usage (Hostinger web terminal, as root):
#   bash vps_inventory.sh
#
set -uo pipefail

line() { printf '\n\033[1m== %s ==\033[0m\n' "$1"; }
note() { printf '   %s\n' "$1"; }

line "HOST"
hostname; uname -a; uptime | tr -s ' '
note "disk:"; df -h / /var 2>/dev/null | sed 's/^/   /'

line "SSH HOST KEY FINGERPRINTS (verify against what your client reports)"
for f in /etc/ssh/ssh_host_*_key.pub; do
  [ -f "$f" ] && ssh-keygen -lf "$f" 2>/dev/null | sed 's/^/   /'
done

line "DOCKER"
if command -v docker >/dev/null 2>&1; then
  docker ps -a --format '   {{.Names}}\t{{.Image}}\t{{.Status}}' 2>/dev/null
  note ""
  note "volumes (data usually lives here, NOT in the container):"
  docker volume ls --format '   {{.Name}}' 2>/dev/null
  note ""
  note "volume sizes:"
  docker system df -v 2>/dev/null | sed -n '/VOLUME NAME/,/^$/p' | sed 's/^/   /' | head -30
else
  note "docker not installed"
fi

line "APPLICATION DIRECTORIES"
for d in /root /opt /srv /home /var/www; do
  [ -d "$d" ] && find "$d" -maxdepth 3 \
      \( -name docker-compose.yml -o -name docker-compose.yaml -o -name .git -o -name '*.env' \) \
      2>/dev/null | head -20 | sed 's/^/   /'
done

line "LARGEST DIRECTORIES (top 25) -- where the data actually is"
du -xh --max-depth=3 /root /opt /srv /var/lib /home 2>/dev/null \
  | sort -rh | head -25 | sed 's/^/   /'

line "DATA FILES: parquet / csv / sqlite / json / dumps"
find /root /opt /srv /home /var/lib -maxdepth 6 -type f \
     \( -name '*.parquet' -o -name '*.csv' -o -name '*.db' -o -name '*.sqlite*' \
        -o -name '*.duckdb' -o -name '*.sql' -o -name '*.dump' -o -name '*.jsonl' \) \
     -size +64k 2>/dev/null | head -40 \
  | while read -r f; do printf '   %10s  %s\n' "$(du -h "$f" | cut -f1)" "$f"; done

line "POSTGRES"
PG=""
if command -v docker >/dev/null 2>&1; then
  PG=$(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | grep -i -m1 postgres | cut -f1)
fi
if [ -n "$PG" ]; then
  note "container: $PG"
  PSQL="docker exec -i $PG psql -U postgres -tAX"
  note "databases:"
  $PSQL -c "SELECT datname||'  '||pg_size_pretty(pg_database_size(datname)) FROM pg_database WHERE datistemplate=false;" 2>/dev/null | sed 's/^/     /'
  for DB in $($PSQL -c "SELECT datname FROM pg_database WHERE datistemplate=false AND datname<>'postgres';" 2>/dev/null); do
    note ""
    note "--- database: $DB ---"
    note "tables by size, with live row estimates:"
    docker exec -i "$PG" psql -U postgres -d "$DB" -tAX -c "
      SELECT '     '||relname||'  rows~'||n_live_tup||'  '||pg_size_pretty(pg_total_relation_size(relid))
      FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 25;" 2>/dev/null

    note ""
    note "ODDS/PROPS/LINE tables -- date coverage (the irreplaceable part):"
    for T in $(docker exec -i "$PG" psql -U postgres -d "$DB" -tAX -c "
        SELECT table_name FROM information_schema.tables
        WHERE table_schema='public'
          AND (table_name ILIKE '%odd%' OR table_name ILIKE '%prop%' OR table_name ILIKE '%line%'
               OR table_name ILIKE '%quote%' OR table_name ILIKE '%market%' OR table_name ILIKE '%projection%');" 2>/dev/null); do
      note "  table: $T"
      # Report every timestamp column and its range. Two distinct timestamps (when the line
      # was published vs when it was captured) is what makes an archive usable for
      # leakage-free backtesting. One timestamp means valid-time only.
      docker exec -i "$PG" psql -U postgres -d "$DB" -tAX -c "
        SELECT '       ts col: '||column_name FROM information_schema.columns
        WHERE table_name='$T' AND data_type LIKE 'timestamp%';" 2>/dev/null
      docker exec -i "$PG" psql -U postgres -d "$DB" -tAX -c "
        SELECT '       rows='||count(*) FROM public.\"$T\";" 2>/dev/null
      for C in $(docker exec -i "$PG" psql -U postgres -d "$DB" -tAX -c "
          SELECT column_name FROM information_schema.columns
          WHERE table_name='$T' AND data_type LIKE 'timestamp%';" 2>/dev/null); do
        docker exec -i "$PG" psql -U postgres -d "$DB" -tAX -c "
          SELECT '       $C: '||coalesce(min(\"$C\")::text,'-')||'  ->  '||coalesce(max(\"$C\")::text,'-')
          FROM public.\"$T\";" 2>/dev/null
      done
    done
  done
else
  note "no postgres container found; checking for a host install"
  command -v psql >/dev/null 2>&1 && note "psql present on host -- inspect manually" || note "no psql on host"
fi

line "REDIS"
R=$(docker ps --format '{{.Names}}\t{{.Image}}' 2>/dev/null | grep -i -m1 redis | cut -f1)
if [ -n "$R" ]; then
  note "container: $R"
  docker exec -i "$R" redis-cli DBSIZE 2>/dev/null | sed 's/^/     keys: /'
  docker exec -i "$R" redis-cli INFO keyspace 2>/dev/null | sed 's/^/     /'
else
  note "no redis container"
fi

line "WEB SERVER / TLS (what currently answers for courtside-edge.com)"
for f in /etc/nginx/sites-enabled/* /etc/caddy/Caddyfile; do
  [ -e "$f" ] && { note "$f:"; grep -E 'server_name|proxy_pass|root|listen|reverse_proxy' "$f" 2>/dev/null | sed 's/^/     /' | head -15; }
done
[ -d /etc/letsencrypt/live ] && { note "certificates:"; ls /etc/letsencrypt/live 2>/dev/null | sed 's/^/     /'; }

line "CRON / TIMERS (is something still collecting odds right now?)"
crontab -l 2>/dev/null | grep -v '^#' | sed 's/^/   /' | head -20
ls /etc/cron.d 2>/dev/null | sed 's/^/   cron.d: /'
systemctl list-timers --no-pager 2>/dev/null | head -12 | sed 's/^/   /'

line "DONE"
note "Nothing was modified. Send this output back before anything is deleted."
