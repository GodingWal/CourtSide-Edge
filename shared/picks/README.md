# shared/picks — Pick Validation & Statistical Rigor

Library backing the pick pipeline validation mesh (Agents 24–26). Origin:
post-mortem of the 2026-06-11 WNBA slate (Clark "debut" hallucination,
edge-sign mismatches, sub-noise edges published as Buys).

## Pipeline topology (P2-1)

```
projection agent  --publish-->  picks.raw
validation agent  --subscribe-> picks.raw                      (Agent 24)
validation agent  --publish-->  picks.validated   (pass)
validation agent  --publish-->  picks.rejected    (fail, with reason codes)
narrative agent   --subscribe-> picks.validated
narrative agent   --publish-->  picks.narrated
claim verifier    --subscribe-> picks.narrated                 (Agent 25)
claim verifier    --publish-->  picks.publishable / picks.rejected
publisher         --subscribe-> picks.publishable              (Agent 26)
                                ^^^ ONLY subscription that reaches users
```

Lineage: Agent 24 stamps every passing `pick_id` in Redis
(`picks:lineage:validated:*`); Agent 25 rejects any `picks.narrated` message
whose pick was never stamped (`LINEAGE_VIOLATION`). Every rejection carries
`{pick_id, reason_code, payload_snapshot, ts}` on `picks.rejected`.

## Modules

| Module | Spec | Contents |
|---|---|---|
| `models.py` | P0-2, P0-3 | Frozen `Pick` (computed-only `edge`, `extra="forbid"`), `NarrativePayload`, `ValidationResult` |
| `validation.py` | P0-1, P0-4, P1-2, P1-3 | `validate_pick()` — pure function, no I/O: edge sign, injury staleness (24h), roster, breakeven threshold, blowout escalation |
| `breakeven.py` | P1-2 | Payout-implied breakeven solver (power closed-form == numeric flex solver), `publish_threshold = breakeven + margin` |
| `numeric_scan.py` | P0-2 | Post-generation scan: narrative numbers absent from payload ⟹ `FABRICATED_NUMERIC` |
| `claims.py` | P0-3 | Deterministic claim extraction + verification (debut/rookie/return/injury/entities) ⟹ `UNGROUNDED_CLAIM` |
| `distributions.py` | P1-1 | Negative-binomial dispersion fit (≥15 games, positional fallback), `{mean, std, p_over}` outputs |
| `minutes_risk.py` | P1-3 | Blowout minutes haircut (−8% @ 70% win prob → −15% @ 85%+), `BLOWOUT_RISK` flags |
| `correlation.py` | P1-4 | Gaussian-copula joint hit probability, correlation-adjusted entry EV ⟹ `CORRELATION_EV_FAIL` |
| `grading.py` | P3-1 | A–D grades; power entries A/B only, C flex-only, D never published |
| `line_tracking.py` | P2-2 | Timestamped line snapshots, adverse-move (≥0.5) re-validation + LEAN demotion |
| `calibration.py` | P2-3 | Append-only `pick_log`, Brier/reliability/CLV weekly report (`run_weekly_report`) |
| `narrative.py` | P0-2/3 | Grounded system prompt; template mode interpolates numbers in code |
| `roster.py`, `channels.py` | P0-4, P2-1 | Roster store (Redis hash), channel constants + lineage helpers |
| `picks_config.json` | — | Payout tables, margins, blowout/correlation/dispersion/grading parameters. Override path via `PICKS_CONFIG_PATH`. |

Payout/breakeven values live in config, not code — refresh the JSON when
books change payouts. Breakeven reference (config-derived, unit-tested):
PrizePicks 2-leg power ≈ 57.7%, 3-leg power ≈ 58.5%, Underdog 3-leg ≈ 55.0%.

## Reason code registry

| Code | Stage | Meaning |
|---|---|---|
| `EDGE_SIGN_MISMATCH` | validation | Recommendation contradicts edge sign |
| `SCHEMA_VIOLATION` | validation | Message fails the Pick/payload schema (e.g. mean-only projection) |
| `FABRICATED_NUMERIC` | claim verify | Narrative contains a number absent from payload |
| `UNGROUNDED_CLAIM` | claim verify | Narrative factual claim unmappable to payload |
| `LINEAGE_VIOLATION` | claim verify | Narrated pick has no `picks.validated` ancestor |
| `STALE_INJURY_DATA` | validation | Injury record older than 24h |
| `ROSTER_MISMATCH` | validation | Player/team assertion contradicts roster table |
| `BELOW_THRESHOLD` | validation | Hit probability under breakeven + margin (→ LEAN) |
| `BLOWOUT_RISK` | validation | Spread/win-prob escalated threshold not met |
| `ADVERSE_LINE_MOVE` | publication | Line moved against pick ≥ 0.5 since capture |
| `CORRELATION_EV_FAIL` | entry build | Joint EV below breakeven after correlation adjustment |

## Golden regression suite (P3-2)

`agents/golden_fixtures/*.json` + `agents/test_golden_regressions.py` replay
every observed production failure (G1–G7 from the 2026-06-11 slate) through
the exact validation + claim-verification path. CI runs the suite as a
dedicated gate before the full test run.

**Convention: every production incident adds at least one golden fixture in
the fix PR.** A fixture is a JSON payload with `pick`, `payload`,
optional `narrative`, and `expect` (`status`, `codes`, optional `flags`);
injury freshness is expressed relative to the suite's frozen reference time
via `last_updated_hours_ago`.

## Weekly calibration job (P2-3)

```python
from shared.picks.calibration import run_weekly_report
run_weekly_report("./data/hoopstats_wnba.db", "./data/calibration_report.md")
```

Reports Brier score (target ≤ 0.24), reliability buckets, average CLV
(closing line − capture line signed by pick direction — the primary KPI),
and per-reason-code rejection counts, filterable per `model_version`.
Schedule via cron or a mesh timer; LEAN picks are logged too, so calibration
covers the picks that were *not* published.
