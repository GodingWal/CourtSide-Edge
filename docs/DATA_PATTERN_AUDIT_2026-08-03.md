# WNBA Data Pattern Audit — 2026-08-03

## Scope and limitations

- 16,076 current player-game rows, 386 players, 2024-05-04 through 2026-08-03.
- 4,571 normalized historical prop quotes, 82 players, but only 38 games from
  2026-06-23 through 2026-07-16.
- Markets covered: points, rebounds, assists, and three-pointers.
- Historical prices are lossy integers. This audit does not claim priced ROI.
- All rolling features use games strictly before the target game's scheduled tipoff.

## Stable basketball signals

The following upper-versus-lower quartile effects were defined on 2025 and checked unchanged
on 2026 player-games. Values are differences in actual-stat residual versus a trailing-10
average.

| Feature | Market | 2025 effect | 2026 effect | Interpretation |
|---|---:|---:|---:|---|
| Recent stat trend | Points | +1.06 | +1.29 | Recent role/form contains some persistent signal. |
| Recent stat trend | Rebounds | +0.58 | +0.29 | Positive but weaker in 2026. |
| Recent stat trend | Assists | +0.33 | +0.36 | Small and seasonally stable. |
| Recent stat trend | Threes | +0.13 | +0.19 | Small and noisy at player level. |
| Minutes trend | Points | +1.13 | +1.33 | Strongest reusable feature family. |
| Minutes trend | Rebounds | +0.70 | +0.57 | Stable opportunity effect. |
| Minutes trend | Assists | +0.44 | +0.51 | Stable opportunity effect. |
| Minutes trend | Threes | +0.07 | +0.19 | Directionally positive. |
| Long rest | Points | -0.25 | -0.24 | Small, stable negative residual. |
| Long rest | Rebounds | -0.17 | -0.14 | Small, stable negative residual. |
| Long rest | Assists | -0.10 | -0.15 | Small, stable negative residual. |

These are associations, not causal estimates. Minutes trend and stat trend are correlated and
must not be counted as independent full-strength adjustments.

## Forecast benchmark results

Mean absolute error on all eligible player-games:

| Market | Season | Trailing 10 | Trend-adjusted | Minutes × rate |
|---|---:|---:|---:|---:|
| Points | 2025 | 4.275 | 4.274 | 4.274 |
| Points | 2026 | 4.367 | 4.362 | 4.385 |
| Rebounds | 2025 | 1.842 | 1.835 | 1.833 |
| Rebounds | 2026 | 1.758 | 1.763 | 1.762 |
| Assists | 2025 | 1.252 | 1.250 | 1.247 |
| Assists | 2026 | 1.255 | 1.252 | 1.251 |

The features carry information, but naive deterministic adjustments barely improve average
error. They should enter a regularized challenger model and uncertainty model rather than be
added as fixed point adjustments.

## Market-strategy candidate

Using a median of at least three latest bookmaker lines and a trailing-10 baseline on the
2026-07-06 onward holdout:

| Market | Minimum disagreement | Decisions | Hit rate | Wilson 95% interval |
|---|---:|---:|---:|---:|
| Points | 1.0 | 66 | 45.5% | 34.0%–57.4% |
| Rebounds | 1.0 | 19 | 68.4% | 46.0%–84.6% |
| Assists | 1.0 | 12 | 58.3% | 32.0%–80.7% |
| Threes | 1.0 | 5 | 80.0% | 37.6%–96.4% |

Only rebounds merits a shadow hypothesis. It is not ready as a betting strategy: there are
only 19 holdout decisions and the confidence interval includes 50%.

## Data-quality discovery

Cross-book ranges are too large to treat every archived line as a comparable main line:

| Market | Median range | 90th percentile | Markets with range ≥ 5 |
|---|---:|---:|---:|
| Points | 2.0 | 7.0 | 68 |
| Rebounds | 1.0 | 3.0 | 11 |
| Assists | 0.0 | 2.0 | 4 |
| Threes | 0.0 | 1.0 | 0 |

Examples include isolated lines far from every other book. These may be alternate lines,
asynchronous observations, or legacy normalization errors. Raw single-book disagreement
produced implausibly high backtest results and must be blocked from strategy evaluation.

## Challenger feature specifications

1. `stat_rate_trend_3_vs_prior_7`
   - Difference between the last-three per-minute rate and games 4–10.
   - Winsorize by market; include sample size and minutes denominator.
2. `minutes_trend_3_vs_prior_7`
   - Difference in mean minutes between the same windows.
   - Interact with starting probability and availability, not actual starting status.
3. `role_change_disagreement`
   - Difference between recent-three and prior-seven start share, closing-lineup probability,
     and projected minutes.
4. `rest_spline`
   - Nonlinear rest feature with knots around 1, 2, 3, and 5 days; do not use one binary flag.
5. `cross_book_line_range`
   - Maximum minus minimum synchronized main line at a common as-of time.
6. `quote_consensus_deviation`
   - Offered line minus synchronized robust consensus median.
   - Block when fewer than three books or when main-line identity is unresolved.
7. `baseline_consensus_edge`
   - Calibrated model median minus robust consensus line.
   - Research first for rebounds; shadow-only until a much larger holdout exists.
8. `forecast_uncertainty_interactions`
   - Increase variance when minutes trend, stat trend, and model components disagree.

## Promotion protocol

- Freeze feature formulas before the next evaluation window.
- Evaluate by rolling origin and season, player, market, and game-time snapshot.
- Cluster uncertainty by game and player.
- Use log loss and calibration as primary metrics; MAE and line value are secondary.
- Apply multiple-comparison correction to the challenger family.
- Require at least 100 independent rebound decisions across at least eight weeks before a
  strategy recommendation can pass even provisionally.
- Never evaluate ROI from the lossy legacy prices.
