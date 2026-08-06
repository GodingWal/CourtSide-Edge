"""Fitted-parameter persistence and access for scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..database import dump_json, get_cursor, load_json
from .edge_shrinkage import EdgeShrinkage, IdentityEdgeShrinkage
from .line_bias import IDENTITY_BIAS, LineBandBias

DEFAULT_MARKET = "__all__"


@dataclass(frozen=True)
class FittedParameters:
    calibration: dict[str, dict[str, float]] = field(default_factory=dict)
    weights: dict[str, dict[str, float]] = field(default_factory=dict)
    shrinkage: dict[str, EdgeShrinkage] = field(default_factory=dict)
    line_bias: dict[str, LineBandBias] = field(default_factory=dict)

    def calibration_for(self, market: str) -> dict[str, float]:
        return self.calibration.get(market) or self.calibration.get(DEFAULT_MARKET, {})

    def weights_for(self, market: str) -> dict[str, float]:
        return self.weights.get(market) or self.weights.get(DEFAULT_MARKET, {})

    def shrinkage_for(self, market: str) -> EdgeShrinkage:
        return self.shrinkage.get(market) or self.shrinkage.get(DEFAULT_MARKET) or IdentityEdgeShrinkage

    def line_bias_for(self, market: str) -> LineBandBias:
        return self.line_bias.get(market) or self.line_bias.get(DEFAULT_MARKET) or IDENTITY_BIAS


def load_fitted_parameters(*, at: datetime | None = None) -> FittedParameters:
    query = """
        SELECT DISTINCT ON (kind, market)
            kind, market, payload
        FROM wnba.fitted_parameters
        WHERE (%s::timestamptz IS NULL OR fitted_at <= %s::timestamptz)
        ORDER BY kind, market, fitted_at DESC
    """
    calibration: dict[str, dict[str, float]] = {}
    weights: dict[str, dict[str, float]] = {}
    shrinkage: dict[str, EdgeShrinkage] = {}
    line_bias: dict[str, LineBandBias] = {}
    with get_cursor() as cur:
        cur.execute(query, (at, at))
        for kind, market, payload in cur.fetchall():
            data = load_json(payload)
            if kind == "calibration_map":
                calibration[market] = {str(k): float(v) for k, v in data.items()}
            elif kind == "ensemble_weights":
                weights[market] = {str(k): float(v) for k, v in data.items()}
            elif kind == "edge_shrinkage":
                shrinkage[market] = EdgeShrinkage(
                    shrink=float(data.get("shrink", 0.0)),
                    sample_size=int(data.get("sample_size", 0)),
                    fitted=bool(data.get("fitted", False)),
                )
            elif kind == "line_bias":
                line_bias[market] = LineBandBias.from_payload(data)
    return FittedParameters(
        calibration=calibration,
        weights=weights,
        shrinkage=shrinkage,
        line_bias=line_bias,
    )


def store_fitted_parameters(
    cur: Any,
    *,
    kind: str,
    market: str,
    at: datetime,
    payload: dict[str, Any],
    sample_size: int,
    is_fitted: bool,
) -> None:
    cur.execute(
        """
        INSERT INTO wnba.fitted_parameters (
            kind, market, fitted_at, payload, sample_size, is_fitted
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (kind, market, at, dump_json(payload), sample_size, is_fitted),
    )
