"""Fail-closed audit of the frozen evidence available to research agents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class AuditIssue:
    code: str
    severity: str
    detail: str


@dataclass(frozen=True)
class AuditReport:
    audit_id: UUID
    status: str
    issues: tuple[AuditIssue, ...]
    freshness_seconds: int

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"


def audit_projection(projection: dict[str, Any], *, now: datetime | None = None) -> AuditReport:
    at = now or datetime.now(UTC)
    issues: list[AuditIssue] = []
    observed = projection.get("observed_at")
    freshness = (
        max(0, int((at - observed).total_seconds())) if isinstance(observed, datetime) else 0
    )
    if not isinstance(observed, datetime):
        issues.append(
            AuditIssue("MISSING_QUOTE_TIME", "critical", "Quote observation time absent.")
        )
    elif freshness > 7200:
        issues.append(AuditIssue("STALE_QUOTE", "critical", f"Quote is {freshness}s old."))
    features = dict(projection.get("features") or {})
    for field in ("expected_minutes", "history_games", "expected_possessions"):
        if features.get(field) is None:
            issues.append(
                AuditIssue("MISSING_FEATURE", "critical", f"Required feature {field} absent.")
            )
    quality = float(projection.get("data_quality_score") or 0)
    if quality < 0.60:
        issues.append(AuditIssue("LOW_DATA_QUALITY", "critical", f"Data quality is {quality:.3f}."))
    locks_at = projection.get("locks_at")
    if not isinstance(locks_at, datetime) or locks_at <= at:
        issues.append(
            AuditIssue("MARKET_LOCKED", "critical", "Market is locked or lock time invalid.")
        )
    status = (
        "blocked"
        if any(issue.severity == "critical" for issue in issues)
        else ("warn" if issues else "pass")
    )
    return AuditReport(uuid4(), status, tuple(issues), freshness)


def persist_audit(cur: Any, projection_id: UUID, report: AuditReport, *, at: datetime) -> None:
    cur.execute(
        """INSERT INTO wnba.research_audits
           (audit_id,projection_id,audited_at,status,issues,freshness_seconds)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (
            report.audit_id,
            projection_id,
            at,
            report.status,
            Jsonb([issue.__dict__ for issue in report.issues]),
            report.freshness_seconds,
        ),
    )
