"""Moving a rule from an idea to something an approver can rule on.

The lifecycle the schema describes is ``proposed -> backtested -> active``, with two locks on the
last step: a named human approver and a stored backtest. Nothing implemented the middle step, so
in practice the lifecycle was ``proposed -> forever``.

This module runs the middle step. It loads proposed rules, replays each against the settled
record, writes the evidence to ``analyst_rules.backtest``, and advances the status to
``backtested``. That is where it stops, permanently and by construction: there is no code path
here that writes ``active``, and there is no argument a research agent can make that would create
one. Activation is a human typing their own name, which is the whole point of the asymmetry the
rules package was built around.

A rule whose backtest comes back ``harmful`` is marked ``rejected`` with its evidence attached --
the record of a rule that was tried and failed is worth as much as the record of one that worked,
and rather more than a rule that quietly disappears.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from psycopg.types.json import Jsonb
from wnba_rules.dsl import Rule, RuleStatus
from wnba_store.db import connect

from wnba_services.forecasting.baseline import default_breakeven
from wnba_services.forecasting.rules import build_facts, load_rules
from wnba_services.learning_loop.rule_backtest import (
    RuleBacktest,
    SettledEpisode,
    backtest_rule,
)

__all__ = [
    "RuleApproval",
    "RuleLifecycleBatch",
    "approve_rule",
    "approve_rule_with_cursor",
    "load_settled_episodes",
    "retire_rule",
    "run_rule_backtests",
]


@dataclass(frozen=True)
class RuleApproval:
    rule_id: str
    status: str
    actor: str
    changed_at: datetime


def _named_actor(value: str) -> str:
    actor = value.strip()
    if len(actor) < 2:
        raise ValueError("a named human actor is required")
    if len(actor) > 100:
        raise ValueError("actor must be at most 100 characters")
    return actor


def _required_reason(value: str) -> str:
    reason = value.strip()
    if len(reason) < 10:
        raise ValueError("an audit reason of at least 10 characters is required")
    if len(reason) > 1000:
        raise ValueError("audit reason must be at most 1000 characters")
    return reason


def approve_rule_with_cursor(
    cur: Any,
    rule_id: str,
    *,
    approved_by: str,
    reason: str,
    now: datetime | None = None,
) -> RuleApproval:
    """Activate one helpful, backtested rule under a named human identity.

    The row lock makes approval atomic with the status check. Merely having a backtest document
    is insufficient: its recorded verdict must be ``helpful``. Agents and scheduled jobs have no
    caller for this function; the CLI requires the human name explicitly.
    """
    actor = _named_actor(approved_by)
    audit_reason = _required_reason(reason)
    at = now or datetime.now(UTC)
    cur.execute(
        """SELECT status,backtest FROM wnba.analyst_rules
           WHERE rule_id=%s FOR UPDATE""",
        (rule_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown analyst rule {rule_id!r}")
    if str(row["status"]) != "backtested":
        raise ValueError(f"rule {rule_id!r} must be backtested before approval")
    backtest = dict(row["backtest"] or {})
    if backtest.get("verdict") != "helpful":
        raise ValueError(f"rule {rule_id!r} does not have a helpful backtest verdict")
    cur.execute(
        """UPDATE wnba.analyst_rules
           SET status='active',approved_by=%s,approved_at=%s,approval_reason=%s
           WHERE rule_id=%s AND status='backtested'""",
        (actor, at, audit_reason, rule_id),
    )
    return RuleApproval(rule_id=rule_id, status="active", actor=actor, changed_at=at)


def approve_rule(
    rule_id: str,
    *,
    approved_by: str,
    reason: str,
    now: datetime | None = None,
) -> RuleApproval:
    with connect() as conn, conn.cursor() as cur:
        return approve_rule_with_cursor(
            cur, rule_id, approved_by=approved_by, reason=reason, now=now
        )


def retire_rule(
    rule_id: str,
    *,
    retired_by: str,
    reason: str,
    now: datetime | None = None,
) -> RuleApproval:
    """Fail closed by retiring an active rule while preserving its full history."""
    actor = _named_actor(retired_by)
    audit_reason = _required_reason(reason)
    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE wnba.analyst_rules
               SET status='retired',retired_at=%s,retired_by=%s,retirement_reason=%s
               WHERE rule_id=%s AND status='active'
               RETURNING rule_id""",
            (at, actor, audit_reason, rule_id),
        )
        if cur.fetchone() is None:
            raise ValueError(f"rule {rule_id!r} is not active")
    return RuleApproval(rule_id=rule_id, status="retired", actor=actor, changed_at=at)


