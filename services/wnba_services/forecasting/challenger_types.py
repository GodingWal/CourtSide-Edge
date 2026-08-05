"""The shape a challenger returns, in a module neither challenger family has to import from.

Extracted rather than left in ``challengers`` because the registry there has to construct every
family, and the tree family has to construct a ``ChallengerPrediction`` -- which is a cycle. The
cycle could be deferred with a lazy import inside the registry, and that would work until the
next person imports the two modules in the other order.

A shared type belongs in a module that depends on neither of its users. That is the whole fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from wnba_services.forecasting.scoring import ScoringInputs

__all__ = ["Challenger", "ChallengerPrediction"]


@dataclass(frozen=True)
class ChallengerPrediction:
    """One challenger's answer for one prop, in the shape the champion reports."""

    name: str
    version: str
    pmf: tuple[float, ...]
    mean: float
    stddev: float
    over: float
    push: float
    under: float
    diagnostics: dict[str, Any]

    def probability_for(self, side: str) -> float:
        return self.over if side == "over" else self.under


class Challenger(Protocol):
    """What the experiment runner needs from a model family."""

    name: str
    version: str

    def specification(self) -> dict[str, Any]:
        """The stored description of this model, for ``wnba.model_versions.specification``."""
        ...

    def predict(self, inputs: ScoringInputs) -> ChallengerPrediction:
        """Score one prop. Pure: same inputs, same output, always."""
        ...
