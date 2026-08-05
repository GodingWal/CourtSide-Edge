# Operations runbook

## Service map

Nginx terminates TLS and proxies the private console to Uvicorn on `127.0.0.1:8090`.
PostgreSQL runs in `wnba-postgres` and is exposed only on `127.0.0.1:5432`. Systemd timers run
market archival, injuries, roles, teammate effects, matchups, forecasts, stats, settlement,
readiness monitoring and backups.

## Daily checks

```bash
systemctl --failed
systemctl list-timers 'wnba-*'
curl -fsS https://courtside-edge.com/api/health
journalctl -u wnba-monitor.service -n 50 --no-pager
```

The health endpoint may remain public but contains no forecasts, evidence or secrets. Every
other route requires the owner credential.

## Common recovery

- Stale market archive: run `sudo systemctl start wnba-archiver.service`, then inspect its log.
- Missing forecasts: verify roles/effects/matchups, then start `wnba-forecast.service`.
- Failed migration: stop deployment, keep the old web process, and restore the pre-deploy dump.
- DeepSeek failure: forecasts continue; research fails closed and may be retried manually.
  Congestion and transport faults are already retried inside the client, so a run recorded as
  `failed` has exhausted `DEEPSEEK_MAX_ATTEMPTS` or was rejected on validation -- read `error`
  before retrying. A run stuck at `running` blocks further spend on that projection until the
  provider's whole timeout budget has elapsed, after which the next attempt reclaims it.
- Disk above 80%: inspect PostgreSQL, Docker and backup growth before deleting anything.

## Escalation

The owner is the only operator. Disable recommendations first, preserve evidence, record UTC
timestamps and avoid destructive repair until a verified backup exists.
