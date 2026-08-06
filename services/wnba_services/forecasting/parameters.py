"""Loading and storing the parameters the system fits about itself.

Everything here is deliberately absence-tolerant. A market with no fitted calibration gets the
identity map, a market with no fitted weights gets the priors, and a market with no fitted
shrinkage gets the stated cold-start factor. That is not a degraded mode to be apologised for --
it is the correct behaviour before enough episodes have settled, and it is what lets the whole
fitted-parameter layer be added without any market having to wait for it.

Rows are superseded rather than updated, so a forecast made last Tuesday stays explicable with
the parameters that were in force last Tuesday.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import uuid4

from psycopg.types.json import Jsonb

from wnba_services.forecasting.calibration import IDENTITY, CalibrationMap
from wnba_services.forecasting.line_bias import IDENTITY_BIAS, LineBandBias
from wnba_services.forecasting.selection import EdgeShrinkage
from wnba_services.forecasting.weights import EnsembleWeights

__all__ = [
    "DEFAULT_MARKET",
    "FittedParameters",
    "load_fitted_parameters",
    "store_fitted_parameters",
]

# The bucket a market falls back to before it has enough settled episodes of its own. Fitting
# per market is right in principle -- rebounds and three-pointers miscalibrate differently --
# but a thin market is better served by the pooled fit than by its own twelve observations.
DEFAULT_MARKET = "__all__"


class Cursor(Protocol):
    """The slice of a psycopg cursor this module uses.

    Narrow on purpose: these functions take a cursor rather than opening their own connection so
    that loading parameters and writing the forecasts that used them happen in one transaction.
    """

    def execute(self, query: str, params: Any = ..., /) -> Any: ...

    def fetchall(self) -> list[Any]: ...


@dataclass(frozen=True)
class FittedParameters:
    """Everything the scorer needs that was learned rather than chosen."""

    calibration: dict[str, CalibrationMap] = field(default_factory=dict)
    weights: dict[str, EnsembleWeights] = field(default_factory=dict)
    shrinkage: dict[str, EdgeShrinkage] = field(default_factory=dict)
    line_bias: dict[str, LineBandBias] = field(default_factory=dict)

    def calibration_for(self, market: str) -> CalibrationMap:
        found = self.calibration.get(market) or self.calibration.get(DEFAULT_MARKET)
        return found or IDENTITY

    def weights_for(self, market: str) -> EnsembleWeights:
        found = self.weights.get(market) or self.weights.get(DEFAULT_MARKET)
        return found or EnsembleWeights.prior(market)

    def shrinkage_for(self, market: str) -> EdgeShrinkage:
        found = self.shrinkage.get(market) or self.shrinkage.get(DEFAULT_MARKET)
        return found or EdgeShrinkage.cold_start()

    def line_bias_for(self, market: str) -> LineBandBias:
        found = self.line_bias.get(market) or self.line_bias.get(DEFAULT_MARKET)
        return found or IDENTITY_BIAS

    @property
    def is_empty(self) -> bool:
        return not (self.calibration or self.weights or self.shrinkage)


def load_fitted_parameters(cur: Cursor) -> FittedParameters:
    """Read the live parameter set. An empty table yields all-default behaviour."""
    cur.execute(
        """SELECT kind,market,payload FROM wnba.fitted_parameters
           WHERE superseded_at IS NULL"""
    )
    calibration: dict[str, CalibrationMap] = {}
    weights: dict[str, EnsembleWeights] = {}
    shrinkage: dict[str, EdgeShrinkage] = {}
    line_bias: dict[str, LineBandBias] = {}
    for row in cur.fetchall():
        kind = str(row["kind"])
        market = str(row["market"])
        payload = dict(row["payload"])
        if kind == "calibration_map":
            calibration[market] = CalibrationMap.from_payload(payload)
        elif kind == "ensemble_weights":
            weights[market] = EnsembleWeights.from_payload(payload)
        elif kind == "edge_shrinkage":
            shrinkage[market] = EdgeShrinkage(
                factor=float(payload.get("factor", 0.5)),
                sample_size=int(payload.get("sample_size", 0)),
                is_fitted=bool(payload.get("is_fitted", False)),
            )
        elif kind == "line_bias":
            line_bias[market] = LineBandBias.from_payload(payload)
    return FittedParameters(
        calibration=calibration, weights=weights, shrinkage=shrinkage, line_bias=line_bias
    )


def store_fitted_parameters(
    cur: Cursor,
    *,
    kind: str,
    market: str,
    at: datetime,
    payload: dict[str, Any],
    sample_size: int,
    log_loss_gain: float = 0.0,
    is_fitted: bool = False,
) -> None:
    """Supersede the live row for this ``(kind, market)`` and insert the new one.

    Two statements rather than an upsert, because the previous value is evidence: the record of
    a calibration map that used to be trusted and no longer is belongs in the history, not in a
    row that quietly changed shape.
    """
    cur.execute(
        """UPDATE wnba.fitted_parameters SET superseded_at=%s
           WHERE kind=%s AND market=%s AND superseded_at IS NULL""",
        (at, kind, market),
    )
    cur.execute(
        """INSERT INTO wnba.fitted_parameters
           (parameter_id,kind,market,fitted_at,sample_size,log_loss_gain,is_fitted,payload)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (uuid4(), kind, market, at, sample_size, log_loss_gain, is_fitted, Jsonb(payload)),
    )
