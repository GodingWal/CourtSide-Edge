# Backup and restore

## Backup policy

- Nightly custom-format PostgreSQL dumps of schema `wnba`.
- SHA-256 sidecar and `pg_restore --list` verification on every backup.
- Fourteen-day local retention.
- Off-host replication is mandatory for disaster recovery and must be configured through
  `WNBA_OFFSITE_BACKUP_COMMAND`; local copies alone do not satisfy the readiness check.

## Restore drill

Run only on the VPS:

```bash
sudo /opt/wnba/repo/infrastructure/restore_drill.sh
```

The script creates a disposable database, restores the latest dump, verifies critical table
counts and removes the disposable database. It never restores over production.

## Production recovery

1. Stop writers and the web process.
2. Preserve the damaged database and logs.
3. Verify the chosen dump checksum and archive listing.
4. Restore into a new database name.
5. Run migrations, row-count checks and API smoke tests against the new database.
6. Change the connection only after verification; retain the old database until sign-off.
