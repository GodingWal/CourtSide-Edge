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
- Disk above 80%: inspect PostgreSQL, Docker and backup growth before deleting anything.

## Research investigations

The coordinator queues investigations from measured changes and calls no model, so planning is
free. Executing a plan spends provider tokens and is a separate command deliberately.

```bash
wnba research plan --limit 15     # queue: free, calls no model
wnba research run-queue --limit 4 # run the top of the queue, capped
wnba research queue               # what the coordinator wants investigated, and why
wnba research execute <plan-id>   # one specific plan: audit, two rounds, synthesis
wnba research score-credibility   # agent credibility, evidence ranking, source reliability
```

`wnba-research.timer` runs `plan` then `run-queue --limit 4` every half hour. The cap is the
point: an injury report landing for a whole team twenty minutes before tip would otherwise turn
one evening into a provider bill, and those investigations would arrive too late to inform
anything. Whatever is left stays queued for the next pass or expires with the market.

A plan that comes back `blocked` is a correct outcome, not a failure: the data auditor found the
inputs contradictory or archival and stopped before any model was called. Fix the input the
finding names and re-plan; the blocked audit stays in the record.

`wnba research author-rules` asks the research director to propose analyst rules from measured
failure patterns. Everything it proposes lands as `proposed`, cannot fire, and needs a backtest
and then a named human approver who is not the proposer — the database refuses anything else.

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
