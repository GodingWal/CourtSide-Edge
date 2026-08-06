"""Nightly refit of calibration maps, ensemble weights, and edge shrinkage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..database import get_cursor
from .calibration import CalibrationPoint, fit_calibration_map
from .edge_shrinkage import EdgeShrinkage
from .line_bias import fit_line_bias
from .parameters import DEFAULT_MARKET, store_fitted_parameters

DEFAULT_LOOKBACK_DAYS = 180
DEFAULT_MINIMUM_SAMPLE = 30


@dataclass(frozen=True)
class FittingBatch:
    calibration_maps: int = 0
    weight_sets: int = 0
    shrinkage_sets: int = 0
    line_bias_sets: int = 0


def fit_model_parameters(
    *,
    at: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    minimum_sample: int = DEFAULT_MINIMUM_SAMPLE,
) -> FittingBatch:
    at = at or datetime.now(timezone.utc)
    since = at - timedelta(days=lookback_days)
    calibration_points: dict[str, list[CalibrationPoint]] = {}
    weight_rows: dict[str, list[tuple[dict[str, float], float]]] = {}
    shrinkage_points: dict[str, list[tuple[float, float, float]]] = {}
    bias_points: dict[str, list[tuple[float, float, float]]] = {}

    with get_cursor() as cur:
        cur.execute(
            """
            SELECT d.market, d.side, d.confidence, d.projected_prob,
                   d.model_breakdown, d.edge, d.breakeven_prob,
                   d.line, d.projected_mean,
                   o.actual_stat, o.hit
            FROM wnba.decision_episodes d
            JOIN wnba.episode_outcomes o ON o.episode_id = d.id
            WHERE d.decided_at >= %s AND d.decided_at < %s
            ORDER BY d.decided_at
            """,
            (since, at),
        )
        for (
            market,
            side,
            confidence,
            projected_prob,
            model_breakdown,
            edge,
            breakeven_prob,
            line,
            projected_mean,
            actual_stat,
            hit,
        ) in cur.fetchall():
            if confidence is None or projected_prob is None:
                continue
            if side == "over":
                probability = float(confidence)
            elif side == "under":
                probability = 1.0 - float(confidence)
            else:
                probability = float(projected_prob)
            point = CalibrationPoint(probability=probability, hit=bool(hit))
            calibration_points.setdefault(market, []).append(point)
            calibration_points.setdefault(DEFAULT_MARKET, []).append(point)

            if edge is not None and breakeven_prob is not None:
                shrinkage_points.setdefault(market, []).append(
                    (float(edge), float(breakeven_prob), float(bool(hit)))
                )
                shrinkage_points.setdefault(DEFAULT_MARKET, []).append(
                    (float(edge), float(breakeven_prob), float(bool(hit)))
                )

            if line is not None and projected_mean is not None and actual_stat is not None:
                triple = (float(line), float(projected_mean), float(actual_stat))
                bias_points.setdefault(market, []).append(triple)
                bias_points.setdefault(DEFAULT_MARKET, []).append(triple)

        cur.execute(
            """
            SELECT market, probabilities, hit
            FROM wnba.backtest_results
            WHERE created_at >= %s AND created_at < %s
            ORDER BY created_at
            """,
            (since, at),
        )
        for market, probabilities, hit in cur.fetchall():
            if not probabilities:
                continue
            parsed = {str(k): float(v) for k, v in probabilities.items() if v is not None}
            if not parsed:
                continue
            weight_rows.setdefault(market, []).append((parsed, float(bool(hit))))
            weight_rows.setdefault(DEFAULT_MARKET, []).append((parsed, float(bool(hit))))

        batch = FittingBatch()
        maps = 0
        weights_count = 0
        shrinkage_count = 0
        bias_count = 0

        for market, points in sorted(calibration_points.items()):
            fitted = fit_calibration_map(points, minimum_sample=minimum_sample)
            store_fitted_parameters(
                cur,
                kind="calibration_map",
                market=market,
                at=at,
                payload=fitted.bins,
                sample_size=fitted.sample_size,
                is_fitted=fitted.is_fitted,
            )
            maps += 1

        for market, rows in sorted(weight_rows.items()):
            weights = _fit_weights(rows, minimum_sample=minimum_sample)
            store_fitted_parameters(
                cur,
                kind="ensemble_weights",
                market=market,
                at=at,
                payload=weights,
                sample_size=len(rows),
                is_fitted=bool(weights),
            )
            weights_count += 1

        for market, points in sorted(shrinkage_points.items()):
            shrinkage = EdgeShrinkage.from_settled(points, minimum_sample=minimum_sample)
            store_fitted_parameters(
                cur,
                kind="edge_shrinkage",
                market=market,
                at=at,
                payload={
                    "shrink": shrinkage.shrink,
                    "sample_size": shrinkage.sample_size,
                    "fitted": shrinkage.fitted,
                },
                sample_size=shrinkage.sample_size,
                is_fitted=shrinkage.fitted,
            )
            shrinkage_count += 1

        for market, triples in sorted(bias_points.items()):
            line_bias = fit_line_bias(triples, market=market)
            store_fitted_parameters(
                cur,
                kind="line_bias",
                market=market,
                at=at,
                payload=line_bias.to_payload(),
                sample_size=len(triples),
                is_fitted=line_bias.is_fitted,
            )
            bias_count += 1

    return FittingBatch(
        calibration_maps=maps,
        weight_sets=weights_count,
        shrinkage_sets=shrinkage_count,
        line_bias_sets=bias_count,
    )


def _fit_weights(
    rows: list[tuple[dict[str, float], float]],
    *,
    minimum_sample: int,
) -> dict[str, float]:
    if len(rows) < minimum_sample:
        return {}
    scores: dict[str, list[float]] = {}
    for probabilities, hit in rows:
        for name, probability in probabilities.items():
            scores.setdefault(name, []).append((probability - hit) ** 2)
    if not scores:
        return {}
    inverse = {
        name: 1.0 / max(sum(values) / len(values), 1e-6)
        for name, values in scores.items()
        if values
    }
    total = sum(inverse.values())
    if total <= 0:
        return {}
    return {name: value / total for name, value in sorted(inverse.items())}