@dataclass(frozen=True)
class RuleLifecycleBatch:
    rules_considered: int
    backtested: int
    rejected: int
    inconclusive: int
    episodes: int

    @property
    def awaiting_approval(self) -> int:
        """Rules that now have supporting evidence and need a human to look at them."""
        return self.backtested


def load_settled_episodes(cur: Any) -> list[SettledEpisode]:
    """Every scoreable settled episode, restated as facts a rule can be tested against.

    The fact vocabulary is the same closed set the live pipeline hands the engine, so a rule
    cannot be backtested on information it would not have at decision time. That is not a
    convenience -- a rule validated against a fact the forecaster never populates would look
    excellent here and never fire in production.
    """
    cur.execute(
        """SELECT d.episode_id,d.player_id,d.game_id,d.prop_type,d.side,d.line,d.source,
                  d.predicted_probability,d.confidence,d.model_disagreement,
                  d.data_quality_score,d.projected_minutes,d.forecast_timestamp,
                  o.hit,
                  f.probability_over,
                  fs.features
           FROM wnba.decision_episodes d
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           LEFT JOIN wnba.stat_forecasts f
             ON f.quote_id=d.quote_id AND f.model_run_id=d.model_run_id
           LEFT JOIN wnba.feature_snapshots fs
             ON fs.feature_snapshot_id=d.feature_snapshot_id
           WHERE NOT o.was_voided AND NOT o.was_push
           ORDER BY d.forecast_timestamp"""
    )

    episodes: list[SettledEpisode] = []
    for row in cur.fetchall():
        features = dict(row["features"] or {})
        designation = str(features.get("injury_designation", "available"))
        effect_count = int(features.get("teammate_effect_count", 0) or 0)
        facts = build_facts(
            predicted_probability=float(str(row["predicted_probability"])),
            confidence=float(str(row["confidence"])),
            model_disagreement=float(str(row["model_disagreement"])),
            data_quality_score=float(str(row["data_quality_score"])),
            projected_minutes=float(str(row["projected_minutes"])),
            line=float(str(row["line"])),
            prop_type=str(row["prop_type"]),
            source=str(row["source"]),
            side=str(row["side"]),
            injury_designation=designation,
            teammate_effect_count=effect_count,
            minutes_std=_optional_float(features.get("minutes_std")),
            start_probability=_optional_float(features.get("start_probability")),
        )
        episodes.append(
            SettledEpisode(
                player_id=str(row["player_id"]),
                game_id=str(row["game_id"]),
                prop_type=str(row["prop_type"]),
                facts=facts,
                probability=float(str(row["predicted_probability"])),
                hit=bool(row["hit"]),
                forecast_timestamp=row["forecast_timestamp"],
            )
        )
    return episodes


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _record(cur: Any, rule: Rule, result: RuleBacktest, *, status: str, at: datetime) -> None:
    cur.execute(
        """UPDATE wnba.analyst_rules
           SET backtest=%s,status=%s
           WHERE rule_id=%s AND status='proposed'""",
        (Jsonb({**result.to_payload(), "backtested_at": at.isoformat()}), status, rule.rule_id),
    )


def run_rule_backtests(*, now: datetime | None = None) -> RuleLifecycleBatch:
    """Backtest every proposed rule.

    Advances a rule to ``backtested`` or ``rejected``. Never to ``active``.
    """
    at = now or datetime.now(UTC)
    considered = backtested = rejected = inconclusive = 0
    breakeven = default_breakeven()

    with connect() as conn, conn.cursor() as cur:
        episodes = load_settled_episodes(cur)

        # Only rules still awaiting evidence. Re-running a backtest over an already-active rule
        # would be a live model change disguised as a maintenance job.
        proposed = [rule for rule in load_rules(cur) if rule.status is RuleStatus.PROPOSED]
        for rule in proposed:
            considered += 1
            result = backtest_rule(rule, episodes, breakeven=breakeven)
            if result.verdict == "helpful":
                _record(cur, rule, result, status="backtested", at=at)
                backtested += 1
            elif result.verdict == "harmful":
                _record(cur, rule, result, status="rejected", at=at)
                rejected += 1
            else:
                # Evidence is stored but the status does not move: the rule is neither ready to
                # be argued for nor demonstrated to be wrong, and it should stay visible as
                # something awaiting more settled games.
                cur.execute(
                    """UPDATE wnba.analyst_rules SET backtest=%s
                       WHERE rule_id=%s AND status='proposed'""",
                    (
                        Jsonb({**result.to_payload(), "backtested_at": at.isoformat()}),
                        rule.rule_id,
                    ),
                )
                inconclusive += 1

    return RuleLifecycleBatch(
        rules_considered=considered,
        backtested=backtested,
        rejected=rejected,
        inconclusive=inconclusive,
        episodes=len(episodes),
    )
