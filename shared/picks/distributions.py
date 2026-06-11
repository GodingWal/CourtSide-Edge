"""Distribution outputs for projections (P1-1).

The projection model outputs a distribution per player-stat, never a bare
mean: counting stats are modeled as negative binomial (overdispersed Poisson)
with per-player dispersion fit from game logs (minimum 15 games; positional
fallback otherwise). Every pick is then expressed as P(actual > line).

With WNBA points sigma around 5-6, a mean edge under ~1.5 points rarely
clears the breakeven threshold — that is intended behavior, not a bug.
"""
import math
from statistics import fmean, pvariance

from shared.picks.config import load_config

POISSON = math.inf  # dispersion sentinel: variance == mean


def fit_dispersion(samples: list[float], config: dict | None = None,
                   position: str = "", stat: str = "PTS") -> float:
    """Negative binomial dispersion r via method of moments.

    var = mean + mean^2 / r  =>  r = mean^2 / (var - mean).
    Returns POISSON (infinite r) when the sample is underdispersed, and the
    positional fallback when fewer than the configured minimum games exist.
    """
    cfg = (config or load_config())["dispersion"]
    if len(samples) < cfg["min_games"]:
        by_position = cfg["fallback_r_by_position"].get(position.upper(), {})
        return float(by_position.get(stat.upper(), cfg["fallback_r_default"]))
    mean = fmean(samples)
    var = pvariance(samples)
    if var <= mean or mean <= 0:
        return POISSON
    return mean * mean / (var - mean)


def std_from_dispersion(mean: float, r: float) -> float:
    if math.isinf(r):
        return math.sqrt(mean)
    return math.sqrt(mean + mean * mean / r)


def p_over(mean: float, line: float, r: float = POISSON) -> float:
    """P(X > line) for a negative binomial (or Poisson) with this mean.

    Pick'em lines are half-numbers so there is no push; for integer lines this
    is the strict-over probability.
    """
    from scipy import stats  # local import: agents without scipy never hit this

    threshold = math.floor(line)
    if math.isinf(r):
        return float(stats.poisson(mu=mean).sf(threshold))
    p = r / (r + mean)
    return float(stats.nbinom(n=r, p=p).sf(threshold))


def normal_p_over(mean: float, std: float, line: float) -> float:
    """Normal-approximation P(X > line) from a pick's stored (mean, std).

    Used at publication time to re-check a moved line without refitting the
    full distribution. stdlib-only so the publisher image stays slim.
    """
    if std <= 0:
        return 1.0 if mean > line else 0.0
    z = (line - mean) / std
    return 0.5 * math.erfc(z / math.sqrt(2))


def project(samples: list[float], line: float, *, config: dict | None = None,
            position: str = "", stat: str = "PTS",
            haircut_scale: float = 1.0) -> dict:
    """Full distribution output for one player-stat: {mean, std, p_over}.

    `haircut_scale` applies any blowout minutes haircut pre-distribution
    (see shared.picks.minutes_risk.apply_haircut).
    """
    if not samples:
        raise ValueError("project() requires at least one game log sample")
    r = fit_dispersion(samples, config=config, position=position, stat=stat)
    mean = fmean(samples) * haircut_scale
    return {
        "mean": round(mean, 3),
        "std": round(std_from_dispersion(mean, r), 3),
        "p_over": round(p_over(mean, line, r), 4),
    }
