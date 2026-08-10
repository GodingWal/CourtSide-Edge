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
risk adjust the distributions. Starter and bench minutes scenarios remain separate through the
forecast and are shown to the owner. Component disagreement reduces confidence and can block
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

- **Calibration maps** — isotonic and regularized logistic (Platt) candidates, per market,
  shrunk toward the identity by sample size. The method with the best held-out log loss is
  adopted only when it beats raw probabilities consistently across rolling-origin folds, where
  every validation episode occurs after the map's training data.
- **Ensemble weights** — fitted on held-out log loss over the simplex, adopted under the same
  consistency requirement. Rolling-origin folds prevent later episodes entering earlier fits;
  highly correlated component pairs are recorded and penalized when their combined fitted
  weight would exceed their prior allocation.
- **Edge shrinkage** — the fraction of an apparent edge that survives regression to the mean,
  estimated as the ratio of signal variance to total variance in the settled record.

All three fall back to identity/prior/cold-start behaviour when the evidence is thin, which is the
correct output rather than a degraded one.

## Selection

Candidates are gated on the **shrunk** probability and its conservative one-sided lower bound
against the break-even implied by the payout table — 57.7% per leg for a two-leg power play —
plus a 2% margin. The bound combines component disagreement with the sampling uncertainty of the
settled shrinkage record. The previous fixed `0.58` threshold was unconnected to any product and
was applied to an unshrunk edge.

A production-qualified pick must also have a quote no more than 30 minutes old, at least two
independent market sources, at least 90% availability probability, minutes uncertainty no more
than 20% of projected minutes, no material minutes-restriction risk, and a shrinkage estimate
fitted from at least 200 settled episodes. Failure of any gate produces no qualified pick. Entry
search uses the lower-bound probabilities—not the headline estimates—and caps exposure per
player, team, and game before correlation-band pricing.

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

## Challengers

Two model families run beside the champion and reach nothing. Both are pure functions of the
same `ScoringInputs` the champion reads, so a difference between them is a difference in
modelling rather than in what they were allowed to see.

| Challenger | Family | What it does that the champion does not |
|---|---|---|
| `hierarchical-bayes` 0.1.0 | Conjugate Gamma–Poisson, three levels (league → prior season → player) | Carries the *posterior variance* of the rate into the predictive distribution, and discounts the likelihood by the player's observed over-dispersion. The champion's `hierarchical` component is a point estimate at a fixed 300-minute prior strength, so it reports the same width for a rate measured over 40 minutes and one measured over 700. |
| `state-space-role` 0.1.0 | Local-level (Kalman) filter on minutes and per-minute rate | Estimates the signal-to-noise ratio per player from the autocovariance of first differences, so the gain rises through a genuine role change and stays low through noise. The champion's `player_state` component decays the past at a fixed per-market half-life either way. A projected-minutes row still overrides the filter's minutes estimate. |

Not implemented, deliberately: a gradient-boosted challenger. It needs a dependency, a training
set assembled from the feature store, and a fitting job with its own leakage controls. A stub
wrapping the same five components would fill the experiments table without creating anything to
learn from.

## Promotion

Only the owner may promote a model, and only after every machine readiness gate is `pass` rather
than `provisional_pass` or `pending`.

Champion/challenger promotion has the same asymmetry the analyst-rule lifecycle has, enforced in
three places rather than one:

- `evaluate_experiments` can reach at most a `challenger_better` **verdict**. It contains no code
  path that writes `promoted`.
- `promote_challenger` re-reads the stored evaluation under a row lock and refuses unless the
  verdict is `challenger_better`, the *independent* market count clears the experiment's gate, and
  no subgroup is flagged as degraded.
- The database rejects `promoted = true` without a named `approved_by` and a stated
  `promotion_reason`, so no scheduled job can promote a model even if the code above were wrong.

Rollback (`wnba learning experiments rollback`) restores the previous champion and leaves the
promotion in the record. A model that was promoted and then withdrawn is a more useful fact than
a model that was never promoted.

Experiments are scored **paired** on identical episodes, deduplicated to one row per market, and
gated on the effective sample after the within-game clustering correction. Confidence intervals
come from deterministic paired cluster bootstraps by game, player, and slate date; the most
conservative bounds are used, and simultaneous experiments and subgroup checks receive a
Bonferroni correction. An episode a challenger failed to score is dropped from both sides; its
failure is still counted in the experiment's failure rate.
