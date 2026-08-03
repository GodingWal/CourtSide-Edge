#!/usr/bin/env bash
# Pull the newest Postgres dump off the VPS and verify it.
#
# Why this exists: the VPS already takes daily dumps, but every copy lives on the same disk
# as the database it protects. That defends against "someone dropped a table". It does not
# defend against losing the box -- which is the failure mode that would destroy the market
# archive, and the market archive is the one asset that cannot be rebuilt at any price.
#
# Provider snapshots are not a substitute: as of this writing the only Hostinger backup was
# three months stale, and the API refuses to create one (405) -- they must be triggered from
# hPanel by hand.
#
# Usage:
#   bash scripts/pull_backup.sh                 # pull newest into ./data/legacy/postgres
#   WNBA_BACKUP_DEST=/mnt/d/backups bash scripts/pull_backup.sh
#
# Schedule it on the workstation (Task Scheduler on Windows, cron elsewhere). A backup that
# depends on someone remembering to run it is a backup that silently stops happening.

set -euo pipefail

VPS_HOST="${WNBA_VPS_HOST:-root@76.13.100.125}"
SSH_KEY="${WNBA_SSH_KEY:-$HOME/.ssh/id_ed25519}"
REMOTE_DIR="${WNBA_REMOTE_BACKUP_DIR:-/var/lib/wnba/backups}"
DEST="${WNBA_BACKUP_DEST:-$(cd "$(dirname "$0")/.." && pwd)/data/legacy/postgres}"
KEEP="${WNBA_BACKUP_KEEP:-7}"

ssh_opts=(-o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=15 -i "$SSH_KEY")

mkdir -p "$DEST"

newest=$(ssh "${ssh_opts[@]}" "$VPS_HOST" "ls -t $REMOTE_DIR/*.dump 2>/dev/null | head -1")
if [[ -z "$newest" ]]; then
    echo "FAIL: no dump found in $REMOTE_DIR on $VPS_HOST" >&2
    exit 1
fi

name=$(basename "$newest")
if [[ -f "$DEST/$name" ]]; then
    echo "already local: $name"
else
    echo "pulling $name"
    scp -q "${ssh_opts[@]}" "$VPS_HOST:$newest" "$VPS_HOST:$newest.sha256" "$DEST/"
fi

# Verify against the checksum the server recorded at dump time, not one we compute here from
# the same bytes we just received -- that would only prove scp is deterministic.
expected=$(awk '{print $1}' "$DEST/$name.sha256")
actual=$(sha256sum "$DEST/$name" | awk '{print $1}')
if [[ "$expected" != "$actual" ]]; then
    echo "FAIL: checksum mismatch for $name" >&2
    echo "  expected $expected" >&2
    echo "  actual   $actual" >&2
    rm -f "$DEST/$name"          # a corrupt backup is worse than an absent one: it lies
    exit 1
fi

size=$(du -h "$DEST/$name" | cut -f1)
echo "OK  $name  ($size)  sha256 ${actual:0:16}…"

# Retain the last N. Deleting the oldest only after the new one has verified means a failed
# pull can never leave us with fewer good copies than we started with.
mapfile -t old < <(ls -t "$DEST"/*.dump 2>/dev/null | tail -n +$((KEEP + 1)))
for stale in "${old[@]:-}"; do
    [[ -n "$stale" ]] || continue
    echo "  pruning $(basename "$stale")"
    rm -f "$stale" "$stale.sha256"
done

echo "local copies: $(ls "$DEST"/*.dump 2>/dev/null | wc -l) (keep=$KEEP)"
