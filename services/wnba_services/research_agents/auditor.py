"""The pre-flight that can refuse to let an investigation run.

Every other agent in this system is a language model reading evidence. The auditor is not, and
that is the point: the question "are these inputs fit to reason about" has a determinate answer,
and asking a model to answer it introduces exactly the failure mode the audit exists to catch.
A model handed contradictory inputs writes a fluent paragraph reconciling them. A deterministic
check handed contradictory inputs says so and stops.

It runs before any provider call, so an investigation over bad inputs costs nothing and -- more
importantly -- produces no claims that later have to be explained away. A cited, confident,
expiring claim built on a stale injury row is worse than no claim at all, because it looks like
evidence.

**Three severities, and only one of them stops anything.**

``blocking``
    The inputs contradict each other or are too stale to reason about. The plan is marked
    ``blocked`` and no agent is called. This is not an error state: a blocked plan is a correct
    outcome that the record should show, and the same plan can be re-audited once the input that
    caused it is fixed.

``degraded``
    Something is missing or thin, but an analyst could still form a defensible view of it. The
    investigation proceeds and the finding is handed to the agents as context, so the skeptic in
    particular has the data problem in front of it rather than having to infer it.

``note``
    Recorded, non-blocking, and mostly useful in aggregate: a run of the same note across a
    hundred plans is a data-quality finding about the pipeline rather than about one prop.

**What it does not do.** It does not judge the forecast. A projection can be wrong in every way a
model can be wrong and still have perfectly consistent inputs; the auditor has no opinion about
that, and adding one would make it a sixth analyst with a veto.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

__all__ = [
    "MAXIMUM_QUOTE_AGE",
    "AuditFinding",
    "AuditReport",
    "audit_plan_inputs",
    "record_audit",
]

# A quote older than this has stopped describing the market it was drawn from. Chosen to match
# the `require_evidence_on_stale_quotes` candidate rule: the threshold at which the system is
# already prepared to demand a live claim is the threshold at which research on that quote needs
# to know it is looking at history.
MAXIMUM_QUOTE_AGE = timedelta(minutes=90)

# Beyond this, the quote is not stale, it is archival, and no investigation of it is about the
# current market.
BLOCKING_QUOTE_AGE = timedelta(hours=6)

# A forecast built on fewer than this many games is thin enough that agents reasoning about
# "recent form" would be reasoning about noise.
THIN_HISTORY_GAMES = 8

_UNAVAILABLE = {"out", "doubtful"}


@dataclass(frozen=True)
class AuditFinding:
    code: str
    severity: str
    detail: str
    observed: dict[str, Any]

    @property
    def is_blocking(self) -> bool:
        return self.severity == "blocking"

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "observed": self.observed,
        }


@dataclass(frozen=True)
class AuditReport:
    verdict: str
    findings: tuple[AuditFinding, ...]

    @property
    def blocking(self) -> tuple[AuditFinding, ...]:
        return tuple(finding for finding in self.findings if finding.is_blocking)

    @property
    def is_blocked(self) -> bool:
        return self.verdict == "blocked"

    def context_for_agents(self) -> list[dict[str, Any]]:
        """Non-blocking findings, in the shape the evidence bundle carries them.

        Blocking findings are excluded because a blocked plan never reaches an agent. Everything
        else is handed over deliberately: an analyst who knows the role model has no row for this
        player writes a different paragraph than one who assumes it does.
        """
        return [finding.to_payload() for finding in self.findings if not finding.is_blocking]

    def to_payload(self) -> list[dict[str, Any]]:
        return [finding.to_payload() for finding in self.findings]


def audit_plan_inputs(inputs: dict[str, Any], *, now: datetime | None = None) -> AuditReport:
    """Check one investigation's inputs for staleness and self-contradiction.

    Pure, and takes a plain mapping rather than a cursor, so the whole decision table is testable
    without a database and every threshold above is a number somebody can argue with.
    """
    at = now or datetime.now(UTC)
    findings: list[AuditFinding] = []

    locks_at = inputs.get("locks_at")
    if isinstance(locks_at, datetime) and locks_at <= at:
        findings.append(
            AuditFinding(
                code="market_locked",
                severity="blocking",
                detail=(
                    "The market has already locked. Research after the lock cannot inform a "
                    "decision and would only be scored against an outcome it could not affect."
                ),
                observed={"locks_at": locks_at.isoformat(), "now": at.isoformat()},
            )
        )

    observed_at = inputs.get("quote_observed_at")
    if isinstance(observed_at, datetime):
        age = at - observed_at
        if age >= BLOCKING_QUOTE_AGE:
            findings.append(
                AuditFinding(
                    code="quote_archival",
                    severity="blocking",
                    detail=(
                        "The quote backing this forecast is hours old. Any conclusion drawn "
                        "about the market would describe a price that no longer exists."
                    ),
                    observed={"age_seconds": int(age.total_seconds())},
                )
            )
        elif age >= MAXIMUM_QUOTE_AGE:
            findings.append(
                AuditFinding(
                    code="quote_stale",
                    severity="degraded",
                    detail=(
                        "The quote has aged past the point where the platform already demands a "
                        "live claim before acting. Market conclusions should be treated as "
                        "historical."
                    ),
                    observed={"age_seconds": int(age.total_seconds())},
                )
            )

    designation = str(inputs.get("injury_designation") or "available").lower()
    availability = inputs.get("availability_probability")
    start_probability = inputs.get("start_probability")
    expected_minutes = inputs.get("expected_minutes")

    # The contradiction that matters most, and the one a language model is most likely to
    # smooth over: the injury feed says the player is out and the role model has them starting.
    if (
        designation in _UNAVAILABLE
        and expected_minutes is not None
        and float(str(expected_minutes)) >= 15.0
    ):
        findings.append(
            AuditFinding(
                code="availability_contradicts_role",
                severity="blocking",
                detail=(
                    f"The injury feed designates this player {designation!r} while the role "
                    "model projects a full rotation workload. One of the two inputs is "
                    "wrong and no analysis conditioned on both can be sound."
                ),
                observed={
                    "injury_designation": designation,
                    "expected_minutes": float(str(expected_minutes)),
                },
            )
        )
    if (
        designation in _UNAVAILABLE
        and availability is not None
        and float(str(availability)) >= 0.60
    ):
        findings.append(
            AuditFinding(
                code="availability_probability_contradicts_designation",
                severity="blocking",
                detail=(
                    f"Designation {designation!r} is paired with an availability "
                    "probability that says the player is likely to play. These come from "
                    "different stages of the pipeline and they disagree."
                ),
                observed={
                    "injury_designation": designation,
                    "availability_probability": float(str(availability)),
                },
            )
        )

    if start_probability is not None and expected_minutes is not None:
        starts = float(str(start_probability))
        minutes = float(str(expected_minutes))
        if starts >= 0.80 and minutes <= 8.0:
            findings.append(
                AuditFinding(
                    code="start_probability_contradicts_minutes",
                    severity="blocking",
                    detail=(
                        "The role model projects a near-certain start alongside fewer minutes "
                        "than a starter plays. The two halves of the same model disagree."
                    ),
                    observed={"start_probability": starts, "expected_minutes": minutes},
                )
            )

    if inputs.get("expected_minutes") is None:
        findings.append(
            AuditFinding(
                code="no_role_projection",
                severity="degraded",
                detail=(
                    "No projected-role row exists for this player and game, so minutes come "
                    "from history alone. Rotation conclusions rest on the player's own past "
                    "rather than on anything about tonight."
                ),
                observed={},
            )
        )

    history_games = inputs.get("history_games")
    if history_games is not None and int(str(history_games)) < THIN_HISTORY_GAMES:
        findings.append(
            AuditFinding(
                code="thin_history",
                severity="degraded",
                detail=(
                    f"Only {history_games} games of history back this forecast. Statements about "
                    "recent form are statements about a handful of games."
                ),
                observed={"history_games": int(str(history_games))},
            )
        )

    quality = inputs.get("data_quality_score")
    if quality is not None and float(str(quality)) < 0.50:
        findings.append(
            AuditFinding(
                code="low_data_quality",
                severity="degraded",
                detail=(
                    "The feature checklist for this forecast is substantially incomplete. "
                    "Absent features are absent from the evidence too."
                ),
                observed={"data_quality_score": float(str(quality))},
            )
        )

    contradictions = inputs.get("contradicting_claims")
    if contradictions is not None and int(str(contradictions)) > 0:
        findings.append(
            AuditFinding(
                code="unresolved_claim_conflict",
                severity="degraded",
                detail=(
                    "Live claims about this subject contradict each other. The investigation "
                    "should resolve the conflict rather than adding a third opinion to it."
                ),
                observed={"contradicting_claims": int(str(contradictions))},
            )
        )

    if any(finding.is_blocking for finding in findings):
        verdict = "blocked"
    elif findings:
        verdict = "degraded"
    else:
        verdict = "clear"
    return AuditReport(verdict=verdict, findings=tuple(findings))


def record_audit(
    cur: Any,
    plan_id: UUID,
    report: AuditReport,
    *,
    at: datetime,
    auditor: str = "data_auditor",
) -> UUID:
    """Persist the audit and move the plan if it was blocked.

    A blocked plan keeps its findings so the same audit does not have to be re-derived to explain
    why nothing ran. Re-auditing later is a new row, not an overwrite: whether an input was
    contradictory an hour ago is a fact about an hour ago.
    """
    audit_id = uuid4()
    cur.execute(
        """INSERT INTO wnba.research_audits
           (audit_id,plan_id,audited_at,verdict,findings,blocking_count,auditor)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (
            audit_id,
            plan_id,
            at,
            report.verdict,
            Jsonb(report.to_payload()),
            len(report.blocking),
            auditor,
        ),
    )
    if report.is_blocked:
        reasons = "; ".join(finding.code for finding in report.blocking)
        cur.execute(
            """UPDATE wnba.research_plans
               SET status='blocked',completed_at=%s,detail=%s
               WHERE plan_id=%s""",
            (at, f"data audit blocked the investigation: {reasons}"[:500], plan_id),
        )
    return audit_id
