"""Which forecasts become candidates -- and how much of the apparent edge to believe.

Two failures sat at the end of the pipeline, and both of them flattered the model.

**The gate was a magic number.** ``probability >= 0.58`` appeared in the source with nothing
connecting it to the product being priced. A two-pick power play at 3x needs **57.7%** per leg
to break even; a three-pick at 6x needs about **55.0%**. One constant cannot be right for both,
and when the payout table changes it is right for neither. :func:`breakeven_probability` reads
the number off the payout rule, so the gate moves when the product moves.

**The gate was applied to an unshrunk edge.** The pipeline scores thousands of props a night
and forwards the ones that clear a threshold. That is a selection procedure, and it selects on
estimation noise: among all the props where the model says 0.62, the ones that got there partly
by luck are over-represented, so the group hits well under 0.62. This is the same regression to
the mean that makes a rookie's hot April so unhelpful.

:class:`EdgeShrinkage` corrects it, and does so without a tuning knob. For a forecast ``q`` and
outcome ``y``, ``E[(y - 1/2)(q - 1/2)]`` equals the variance of the *true* edges, while
``Var(q)`` is that plus estimation noise. Their ratio is the fraction of the observed edge worth
believing, and both quantities are directly measurable from settled episodes. Until enough have
settled, the fallback halves the edge -- a stated prior rather than an invisible one.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from wnba_domain.enums import RecommendationStatus
from wnba_domain.market import PayoutRule
from wnba_marketmath.pickem import breakeven_uniform_leg_probability

__all__ = [
    "COLD_START_SHRINK_FACTOR",
    "MINIMUM_SHRINK_SAMPLE",
    "CandidateDecision",
    "EdgeShrinkage",
    "breakeven_probability",
    "decide_candidate",
]

# With no settled evidence that our edges are real, believe half of each one. Conservative by
# construction: the cost of under-betting a genuine edge is opportunity, the cost of
# over-betting a phantom one is money.
COLD_START_SHRINK_FACTOR = 0.5
MINIMUM_SHRINK_SAMPLE = 200

# How far past break-even a shrunk probability must sit before it is worth calling a candidate.
# Covers the part of the payout table we have not verified and the correlation we have not yet
# estimated, both of which move EV against us rather than for us.
DEFAULT_MARGIN = 0.02

_MAX_SHRINK_FACTOR = 1.0


@dataclass(frozen=True)
class EdgeShrinkage:
    """The fraction of an apparent edge that survives regression to the mean."""

    factor: float
    sample_size: int
    is_fitted: bool = False

    def apply(self, probability: float) -> float:
        """Pull a probability toward 0.5 by ``1 - factor``. Never past it, never away from it."""
        clamped = max(0.0, min(_MAX_SHRINK_FACTOR, self.factor))
        return 0.5 + (probability - 0.5) * clamped

    def to_payload(self) -> dict[str, Any]:
        return {
            "factor": self.factor,
            "sample_size": self.sample_size,
            "is_fitted": self.is_fitted,
        }

    @classmethod
    def cold_start(cls) -> EdgeShrinkage:
        return cls(factor=COLD_START_SHRINK_FACTOR, sample_size=0)

    @classmethod
    def from_settled(
        cls,
        points: Sequence[tuple[float, float]],
        *,
        minimum_sample: int = MINIMUM_SHRINK_SAMPLE,
    ) -> EdgeShrinkage:
        """Estimate the shrinkage factor from ``(probability, outcome)`` pairs.

        ``Cov(q, y)`` over the observed spread of ``q`` is the signal-to-total ratio: it is 1
        when every point of stated edge shows up in the results and 0 when none of it does. A
        negative estimate means the edges pointed the wrong way, which is not a licence to
        invert them -- it collapses to zero, and the drift incident it should raise is the
        learning loop's business.
        """
        usable = [
            (probability, float(outcome))
            for probability, outcome in points
            if 0.0 <= probability <= 1.0 and outcome in (0.0, 1.0, 0, 1, True, False)
        ]
        if len(usable) < minimum_sample:
            return cls.cold_start()

        count = len(usable)
        mean_probability = math.fsum(probability for probability, _ in usable) / count
        observed_variance = (
            math.fsum((probability - mean_probability) ** 2 for probability, _ in usable) / count
        )
        if observed_variance <= 1e-9:
            # Every forecast said the same thing; there is no edge spread to shrink.
            return cls(factor=0.0, sample_size=count, is_fitted=True)

        signal_variance = (
            math.fsum(
                (outcome - 0.5) * (probability - mean_probability)
                for probability, outcome in usable
            )
            / count
        )
        factor = max(0.0, min(_MAX_SHRINK_FACTOR, signal_variance / observed_variance))
        return cls(factor=factor, sample_size=count, is_fitted=True)


def breakeven_probability(rule: PayoutRule) -> float:
    """Per-leg hit rate this payout rule needs to break even, assuming independent legs.

    Independence is the optimistic assumption for a power play and the pessimistic one for a
    flex, which is why this is a gate rather than a price. Actual entry EV goes through the
    correlated simulator in ``wnba_sim``; nothing here is a substitute for that.
    """
    return breakeven_uniform_leg_probability(rule)


@dataclass(frozen=True)
class CandidateDecision:
    """The gate's verdict on one forecast, with the number that decided it."""

    status: RecommendationStatus
    side: str
    raw_probability: float
    shrunk_probability: float
    breakeven: float
    reason: str

    @property
    def edge(self) -> float:
        """Shrunk probability less the break-even the payout table demands."""
        return self.shrunk_probability - self.breakeven


