"""Measured projection bias by market and line band.

Settled outcomes keep saying the same thing: actual minus projected runs positive for the
high lines -- the shrinkage that protects the model from small samples pulls star projections
toward the pack. A probability calibration map cannot fix that, because the bias lives in the
mean, not in the mapping from mean to probability: a +2-point bias at a 15-point line and the
same bias at a 30-point line are different probability errors, and one knot per probability
bucket sees them mixed.

So the correction is measured and applied where it is generated: per market, per line band,
as an additive shift to the model-side component means. The market component is untouched --
the operator already knows the line. Bands with thin samples are shrunk toward zero, because
a bias "measured" over eleven games is noise wearing a lab coat.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

# A band needs this many settled forecasts before its mean error is trusted at all, and even
# then it is shrunk by n/(n+K) -- full strength is approached asymptotically, never reached.
MIN_BAND_SAMPLE = 30
SHRINK_K = 25.0

# Band edges per market, in line units. Chosen from the settled-error profile: the bias
# changes across these boundaries, which is what makes them boundaries.
BAND_EDGES: dict[str, tuple[float, ...]] = {
    "points": (10.0, 15.0, 20.0, 25.0),
    "points_assists": (15.0, 20.0, 25.0, 30.0),
    "points_rebounds": (15.0, 20.0, 25.0, 30.0),
    "rebounds": (5.0, 8.0, 11.0),
    "rebounds_assists": (8.0, 11.0, 14.0),
    "assists": (3.0, 5.0, 7.0),
    "three_pointers": (1.5, 2.5, 3.5),
    "points_rebounds_assists": (20.0, 25.0, 30.0, 35.0),
}
DEFAULT_EDGES: tuple[float, ...] = (10.0, 20.0)


@dataclass(frozen=True)
class LineBand:
    """One measured band: lines in [lo, hi) carry ``bias`` points of measured mean error."""

    lo: float
    hi: float
    bias: float
    sample_size: int

    def to_payload(self) -> dict[str, Any]:
        return {"lo": self.lo, "hi": self.hi, "bias": self.bias, "sample_size": self.sample_size}


@dataclass(frozen=True)
class LineBandBias:
    """The fitted correction for one market, or the identity when nothing is measured."""

    bands: tuple[LineBand, ...] = ()
    is_fitted: bool = False

    def bias_for(self, line: float) -> float:
        for band in self.bands:
            if band.lo <= line < band.hi:
                return band.bias
        return 0.0

    def apply(self, mean: float, line: float) -> float:
        return max(0.0, mean + self.bias_for(line))

    def to_payload(self) -> dict[str, Any]:
        return {"bands": [band.to_payload() for band in self.bands], "is_fitted": self.is_fitted}

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> LineBandBias:
        bands = tuple(
            LineBand(
                lo=float(band["lo"]),
                hi=float(band["hi"]),
                bias=float(band["bias"]),
                sample_size=int(band["sample_size"]),
            )
            for band in payload.get("bands", [])
        )
        return cls(bands=bands, is_fitted=bool(payload.get("is_fitted", False)))


IDENTITY_BIAS = LineBandBias()


def _band_index(edges: tuple[float, ...], line: float) -> int:
    for index, edge in enumerate(edges):
        if line < edge:
            return index
    return len(edges)


def fit_line_bias(
    points: Iterable[tuple[float, float, float]],
    *,
    market: str,
    minimum_sample: int = MIN_BAND_SAMPLE,
    shrink_k: float = SHRINK_K,
) -> LineBandBias:
    """Fit one market's band biases from ``(line, projected_mean, actual)`` triples.

    The bias for a band is its mean signed error, shrunk toward zero by n/(n+K) so a thin
    band earns only a fraction of its apparent correction. Bands below ``minimum_sample``
    contribute nothing -- zero, not a guess.
    """
    edges = BAND_EDGES.get(market, DEFAULT_EDGES)
    errors: list[list[float]] = [[] for _ in range(len(edges) + 1)]
    for line, projected, actual in points:
        errors[_band_index(edges, line)].append(actual - projected)

    bands: list[LineBand] = []
    bounds = (0.0, *edges, math.inf)
    for index, band_errors in enumerate(errors):
        count = len(band_errors)
        if count < minimum_sample:
            continue
        raw = math.fsum(band_errors) / count
        shrunk = raw * (count / (count + shrink_k))
        bands.append(
            LineBand(lo=bounds[index], hi=bounds[index + 1], bias=shrunk, sample_size=count)
        )
    return LineBandBias(bands=tuple(bands), is_fitted=bool(bands))
