"""The job that turns settled episodes back into model parameters.

This is the half of the learning loop that was missing. Settlement scored every paper forecast,
evaluation measured the calibration error and opened drift incidents about it, and then the
pipeline forecast the next night using exactly the parameters that had just been shown to be
wrong. Measuring a bias is not correcting it.

Run after settlement. It fits three things per market, and refuses all three when the evidence is
thin or the improvement does not survive cross-fitting:

* the calibration map, on raw ``P(over)`` against whether the over actually landed;
* the ensemble weights, on each component's ``P(over)`` against the same outcome;
* the edge shrinkage factor, on the selected side's probability against whether it hit.

Every market also contributes to a pooled fit under :data:`DEFAULT_MARKET`, which is what a thin
market falls back to. Three-pointers and rebounds genuinely miscalibrate differently, so fitting
per market is right -- but a market with forty settled episodes is better served by the pooled
correction than by its own noise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from wnba_store.db import connect

from wnba_services.forecasting.calibration import fit_calibration_map
from wnba_services.forecasting.parameters import DEFAULT_MARKET, store_fitted_parameters
from wnba_services.forecasting.scoring import COMPONENT_NAMES
from wnba_services.forecasting.selection import EdgeShrinkage
from wnba_services.forecasting.weights import fit_ensemble_weights

__all__ = ["FittingBatch", "fit_model_parameters"]


@dataclass(frozen=True)
class FittingBatch:
    episodes: int
    calibration_maps: int
    weight_sets: int
    shrinkage_sets: int
    fitted_calibration: int
    fitted_weights: int


def _over_outcome(side: str, hit: bool) -> float:
    """Restate a settled result as "did the over land", whichever side was selected."""
    return float(hit) if side == "over" else float(not hit)


def fit_model_parameters(*, now: datetime | None = None) -> FittingBatch:
    """Refit calibration, weights and shrinkage from every scoreable settled episode."""
    at = now or datetime.now(UTC)
    episodes = 0
    calibration_maps = weight_sets = shrinkage_sets = 0
    fitted_calibration = fitted_weights = 0

    with connect() as conn, conn.cursor() as cur:
        # Voids and pushes are excluded at the source. A void is a coach's rotation decision and
        # a push is an outcome that resolved neither way; fitting parameters to either would be
        # teaching the model from events that carry no information about forecast quality.
        cur.execute(
            """SELECT d.prop_type,d.side,o.hit,d.predicted_probability,
                      coalesce(f.probability_over_raw,f.probability_over) AS raw_over,
                      d.episode_id
               FROM wnba.decision_episodes d
               JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               LEFT JOIN wnba.stat_forecasts f
                 ON f.quote_id=d.quote_id AND f.model_run_id=d.model_run_id
               WHERE NOT o.was_voided AND NOT o.was_push
               ORDER BY d.forecast_timestamp"""
        )
        settled = cur.fetchall()
        episodes = len(settled)

        calibration_points: dict[str, list[tuple[float, float]]] = {}
        shrinkage_points: dict[str, list[tuple[float, float]]] = {}
        for row in settled:
            market = str(row["prop_type"])
            side = str(row["side"])
            hit = bool(row["hit"])
            if row["raw_over"] is not None:
                point = (float(str(row["raw_over"])), _over_outcome(side, hit))
                calibration_points.setdefault(market, []).append(point)
                calibration_points.setdefault(DEFAULT_MARKET, []).append(point)
            selected = (float(str(row["predicted_probability"])), float(hit))
            shrinkage_points.setdefault(market, []).append(selected)
            shrinkage_points.setdefault(DEFAULT_MARKET, []).append(selected)

        cur.execute(
            """SELECT d.prop_type,d.side,o.hit,d.episode_id,
                      fc.component_name,fc.probability_over
               FROM wnba.forecast_components fc
               JOIN wnba.stat_forecasts f ON f.projection_id=fc.projection_id
               JOIN wnba.decision_episodes d
                 ON d.quote_id=f.quote_id AND d.model_run_id=f.model_run_id
               JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               WHERE NOT o.was_voided AND NOT o.was_push"""
        )
        by_episode: dict[str, tuple[str, float, dict[str, float]]] = {}
        for row in cur.fetchall():
            episode_id = str(row["episode_id"])
            entry = by_episode.setdefault(
                episode_id,
                (
                    str(row["prop_type"]),
                    _over_outcome(str(row["side"]), bool(row["hit"])),
                    {},
                ),
            )
            entry[2][str(row["component_name"])] = float(str(row["probability_over"]))

        weight_points: dict[str, list[tuple[dict[str, float], float]]] = {}
        for market, outcome, components in by_episode.values():
            if not set(COMPONENT_NAMES) <= set(components):
                continue
            weight_points.setdefault(market, []).append((components, outcome))
            weight_points.setdefault(DEFAULT_MARKET, []).append((components, outcome))

        for market, points in sorted(calibration_points.items()):
            fitted = fit_calibration_map(market, points)
            store_fitted_parameters(
                cur,
                kind="calibration_map",
                market=market,
                at=at,
                payload=fitted.to_payload(),
                sample_size=fitted.sample_size,
                log_loss_gain=fitted.fitted_log_loss_gain,
                is_fitted=not fitted.is_identity,
            )
            calibration_maps += 1
            fitted_calibration += int(not fitted.is_identity)

        for market, observations in sorted(weight_points.items()):
            fitted_weight = fit_ensemble_weights(
                market, observations, component_names=list(COMPONENT_NAMES)
            )
            store_fitted_parameters(
                cur,
                kind="ensemble_weights",
                market=market,
                at=at,
                payload=fitted_weight.to_payload(),
                sample_size=fitted_weight.sample_size,
                log_loss_gain=fitted_weight.log_loss_gain,
                is_fitted=fitted_weight.is_fitted,
            )
            weight_sets += 1
            fitted_weights += int(fitted_weight.is_fitted)

        for market, points in sorted(shrinkage_points.items()):
            shrinkage = EdgeShrinkage.from_settled(points)
            store_fitted_parameters(
                cur,
                kind="edge_shrinkage",
                market=market,
                at=at,
                payload=shrinkage.to_payload(),
                sample_size=shrinkage.sample_size,
                is_fitted=shrinkage.is_fitted,
            )
            shrinkage_sets += 1

    return FittingBatch(
        episodes=episodes,
        calibration_maps=calibration_maps,
        weight_sets=weight_sets,
        shrinkage_sets=shrinkage_sets,
        fitted_calibration=fitted_calibration,
        fitted_weights=fitted_weights,
    )