def decide_candidate(
    *,
    over_probability: float,
    under_probability: float,
    shrinkage: EdgeShrinkage,
    breakeven: float,
    quality: float,
    disagreement: float,
    injury_designation: str,
    rule_blocked: bool = False,
    rule_reason: str = "",
    margin: float = DEFAULT_MARGIN,
    minimum_quality: float = 0.85,
    maximum_disagreement: float = 0.12,
) -> CandidateDecision:
    """Choose a side and decide whether the shrunk edge clears the payout table.

    Order matters and is deliberate: a rule block outranks an injury block, which outranks any
    amount of apparent edge. Every one of these paths can only *decline*; there is no branch
    that promotes a forecast the arithmetic did not support.
    """
    side = "over" if over_probability >= under_probability else "under"
    raw = max(over_probability, under_probability)
    shrunk = shrinkage.apply(raw)

    if rule_blocked:
        return CandidateDecision(
            RecommendationStatus.BLOCKED_BY_SKEPTIC,
            side,
            raw,
            shrunk,
            breakeven,
            rule_reason or "blocked by an active analyst rule",
        )
    if injury_designation in {"questionable", "doubtful", "unknown"}:
        return CandidateDecision(
            RecommendationStatus.BLOCKED_BY_SKEPTIC,
            side,
            raw,
            shrunk,
            breakeven,
            f"availability is {injury_designation}",
        )
    if quality < minimum_quality:
        return CandidateDecision(
            RecommendationStatus.BLOCKED_DATA_QUALITY,
            side,
            raw,
            shrunk,
            breakeven,
            f"data quality {quality:.2f} below {minimum_quality:.2f}",
        )
    if disagreement > maximum_disagreement:
        return CandidateDecision(
            RecommendationStatus.BLOCKED_CALIBRATION,
            side,
            raw,
            shrunk,
            breakeven,
            f"component disagreement {disagreement:.3f} above {maximum_disagreement:.3f}",
        )
    if shrunk < breakeven + margin:
        return CandidateDecision(
            RecommendationStatus.DECLINED_NO_EDGE,
            side,
            raw,
            shrunk,
            breakeven,
            f"shrunk {shrunk:.3f} below break-even {breakeven:.3f} plus {margin:.3f}",
        )
    return CandidateDecision(
        RecommendationStatus.CANDIDATE,
        side,
        raw,
        shrunk,
        breakeven,
        f"shrunk {shrunk:.3f} clears break-even {breakeven:.3f} plus {margin:.3f}",
    )
