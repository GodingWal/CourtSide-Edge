# Data dictionary and source register

## Time semantics

`valid_*` describes when a fact is true in basketball. `system_*` describes when CourtSide Edge
learned it. Backtests constrain both market observations and feature history to forecast time.

## Principal objects

| Object | Purpose |
|---|---|
| `games`, `players`, `teams` | Canonical basketball identity |
| `player_game_lines` | Append-only corrected official box scores |
| `prop_quotes` | Live market observations |
| `historical_prop_quotes` | Conservatively mapped rescued market observations |
| `feature_snapshots` | Frozen forecast inputs |
| `stat_forecasts`, `forecast_components` | Ensemble and component distributions |
| `decision_episodes`, `episode_outcomes` | Immutable paper decisions and settlements |
| `source_documents`, `evidence`, `research_claims` | Cited research memory |
| `backtest_results` | Point-in-time benchmark replay |
| `readiness_gate_results` | Fail-closed validation evidence |

## Source register

| Source | Use | Reliability treatment |
|---|---|---|
| ESPN | Schedule and final box scores | Raw payload hashes and correction history |
| WNBA official reports | Injury status | PDF checksum, bitemporal status and expiry |
| Underdog | Live prop board | Polite polling, changed states only |
| Legacy CourtSide archive | Historical lines/stats | Immutable staging; conservative mapping |
| DeepSeek | Research synthesis | Supplied evidence only; strict validated JSON |

The legacy price columns are retained as raw lossy integers and excluded from EV/profit metrics.
Footage may enter only after rights are documented.
