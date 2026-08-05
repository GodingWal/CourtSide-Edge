"""Deciding what is worth investigating, before anybody investigates it.

The workflow had one entry point: a human passing a projection id to a CLI command. That is an
analyst review tool. A research organization decides for itself what deserves attention, states
why, and can be audited afterwards for having attended to the wrong things.

Five triggers earn a plan, and each one is a *change* rather than a level. Researching the props
with the biggest edges would produce a queue that is a description of the model's confidence; a
queue built from changes is a description of what happened in the world since the last look.

``injury_change``
    An availability row was written after the forecast was made. The forecast is now stale in the
    one input that matters most, and no amount of modelling fixes reading the wrong designation.

``role_change``
    A projected-role row postdates the forecast: minutes or the starter question moved.

``model_disagreement``
    The components disagree beyond the level at which pooled sharpness has historically been an
    artefact. This is the trigger that looks at the model rather than the world, and it is here
    because the learning loop attributes a large share of its avoidable errors to exactly this
    signature.

``claim_expiry``
    A claim the last investigation relied on has passed its refresh time while the market is
    still open. The conclusion may still hold; what is no longer true is that anybody checked.

``candidate_recommendation``
    The decision gate produced a candidate. A prop the system is prepared to act on deserves more
    scrutiny than one it already declined, and this is the only trigger that keys on the
    decision.

A plan is inert. It names a projection, a set of roles, a priority and a reason, and it does not
call a model -- :func:`run_planned_research` does that, and only after the auditor has cleared
the inputs. Planning and executing are separated so that "what did we choose to look at" and
"what did the analysts say" are two questions with two answers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_store.db import connect

__all__ = [
    "DISAGREEMENT_TRIGGER",
    "PLAN_ROLES",
    "PlannedInvestigation",
    "PlanningBatch",
    "expire_stale_plans",
    "pending_plans",
    "plan_investigations",
]

# Component spread above which pooled sharpness has historically been an artefact rather than
# evidence. The same number the `shrink_when_components_disagree` candidate rule uses -- if the
# system is prepared to propose shrinking here, it should be prepared to look here.
DISAGREEMENT_TRIGGER = 0.10

# A plan needs at least this long before lock to be worth running. Research that lands after the
# market closes is a cost with no decision attached to it.
MINIMUM_LEAD = timedelta(minutes=12)

# Which roles each trigger actually needs. Running all five on every plan is what a review tool
# does; a coordinator sends the availability analyst to an injury change and does not send the
# matchup analyst to look at a line move.
PLAN_ROLES: dict[str, tuple[str, ...]] = {
    "injury_change": ("availability", "rotation", "skeptic"),
    "role_change": ("rotation", "availability", "skeptic"),
    "model_disagreement": ("rotation", "matchup", "market", "skeptic"),
    "claim_expiry": ("availability", "rotation", "market", "skeptic"),
    "line_move": ("market", "skeptic"),
    "candidate_recommendation": ("availability", "rotation", "matchup", "market", "skeptic"),
    "manual": ("availability", "rotation", "matchup", "market", "skeptic"),
}

_TRIGGER_PRIORITY: dict[str, int] = {
    "injury_change": 90,
    "role_change": 80,
    "candidate_recommendation": 70,
    "claim_expiry": 65,
    "model_disagreement": 55,
    "line_move": 50,
    "manual": 60,
}


@dataclass(frozen=True)
class PlannedInvestigation:
    plan_id: UUID
    projection_id: UUID
    trigger_kind: str
    priority: int
    roles: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class PlanningBatch:
    considered: int
    planned: int
    skipped_open: int
    expired: int
    plans: tuple[PlannedInvestigation, ...] = ()


def _roles_for(trigger: str) -> list[str]:
    return list(PLAN_ROLES.get(trigger, PLAN_ROLES["manual"]))


def expire_stale_plans(cur: Any, *, now: datetime) -> int:
    """Close plans whose market locked before anybody got to them.

    An unexecuted plan is not a failure worth alerting on, but leaving it `planned` forever makes
    the queue depth meaningless. Expiring it keeps the record of what the coordinator wanted to
    look at and could not.
    """
    cur.execute(
        """UPDATE wnba.research_plans
           SET status='expired',completed_at=%s,
               detail='market locked before the investigation ran'
           WHERE status IN ('planned','auditing') AND locks_at<=%s""",
        (now, now),
    )
    return int(cur.rowcount)


def plan_investigations(
    *,
    now: datetime | None = None,
    limit: int = 25,
    planned_by: str = "research_coordinator",
) -> PlanningBatch:
    """Queue investigations for open markets that have changed since they were forecast.

    Ordered by trigger priority and lock time, so the queue drains toward the props that are both
    most affected and closest to being un-actionable. Nothing here calls a language model.
    """
    at = now or datetime.now(UTC)
    deadline = at + MINIMUM_LEAD
    planned: list[PlannedInvestigation] = []
    considered = skipped = 0

    with connect() as conn, conn.cursor() as cur:
        expired = expire_stale_plans(cur, now=at)

        # One pass over open forecasts, computing every trigger at once. The alternative -- a
        # query per trigger -- rescans the same board five times and lets two triggers each
        # queue the same projection before either sees the other's row.
        cur.execute(
            """SELECT f.projection_id,f.player_id,f.game_id,f.prop_type,f.generated_at,
                      f.expires_at AS locks_at,f.line,
                      d.episode_id,d.model_disagreement,d.system_recommendation,
                      q.system_from AS quote_observed_at,
                      (SELECT max(i.system_from) FROM wnba.injury_status i
                        WHERE i.player_id=f.player_id AND i.game_id=f.game_id
                          AND i.system_to IS NULL) AS injury_written_at,
                      (SELECT max(r.system_from) FROM wnba.projected_roles r
                        WHERE r.player_id=f.player_id AND r.game_id=f.game_id
                          AND r.system_to IS NULL) AS role_written_at,
                      (SELECT min(c.refresh_after) FROM wnba.research_claims c
                        JOIN wnba.agent_analyses a ON a.analysis_id=c.analysis_id
                        JOIN wnba.research_runs rr ON rr.research_run_id=a.research_run_id
                        WHERE rr.projection_id=f.projection_id AND c.superseded_at IS NULL
                          AND c.refresh_after IS NOT NULL) AS claim_refresh_due
               FROM wnba.stat_forecasts f
               JOIN wnba.decision_episodes d ON d.quote_id=f.quote_id
                 AND d.model_run_id=f.model_run_id
               JOIN wnba.prop_quotes q ON q.quote_id=f.quote_id
               WHERE f.expires_at>%s
                 AND NOT EXISTS (
                     SELECT 1 FROM wnba.research_plans p
                     WHERE p.projection_id=f.projection_id
                       AND p.status IN ('planned','auditing','running'))
               ORDER BY f.expires_at""",
            (deadline,),
        )
        rows = [dict(row) for row in cur.fetchall()]

        candidates: list[tuple[int, datetime, dict[str, Any], str, str, dict[str, Any]]] = []
        for row in rows:
            considered += 1
            trigger = _classify(row, at)
            if trigger is None:
                continue
            kind, reason, detail = trigger
            locks_at = row["locks_at"]
            if not isinstance(locks_at, datetime):
                continue
            candidates.append((_TRIGGER_PRIORITY[kind], locks_at, row, kind, reason, detail))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        for priority, locks_at, row, kind, reason, detail in candidates[: max(0, limit)]:
            plan_id = uuid4()
            roles = _roles_for(kind)
            cur.execute(
                """INSERT INTO wnba.research_plans
                   (plan_id,projection_id,episode_id,planned_at,planned_by,trigger_kind,
                    trigger_detail,priority,roles,questions,reason,locks_at,status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'planned')
                   ON CONFLICT DO NOTHING""",
                (
                    plan_id,
                    row["projection_id"],
                    row["episode_id"],
                    at,
                    planned_by,
                    kind,
                    Jsonb(detail),
                    priority,
                    roles,
                    Jsonb({}),
                    reason,
                    locks_at,
                ),
            )
            if cur.rowcount == 0:
                skipped += 1
                continue
            planned.append(
                PlannedInvestigation(
                    plan_id=plan_id,
                    projection_id=UUID(str(row["projection_id"])),
                    trigger_kind=kind,
                    priority=priority,
                    roles=tuple(roles),
                    reason=reason,
                )
            )

    return PlanningBatch(
        considered=considered,
        planned=len(planned),
        skipped_open=skipped,
        expired=expired,
        plans=tuple(planned),
    )


def _classify(row: dict[str, Any], at: datetime) -> tuple[str, str, dict[str, Any]] | None:
    """The highest-priority trigger this forecast satisfies, or ``None``.

    One trigger per plan rather than a list, because the trigger determines which roles run and a
    plan that fires for four reasons at once would just run everything -- which is the behaviour
    the coordinator exists to replace.
    """
    generated_at = row["generated_at"]
    if not isinstance(generated_at, datetime):
        return None

    injury_at = row["injury_written_at"]
    if isinstance(injury_at, datetime) and injury_at > generated_at:
        return (
            "injury_change",
            "An availability row was written after this forecast was made, so the forecast "
            "read a designation that has since changed.",
            {"injury_written_at": injury_at.isoformat(), "forecast_at": generated_at.isoformat()},
        )

    role_at = row["role_written_at"]
    if isinstance(role_at, datetime) and role_at > generated_at:
        return (
            "role_change",
            "A projected-role row postdates this forecast: expected minutes or the starter "
            "question moved after the probability was computed.",
            {"role_written_at": role_at.isoformat(), "forecast_at": generated_at.isoformat()},
        )

    refresh_due = row["claim_refresh_due"]
    if isinstance(refresh_due, datetime) and refresh_due <= at:
        return (
            "claim_expiry",
            "A research claim this projection relied on has passed its refresh time while the "
            "market is still open. Nobody has re-checked it since.",
            {"refresh_after": refresh_due.isoformat()},
        )

    if str(row["system_recommendation"]) == "candidate":
        return (
            "candidate_recommendation",
            "The decision gate produced a candidate. A prop the system is prepared to act on "
            "warrants more scrutiny than one it already declined.",
            {"system_recommendation": "candidate"},
        )

    disagreement = row["model_disagreement"]
    if disagreement is not None and float(str(disagreement)) > DISAGREEMENT_TRIGGER:
        return (
            "model_disagreement",
            "Component spread is above the level at which the ensemble's sharpness has "
            "historically been an artefact of pooling rather than evidence.",
            {
                "model_disagreement": float(str(disagreement)),
                "threshold": DISAGREEMENT_TRIGGER,
            },
        )
    return None


def pending_plans(
    cur: Any, *, limit: int = 20, now: datetime | None = None
) -> list[dict[str, Any]]:
    """The queue, highest priority first, excluding anything whose market has closed."""
    at = now or datetime.now(UTC)
    cur.execute(
        """SELECT p.*,f.player_id,f.prop_type,f.line,pl.full_name
           FROM wnba.research_plans p
           JOIN wnba.stat_forecasts f ON f.projection_id=p.projection_id
           JOIN wnba.players pl ON pl.player_id=f.player_id
           WHERE p.status='planned' AND p.locks_at>%s
           ORDER BY p.priority DESC,p.locks_at LIMIT %s""",
        (at, max(1, limit)),
    )
    return [dict(row) for row in cur.fetchall()]


def role_questions(roles: Sequence[str], base: dict[str, str]) -> dict[str, str]:
    """Narrow the standard question set to the roles this plan actually asked for."""
    return {role: base[role] for role in roles if role in base}
