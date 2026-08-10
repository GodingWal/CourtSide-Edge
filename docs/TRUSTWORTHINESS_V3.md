# Trustworthiness v3

## Objective

Improve out-of-sample decision quality by measuring when the model is trustworthy, not by
maximising the number of published picks. Every new learner is shadow-only until a locked,
rolling-origin experiment and a named human promotion.

## Data flow

```text
immutable quotes + roles + box scores
              |
              v
 point-in-time champion + shadow challengers
              |
              +--> shared-state game simulator --> covariance / scenario diagnostics
              |
              v
 paper decision --> closing line --> outcome
              |
              v
 trust fitting (weekly, strictly time ordered)
   |        |          |          |
 coverage  conformal  source     paired feature
 policy    intervals  reliability ablations
   \___________ human-visible Learning evidence __________/
                              |
                              v
                  experiment + named approval only
```

## Components

- **Closing-line lifecycle:** immutable opening/current/closing designations and directional
  line value. In pick'em markets this is a weaker proxy than price-based sportsbook CLV and is
  labelled accordingly.
- **Selective policy:** evaluates log loss and calibration at increasing publication coverage.
  It selects the widest held-out coverage satisfying declared risk limits. Thin or unsuccessful
  fits abstain.
- **Adaptive conformal intervals:** finite-sample absolute-residual intervals segmented by prop
  and role. Segments below the evidence floor explicitly fall back to the pooled interval.
- **Role states:** continuous minutes/start evidence is also labelled as confirmed starter,
  probable starter, sixth player, rotation bench, emergency replacement, returning from injury,
  or minutes restriction. The continuous probabilities remain authoritative.
- **Market profiles:** points and threes separate volume from conversion; combination markets
  propagate component covariance; the profile used is recorded in the feature snapshot.
- **Opponent effects:** the existing schedule-adjusted offence/defence fit remains shrunk toward
  league average with a 12-game prior.
- **Joint game simulator:** shared pace, team usage, minutes and blowout draws produce a full
  covariance matrix. It is a shadow dependence diagnostic; it does not overwrite champion
  marginals.
- **Feature ablation:** removes each ensemble component from the same settled episodes and uses
  paired log-loss differences with a Bonferroni-adjusted interval.
- **Source reliability:** robust line error, freshness and sample size determine a bounded source
  weight. An unmeasured source retains a conservative prior rather than zero trust.
- **Replay:** reconstructs frozen features and component votes, separates quotes available at the
  decision from later movement, and appends the outcome without rewriting the decision.

## Assumptions and trade-offs

- Absolute-residual conformal bands quantify stat uncertainty, not causal uncertainty. A new
  rotation regime can still break historical coverage; the role segmentation and drift monitor
  are mitigations, not guarantees.
- The game simulator is intentionally parsimonious. It estimates dependence with shared latent
  state and leaves the champion's discrete marginal distribution intact. A possession-level
  event model should replace it only when licensed event data has sufficient historical depth.
- Source reliability uses distance to the realised stat because pick'em prices are incomplete.
  Once trustworthy two-sided prices exist, price-based CLV should become the primary source
  target.
- Feature ablation measures predictive contribution, not causal importance. Correlated features
  may substitute for one another; the redundancy cap and paired experiment matrix must be read
  together.

## Growth path

Revisit the design when there are at least 500 independent settled recommendations per major
prop/role segment, two legally collected market sources with reliable prices, or licensed
possession/tracking data. Those thresholds justify group-specific selective policies, weighted
conformal shift correction and a possession-level joint model respectively.
