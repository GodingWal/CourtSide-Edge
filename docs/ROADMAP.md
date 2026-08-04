# Delivery roadmap

Updated: 2026-08-04

The platform is in **Phase 3: private shadow analysis**. Live and rescued historical markets,
five-component forecasts, rolling-origin replay, paper settlement and the learning memory are
operational. Automated wager execution remains permanently out of scope.

## Current state

| Capability | State | Evidence |
|---|---|---|
| Strict domain and ontology | Complete | Pydantic models, ontology YAML, drift tests |
| Bitemporal PostgreSQL core | Complete | `wnba` schema and point-in-time leakage tests |
| Market archive | Live | Underdog poll every 15 minutes; changed states only |
| Correlated entry pricing | Complete as a library | Copula simulation and payout tests |
| VPS web console | Live | `https://courtside-edge.com` |
| PostgreSQL backup | Nightly local backup | Restore-list and checksum verification; 14-day retention |
| Historical statistical data | Canonical | More than 16,000 player lines with ESPN lineage |
| Historical market data | Partially normalized | 4,571 quotes mapped to 46 games; ambiguous rows retained |
| Forecasts and recommendations | Shadow/live | Five components and immutable paper episodes |
| Shared production/replay scorer | Complete | `score_prop` is the only forecasting code path |
| Fitted calibration and weights | Operational | Isotonic maps, stacked weights, edge shrinkage |
| Analyst rules in the forecast path | Operational | Active and shadow firings recorded per episode |
| Walk-forward evaluation | Operational | Five pre-tip snapshots and benchmark comparisons |
| PAT-style research | Awaiting API key | Five DeepSeek roles with cited expiring claims |
| Learning loop | Operational | Calibration, drift, attribution, feedback and proposals |
| Video intelligence | Not started | Experimental track remains isolated |

## Critical path

### 1. Normalize historical basketball data

- Preserve the legacy SQLite tables in immutable staging tables.
- Backfill canonical schedules, teams, players and complete box scores from ESPN/wehoop.
- Resolve aliases using source IDs; do not fuzzy-merge players automatically.
- Add daily schedule, roster, box-score and injury ingestion.
- Reconcile game/player totals across sources and quarantine disagreements.

Exit gate: at least two complete seasons can be reconstructed point-in-time, with documented
coverage and no unresolved identity collisions in the training set.

### 2. Build the statistical baseline

- Minutes distribution: availability, start probability, rotation stability and blowout risk.
- Per-minute opportunity models for points, rebounds, assists and three-pointers.
- Conversion models and a correlated possession/stat simulation.
- Baselines: season average, last-five, minutes x rate and market-implied probability.
- Rolling-origin backtests at evening-before, 6h, 2h, 30m and 10m snapshots.

Exit gate: production candidates beat the simple minutes x rate baseline on held-out log loss
and calibration without material subgroup degradation.

Status: the replay and the live board now share one scorer, so this comparison finally measures
the deployed model. The comparison itself has not been re-run since the change, and the prior
0.3.1 figures were withdrawn rather than carried forward -- they described a replay-only ensemble
that shared a single component with production.

### 3. Produce paper decisions and settle them

- Generate immutable `DecisionEpisode` records from live quotes.
- Store model run, feature snapshot, evidence and random seed.
- Freeze paper decisions before tip; never rewrite them after news.
- Settle outcomes, Brier score, log loss and line value automatically.
- Add calibration, drift, segment and drawdown views to the console.

Exit gate: 500+ out-of-sample paper recommendations with stable calibration and positive line
value. Profit alone is not a gate.

### 4. Add market intelligence

- Add another lawful free source if one remains stable and permitted.
- Build consensus, stale-line detection, opening/closing designation and source reliability.
- Verify payout tables from the live products; remove the unverified defaults.
- Model cross-player and same-game dependence, not a single scalar correlation.

### 5. Add PAT-style research and the learning loop

- Evidence retrieval with immutable source documents and claim expiry.
- Availability, rotation and matchup analysts plus an independent skeptic.
- Reject uncited claims; agents may propose features but never write forecast probabilities.
- Postgame error attribution, analyst feedback, hypothesis registry and weekly proposals.
- Champion/challenger evaluation, shadow deployment and human-only promotion.
- Feed measured miscalibration back into the forecast rather than only recording it. Done:
  calibration maps, ensemble weights and edge shrinkage are refit at settlement and applied on
  the next run, each adopted only when it beats the status quo out of fold.

### 6. Add video intelligence as an experimental sensor

- Import the referenced basketball-analysis project into an isolated dependency group.
- Establish player/ball tracking and court-calibration benchmarks on licensed footage.
- Add identity resolution, jersey OCR, event alignment and a low-confidence review queue.
- Promote only features that improve held-out forecasts; production cannot depend on video.

## VPS work

### Required before model training

- Automated off-host backup replication and a tested restore drill.
- Normalize the rescued legacy data into staging, then canonical tables.
- Monitoring for stale polls, parser rejection, database growth, backup failure and API health.
- Separate least-privilege database roles for migrations, ingestion and read-only web access.

### Required before public analyst use

- Authentication for non-public evidence, overrides and administrative actions.
- Rate limits, structured request logs, error tracking and an incident runbook.
- CI/CD that runs the full quality gate and migrations before a controlled restart.
- Retire the 46 legacy containers only after every useful collector is replaced and observed in
  parallel; preserve the archived source and SQLite snapshot.

### Required before any real-money consideration

- Rotate all credentials exposed during development and remove root-based deployment.
- Verify source terms and footage rights.
- Complete the paper-performance gates above.
- Keep execution manual. Automated book login, wagering and bankroll transfer remain out of
  scope even after model validation.
