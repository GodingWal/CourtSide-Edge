#!/usr/bin/env bash
# One-time cleanup: remove DEMO data that earlier builds seeded into the
# production SQLite DB (fake bets, generated bankroll history, sample events,
# audit traces, hedges, demo agent context). Reference data (players,
# settings) is kept.
#
# ⚠ This deletes ALL rows in those tables — including any real bets entered so
# far. Only run while transitioning the site to live data.
#
# Usage (on the VPS, from repo root):  CONFIRM=yes bash deploy/scripts/purge-demo-data.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DB="${DATABASE_PATH:-$REPO_ROOT/data/hoopstats_wnba.db}"

[ "${CONFIRM:-}" = "yes" ] || { echo "✗ Refusing: set CONFIRM=yes to wipe demo data from $DB" >&2; exit 1; }
[ -f "$DB" ] || { echo "✗ Database not found at $DB" >&2; exit 1; }

echo "→ Purging demo data from $DB…"
sqlite3 "$DB" <<'SQL'
DELETE FROM bets;
DELETE FROM bankroll_history;
DELETE FROM qualitative_events;
DELETE FROM decision_audit;
DELETE FROM hedging_opportunities;
DELETE FROM agent_context_store;
VACUUM;
SQL

echo "✓ Demo data purged. The dashboard now reflects only live data."
echo "  Restart the web tier so in-memory state resets:"
echo "  docker compose --env-file deploy/env/web.env -f deploy/docker-compose.web.yml restart web_server"
