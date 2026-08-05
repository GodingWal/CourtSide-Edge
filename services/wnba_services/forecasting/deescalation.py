"""Acting on the drift the system already measures.

``drift_incidents`` has been opened, severity-graded, auto-resolved and displayed since migration
013. Every row carried an ``automatic_response`` -- ``reduce_confidence`` or
``require_manual_review`` -- and nothing anywhere read the column. The system could see its own
calibration decaying, say what it intended to do about it, and then not do it.

This module is the missing consumer, and it is deliberately the least clever thing that could
work: while an unresolved calibration incident is open against the model version producing the
forecast, every probability that model emits is shrunk toward even before it reaches the
recommendation gate.

**Why this is safe to automate when promotion is not.** The system's standing asymmetry is that
it may become more cautious on its own and may never become more confident on its own. Shrinking
toward even is strictly the former: it removes edge, it moves probabilities toward 0.5, and there
is no input to this module that could sharpen a forecast. The database enforces that too --
``deescalation_events`` rejects a row whose ``probability_after`` is further from even than its
``probability_before``. The escape from de-escalation is not a decision this code can make: the
incident resolves when measured calibration returns to health, which is arithmetic, or a human
closes it.

**``require_manual_review`` does not hard-block here.** Blocking outright would silently change
what the recommendation gate means, and that gate's contract belongs to ``decide_candidate``. A
0.50 shrink drops nearly everything under the candidate threshold on its own, which produces the
same practical outcome by a route that stays visible in the numbers rather than hidden in a
status.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

__all__ = [
    "BLOCKING_SHRINK",
    "WARNING_SHRINK",
    "DriftGuard",
    "load_drift_guard",
    "record_deescalation",
    "shrink_toward_even",
]

# Same magnitude vocabulary the rule language uses, so a reader comparing a rule firing with a
# drift response is comparing like with like.
WARNING_SHRINK = 0.25
BLOCKING_SHRINK = 0.50


def shrink_toward_even(probability: float, magnitude: float) -> float:
    """Move a probability a fraction of the way to 0.5. Never away from it."""
    return 0.5 + (probability - 0.5) * (1.0 - magnitude)


@dataclass(frozen=True)
class DriftGuard:
    """The strongest unresolved calibration incident against one model version."""

    incident_id: UUID | None
    response: str | None
    magnitude: float
    severity: str | None
    component: str | None
    observed_value: float | None

    @property
    def active(self) -> bool:
        return self.incident_id is not None and self.magnitude > 0.0

    def apply(self, probability: float) -> float:
        if not self.active:
            return probability
        return shrink_toward_even(probability, self.magnitude)

    def detail(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "component": self.component,
            "observed_calibration_error": self.observed_value,
            "magnitude": self.magnitude,
            "response": self.response,
            "automatic": True,
            "reversal_requires_resolution": True,
        }


_NO_GUARD = DriftGuard(None, None, 0.0, None, None, None)


def load_drift_guard(cur: Any, model_version_id: UUID) -> DriftGuard:
    """The strongest open calibration incident for this model version, or an inert guard.

    Blocking outranks warning; among equals the most recently opened wins. One guard is applied
    per forecast rather than one per component, because a run emits a single probability and
    stacking shrinks per component would compound a correction the evidence does not support.
    """
    cur.execute(
        """SELECT incident_id,component_name,severity,automatic_response,observed_value
           FROM wnba.drift_incidents
           WHERE model_version_id=%s AND metric='calibration_error' AND resolved_at IS NULL
           ORDER BY (severity='blocking') DESC, opened_at DESC
           LIMIT 1""",
        (model_version_id,),
    )
    row = cur.fetchone()
    if row is None:
        return _NO_GUARD
    severity = str(row["severity"])
    return DriftGuard(
        incident_id=UUID(str(row["incident_id"])),
        response=str(row["automatic_response"]),
        magnitude=BLOCKING_SHRINK if severity == "blocking" else WARNING_SHRINK,
        severity=severity,
        component=str(row["component_name"]),
        observed_value=(
            None if row["observed_value"] is None else float(str(row["observed_value"]))
        ),
    )


def record_deescalation(
    cur: Any,
    guard: DriftGuard,
    *,
    episode_id: UUID,
    before: float,
    after: float,
    at: datetime,
) -> int:
    """Write the audit row. A forecast that was widened should be able to say why."""
    if not guard.active or guard.incident_id is None:
        return 0
    cur.execute(
        """INSERT INTO wnba.deescalation_events
           (event_id,incident_id,episode_id,applied_at,response,probability_before,
            probability_after,detail)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            uuid4(),
            guard.incident_id,
            episode_id,
            at,
            guard.response,
            before,
            after,
            Jsonb(guard.detail()),
        ),
    )
    return 1
