# Model card: auditable ensemble 0.4.0

## Intended use

WNBA player-prop probability analysis for one private analyst. The model is shadow-only and
must not be treated as validated wagering advice.

## Components

Five components, pooled log-linearly at weights that are fitted from settled episodes when the
evidence supports it and fall back to the priors below when it does not.

| Component | Prior weight | What it contributes |
|---|---|---|
| Empirical simulation | 0.30 | Coupled `(minutes, rate)` bootstrap over the player's own games |
| League-shrunk hierarchical rate | 0.20 | Player rate shrunk through the prior season toward the league |
| Exponentially weighted player state | 0.12 | Recent form at a per-market half-life |
| Opportunity/conversion decomposition | 0.18 | Attempts per minute times points per attempt |
| Devigged market prior | 0.20 | What the price says, once the operator's margin is removed |

Injuries, expected role and minutes, teammate absences, pace, opponent defence, rest and blowout
risk adjust the distributions. Component disagreement reduces confidence and can block
candidates.

## Distributional assumptions

- Counts are **negative binomial**, not Poisson. The variance-to-mean ratio is estimated from the
  player's own history and shrunk toward a market prior; points run near 2.3, rebounds and
  assists near 1.2. Modelling points as Poisson understated their spread by roughly a third and
  biased every tail line.
- Combination markets propagate covariance across their stat groups rather than treating the sum
  as a single count with the summed rate.
- Minutes are a **two-regime mixture** — starter and bench, mixed by start probability — rather
  than a single truncated Gaussian.

## The scorer is shared

`score_prop` in `wnba_services.forecasting.scoring` is called by both the live board and the
walk-forward replay. Before 0.4.0 these were two different models over two different component
sets, and the replay's Brier score described neither the deployed ensemble nor any feature it
used. Every performance number below is now produced by the code that runs.

## Current evidence

**None yet at this version.** The 0.3.1 figures previously quoted here (ensemble Brier ≈ 0.2424
versus 0.2472 for minutes × rate) measured the replay-only ensemble, not the production model,
and have been withdrawn rather than carried forward. The replay must be re-run against the shared
scorer before this section can say anything.

## Fitted parameters

Three sets of parameters are learned from settled episodes and stored in
`wnba.fitted_parameters`, superseded rather than overwritten:

- **Calibration maps** — isotonic, per market, shrunk toward the identity by sample size, and
  adopted only when they beat the raw probabilities out of fold in nearly every fold.
- **Ensemble weights** — fitted on held-out log loss over the simplex, adopted under the same
  consistency requirement.
- **Edge shrinkage** — the fraction of an apparent edge that survives regression to the mean,
  estimated as the ratio of signal variance to total variance in the settled record.

All three fall back to identity/prior/cold-start behaviour when the evidence is thin, which is the
correct output rather than a degraded one.

## Selection

Candidates are gated on the **shrunk** probability against the break-even implied by the payout
table — 57.7% per leg for a two-leg power play — plus a 2% margin. The previous fixed `0.58`
threshold was unconnected to any product and was applied to an unshrunk edge.

## Known limitations

- Historical prices are unusable for return calculations because decimal odds were truncated.
- Only 38 historical games are represented in the market replay.
- No production video features.
- Live calibration and void/late-news evidence remain immature.
- Payout tables are unverified, so the break-even the gate uses is only as good as the bundled
  numbers.
- Rest-day adjustments are stated priors, not fitted coefficients. They are now recorded as
  features on every forecast, which is what will eventually make fitting them possible.
- The market prior is only informative where the source prices both sides differently; on a flat
  pick'em board it correctly contributes nothing.

## Promotion

Only the owner may promote a model, and only after every machine readiness gate is `pass` rather
than `provisional_pass` or `pending`.
