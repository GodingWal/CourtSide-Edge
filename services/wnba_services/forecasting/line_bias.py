"""Measured projection bias by line band, applied to model-side scoring.

Backtest evidence shows the pooled model systematically under-projects
high-usage players and over-projects low lines, with the sign and size of
the error depending on where the betting line sits (a proxy for usage
tier).  Calibration fixes probability mapping but cannot move the mean of
the projection; this module shifts the model-side component means by the
measured, shrinkage-discounted bias for the band containing the line.

Design rules:

* Market-derived components are never touched - the bias is a property of
  our model, not of the market price.
* Bands with thin samples (< ``MIN_BAND_SAMPLE``) contribute nothing.
* Raw band bias is shrunk toward zero by ``n / (n + SHRINK_K)`` so small
  samples move the mean only slightly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

MIN_BAND_SAMPLE = 30
SHRINK_K = 25.0

# Band edges per market; a line belongs to the band whose lower edge is the
# largest edge <= line.  One edge below the smallest expected line anchors
# the lowest band.
BAND_EDGES: dict[str, tuple[float, ...]] = {
    "points": (10.0, 15.0, 20.0, 25.0),
    "rebounds": (5.0, 8.0, 11.0),
    "assists": (3.0, 5.0, 7.0),
    "three_pointers": (1.5, 2.5, 3.5),
    "points_rebounds_assists": (15.0, 25.0, 35.0),
    "points_rebounds": (15.0, 20.0, 30.0),
    "points_assists": (15.0, 20.0, 30.0),
    "rebounds_assists": (8.0, 12.0, 16.0),
}
DEFAULT_EDGES = (10.0, 20.0)


@dataclass(frozen=True)
class LineBand:
    """One fitted band: edges and the shrunk mean shift."""

    lower: float
    upper: float | None
    bias: float
    sample_size: int

    def contains(self, line: float) -> bool:
        if line < self.lower:
            return False
        return self.upper is None or line < self.upper


@dataclass(frozen=True)
class LineBandBias:
    """Per-market set of band biases with a fitted flag."""

    market: str
    bands: tuple[LineBand, ...] = field(default_factory=tuple)
    is_fitted: bool = False

    def bias_for(self, line: float) -> float:
        for band in self.bands:
            if band.contains(line):
                return band.bias
        return 0.0

    def apply(self, mean: float, line: float) -> float:
        return max(0.0, mean + self.bias_for(line))

    def to_payload(self) -> dict:
        return {
            "market": self.market,
            "is_fitted": self.is_fitted,
            "bands": [
                {
                    "lower": b.lower,
                    "upper": b.upper,
                    "bias": b.bias,
                    "sample_size": b.sample_size,
                }
                for b in self.bands
            ],
        }

    @staticmethod
    def from_payload(payload: dict) -> "LineBandBias":
        return LineBandBias(
            market=str(payload.get("market", "__all__")),
            is_fitted=bool(payload.get("is_fitted", False)),
            bands=tuple(
                LineBand(
                    lower=float(b["lower"]),
                    upper=None if b.get("upper") is None else float(b["upper"]),
                    bias=float(b.get("bias", 0.0)),
                    sample_size=int(b.get("sample_size", 0)),
                )
                for b in payload.get("bands", [])
            ),
        )


IDENTITY_BIAS = LineBandBias(market="__all__")


def _edges_for(market: str) -> tuple[float, ...]:
    return BAND_EDGES.get(market, DEFAULT_EDGES)


def fit_line_bias(
    points: Iterable[tuple[float, float, float]],
    *,
    market: str,
    minimum_sample: int = MIN_BAND_SAMPLE,
    shrink_k: float = SHRINK_K,
) -> LineBandBias:
    """Fit shrunk per-band bias from (line, projected_mean, actual) triples."""

    triples = [(float(line), float(projected), float(actual)) for line, projected, actual in points]
    if not triples:
        return LineBandBias(market=market)

    edges = _edges_for(market)
    bands: list[LineBand] = []
    fitted_any = False
    bounds = list(edges) + [None]
    lower = float("-inf")
    for upper in bounds:
        members = [t for t in triples if lower <= t[0] and (upper is None or t[0] < upper)]
        count = len(members)
        bias = 0.0
        if count >= minimum_sample:
            raw = sum(actual - projected for _, projected, actual in members) / count
            bias = raw * (count / (count + shrink_k))
            fitted_any = True
        bands.append(
            LineBand(
                lower=0.0 if lower == float("-inf") else lower,
                upper=upper,
                bias=bias,
                sample_size=count,
            )
        )
        if upper is None:
            break
        lower = upper

    if not fitted_any:
        return LineBandBias(market=market)
    return LineBandBias(market=market, bands=tuple(bands), is_fitted=True)
