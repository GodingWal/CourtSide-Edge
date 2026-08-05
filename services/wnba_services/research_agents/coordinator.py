"""Deterministic research planning and automatic trigger policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

ROLE_QUESTIONS = {
    "availability": "What availability or restriction evidence could invalidate the minutes state?",
    "rotation": "How stable are the role, start, minutes, and closing-lineup assumptions?",
    "matchup": "Which opponent, pace, rest, and blowout conditions materially change uncertainty?",
    "market": "Is the quote fresh, corroborated, and consistent with available news?",
    "skeptic": "What is the strongest evidence against trusting this forecast?",
}


@dataclass(frozen=True)
class ResearchPlan:
    plan_id: UUID
    trigger_codes: tuple[str, ...]
    questions: dict[str, str]
    required_evidence_groups: tuple[str, ...]


def trigger_codes(projection: dict[str, Any], *, now: datetime | None = None) -> tuple[str, ...]:
    at = now or datetime.now(UTC)
    features = dict(projection.get("features") or {})
    codes: list[str] = []
    designation = str(features.get("injury_designation") or "available").lower()
    if designation not in {"available", "active", "probable"}:
        codes.append("MATERIAL_INJURY_STATUS")
    if float(projection.get("model_disagreement") or 0) >= 0.10:
        codes.append("HIGH_MODEL_DISAGREEMENT")
    observed = projection.get("observed_at")
    if isinstance(observed, datetime) and (at - observed).total_seconds() >= 3600:
        codes.append("STALE_MARKET_QUOTE")
    start = features.get("start_probability")
    if start is not None and 0.35 <= float(start) <= 0.65:
        codes.append("UNRESOLVED_STARTER")
    if float(features.get("teammate_effect_count") or 0) > 0:
        codes.append("TEAMMATE_ROLE_CHANGE")
    return tuple(codes)


def build_plan(projection: dict[str, Any], *, now: datetime | None = None) -> ResearchPlan:
    return ResearchPlan(
        plan_id=uuid4(),
        trigger_codes=trigger_codes(projection, now=now),
        questions=dict(ROLE_QUESTIONS),
        required_evidence_groups=(
            "availability",
            "role_effects",
            "matchup",
            "market",
            "forecast_audit",
        ),
    )


def persist_plan(cur: Any, projection_id: UUID, plan: ResearchPlan, *, at: datetime) -> None:
    cur.execute(
        """INSERT INTO wnba.research_plans
           (plan_id,projection_id,created_at,trigger_codes,questions,required_evidence_groups,status)
           VALUES (%s,%s,%s,%s,%s,%s,'planned')""",
        (
            plan.plan_id,
            projection_id,
            at,
            list(plan.trigger_codes),
            Jsonb(plan.questions),
            list(plan.required_evidence_groups),
        ),
    )
