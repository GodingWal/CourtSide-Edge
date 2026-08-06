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
- DeepSeek failure: forecasts continue. Individual agents fail open -- a run completes with
  the roles that answered, and each missing one is a `fallback` row in `wnba.model_advisories`.
  A run only fails when every role failed. Check `disposition` and `failure_reason` there before
  suspecting the pipeline. Congestion and transport faults are already retried inside the
  client, so a run recorded as `failed` has exhausted `DEEPSEEK_MAX_ATTEMPTS` or was rejected
  on validation -- read `error` before retrying. A run stuck at `running` blocks further spend
  on that projection until the provider's whole timeout budget has elapsed, after which the
  next attempt reclaims it.
- DeepSeek failure: forecasts continue. Congestion and transport faults are already retried
  inside the client, so a run recorded as `failed` has exhausted `DEEPSEEK_MAX_ATTEMPTS` or was
  rejected on validation -- read `error` before retrying. A run stuck at `running` blocks further
  spend on that projection until the provider's whole timeout budget has elapsed, after which the
  next attempt reclaims it.
  A single analyst failing is not a failed run: stage one keeps whatever answered, and each lost
  role is a `fallback` row in `wnba.model_advisories` with its reason. A run fails only when
  *every* analyst failed. Read `disposition` and `failure_reason` there before suspecting the
  pipeline.
- A completed run with no row in `wnba.research_verdicts` is not a bug. It means the skeptic
  failed: the analyses are kept, and no verdict is synthesised from an unreviewed file, because
  one computed without the review would report every claim as uncontested and read exactly like
  a file nobody could fault. Look for a `fallback` advisory naming the `skeptic` role, and re-run
  the projection if the market has not locked.
- Owner picks stuck on `pending`: `wnba learning settle` settles them alongside paper episodes.
  Legs whose `player_id` is null were confirmed under a name that matched no player, or matched
  more than one; they never settle, and that is deliberate. Re-enter them with a name from the
  board rather than relaxing the match.
- Disk above 80%: inspect PostgreSQL, Docker and backup growth before deleting anything.

## Champion/challenger experiments

Opening an experiment makes the live forecast run also score the board with that challenger and
write the result to `wnba.challenger_predictions`. Nothing else reads that table, so the
recommendations on the board are unchanged by an open experiment.

```bash
wnba learning experiments open state-space-role --opened-by "<name>"
wnba learning experiments list
wnba learning experiments evaluate          # also runs weekly in wnba-rule-learning.service
```

`evaluate` reaches a verdict and stops. Promotion and rollback are separate commands that each
require a named human and a reason of at least ten characters:

```bash
wnba learning experiments promote  <experiment-id> --approved-by "<name>" --reason "<why>"
wnba learning experiments rollback <experiment-id> --rolled-back-by "<name>" --reason "<why>"
```

If a promotion looks wrong after the fact, roll it back rather than editing the row: the
promotion stays in the record deliberately. A challenger whose failure rate or p95 latency is
climbing should be abandoned (`experiments abandon`) rather than left collecting predictions it
cannot produce.

## Escalation

The owner is the only operator. Disable recommendations first, preserve evidence, record UTC
timestamps and avoid destructive repair until a verified backup exists.
