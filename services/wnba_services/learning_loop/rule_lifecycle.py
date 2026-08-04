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

__all__ = ["RuleLifecycleBatch", "load_settled_episodes", "run_rule_backtests"]


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
