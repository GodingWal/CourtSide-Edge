# Model card: auditable ensemble 0.3.1

## Intended use

WNBA player-prop probability analysis for one private analyst. The model is shadow-only and
must not be treated as validated wagering advice.

## Components

- Empirical minutes/rate simulation: 35%.
- League-shrunk hierarchical rate: 25%.
- Exponentially weighted player state: 15%.
- Opportunity/conversion decomposition: 20%.
- Line-centred market prior: 5%.

Injuries, expected role/minutes, teammate absences, pace, opponent defence, rest and blowout risk
adjust the distributions. Component disagreement reduces confidence and can block candidates.

## Current evidence

The latest retrospective point-in-time replay contains 14,880 raw forecast snapshots and 3,614
deduplicated decisions over 38 games. Ensemble Brier score is approximately 0.2424 versus 0.2472
for minutes × rate. This is provisional evidence because weights were inspected after replay and
the period is short.

## Known limitations

- Historical prices are unusable for return calculations because decimal odds were truncated.
- Only 38 historical games are represented in the market replay.
- No production video features.
- Live calibration and void/late-news evidence remain immature.
- Market prior weights are fixed, not learned from a locked holdout.

## Promotion

Only the owner may promote a model, and only after every machine readiness gate is `pass` rather
than `provisional_pass` or `pending`.
