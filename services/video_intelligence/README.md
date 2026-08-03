# WNBA video intelligence

This package is an isolated experimental sensor. It is excluded from the production workspace,
cannot block forecasts, and may consume only footage with documented analysis rights.

The target is spatial and role information unavailable in ordinary box scores: court position,
spacing, defender distance, matchup time, touches, possession duration, shot quality, rebound
positioning and movement load. Recreating points and rebounds from video is a validation task,
not the product.

See [the implementation plan](../../docs/COMPUTER_VISION_PLAN.md) and
[`feature_registry.yaml`](feature_registry.yaml).
