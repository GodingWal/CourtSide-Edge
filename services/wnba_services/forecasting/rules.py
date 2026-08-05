"""Putting the rules engine in the path it was built for.

``packages/wnba_rules`` shipped a closed-vocabulary rule language, a shadow-firing engine, an
approval invariant that no rule can go live without a named human, and a pair of database tables
to record every firing. It had no importer. Nothing in ``services`` or ``apps`` referenced it, so
the analyst knowledge it was designed to carry was codified, reviewed, stored -- and never
consulted by a forecast.

This module is the missing edge. It reads the active and shadow rules, assembles the facts the
closed vocabulary permits, applies them between the calibrated probability and the candidate
gate, and writes every firing -- shadow ones included -- back to ``wnba.rule_firings``.

The safety asymmetry is inherited rather than re-implemented: the language has no verb that can
raise a probability, so nothing here can either. The worst a rule can do to a forecast is talk it
out of one.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import ValidationError
from wnba_rules.dsl import (
    RULE_FIELDS,
    ActionKind,
    Operator,
    Rule,
    RuleParseError,
    RuleStatus,
)
from wnba_rules.engine import Firing, RuleOutcome, apply_rules

__all__ = [
    "RULE_FIELDS",
    "active_rule_count",
    "build_facts",
    "evaluate_rules",
    "load_rules",
    "parse_rule_definition",
    "record_firings",
]


class Cursor(Protocol):
    def execute(self, query: str, params: Any = ..., /) -> Any: ...

    def fetchall(self) -> list[Any]: ...


def _coerce_enums(definition: dict[str, Any]) -> dict[str, Any]:
    """Turn the JSONB document's strings into the enum members the DSL requires.

    ``Rule`` is a strict Pydantic model, so ``"gt"`` is not accepted where an ``Operator`` is
    expected -- and strictness here is a feature rather than an inconvenience: it is what stops a
    typo'd operator from being coerced into something plausible. The cost is that the boundary
    between JSONB and the model has to do the conversion explicitly, which is this function. An
    unrecognised value raises ``ValueError``, and the caller drops the rule.
    """
    coerced = dict(definition)
    coerced["status"] = RuleStatus(str(coerced["status"]))

    conditions = []
    for raw in coerced.get("conditions") or []:
        condition = dict(raw)
        condition["operator"] = Operator(str(condition["operator"]))
        conditions.append(condition)
    coerced["conditions"] = conditions

    action = dict(coerced.get("action") or {})
    if "kind" in action:
        action["kind"] = ActionKind(str(action["kind"]))
    coerced["action"] = action
    return coerced


def parse_rule_definition(definition: dict[str, Any]) -> Rule:
    """Validate one rule document against the closed vocabulary, raising if it does not fit.

    ``load_rules`` swallows a parse failure because a bad row must not take down a forecast. A
    *proposal* is the opposite case: an unparseable definition should never reach the table in the
    first place, and this is the gate that keeps a model-authored rule out. Anything that survives
    here is expressible in the same language a human's proposal is, with the same absence of any
    verb that could raise a probability.
    """
    return Rule.model_validate(_coerce_enums(dict(definition)))


def load_rules(cur: Cursor, *, include_shadow: bool = True) -> list[Rule]:
    """Load rules the engine may run. Malformed definitions are skipped, never guessed at.

    A row whose stored definition no longer parses against the current vocabulary is a
    governance problem to be surfaced, not a forecast-time decision to be improvised. Dropping it
    fails safe -- the rule simply does not fire -- which is the correct direction for a language
    in which every action is a restriction.
    """
    statuses = ["active"]
    if include_shadow:
        statuses += ["proposed", "backtested"]
    cur.execute(
        """SELECT rule_id,title,rationale,definition,status,priority,proposed_by,approved_by
           FROM wnba.analyst_rules WHERE status=ANY(%s)""",
        (statuses,),
    )

    rules: list[Rule] = []
    for row in cur.fetchall():
        definition = dict(row["definition"])
        definition.update(
            {
                "rule_id": str(row["rule_id"]),
                "title": str(row["title"]),
                "rationale": str(row["rationale"]),
                "status": str(row["status"]),
                "priority": int(row["priority"]),
                "proposed_by": str(row["proposed_by"]),
                "approved_by": None if row["approved_by"] is None else str(row["approved_by"]),
            }
        )
        try:
            rules.append(Rule.model_validate(_coerce_enums(definition)))
        except (RuleParseError, ValidationError, ValueError, KeyError, TypeError):
            continue
    return rules


def build_facts(
    *,
    predicted_probability: float,
    confidence: float,
    model_disagreement: float,
    data_quality_score: float,
    projected_minutes: float,
    line: float,
    prop_type: str,
    source: str,
    side: str,
    injury_designation: str,
    teammate_effect_count: int,
    minutes_std: float | None = None,
    availability_probability: float | None = None,
    start_probability: float | None = None,
    closing_lineup_probability: float | None = None,
    quote_age_seconds: float | None = None,
    book_count: int | None = None,
) -> dict[str, object]:
    """Assemble the fact mapping, dropping anything the pipeline could not actually observe.

    Omitted rather than defaulted. A condition on a missing fact evaluates to ``False`` in the
    DSL, so leaving it out means a rule written for it simply does not fire; supplying a
    stand-in would let that rule fire on a number nobody measured.
    """
    facts: dict[str, object] = {
        "predicted_probability": predicted_probability,
        "confidence": confidence,
        "model_disagreement": model_disagreement,
        "data_quality_score": data_quality_score,
        "projected_minutes": projected_minutes,
        "line": line,
        "prop_type": prop_type,
        "source": source,
        "side": side,
        "injury_designation": injury_designation,
        "teammate_effect_count": teammate_effect_count,
    }
    optional: dict[str, object | None] = {
        "minutes_std": minutes_std,
        "availability_probability": availability_probability,
        "start_probability": start_probability,
        "closing_lineup_probability": closing_lineup_probability,
        "quote_age_seconds": quote_age_seconds,
        "book_count": book_count,
    }
    facts.update({name: value for name, value in optional.items() if value is not None})

    unknown = set(facts) - set(RULE_FIELDS)
    if unknown:
        raise ValueError(f"facts outside the rule vocabulary: {sorted(unknown)}")
    return facts


def evaluate_rules(rules: list[Rule], facts: dict[str, object], probability: float) -> RuleOutcome:
    """Apply every matching rule to one candidate decision.

    Probability for the *selected side*, not for the over: a rule saying "shrink when we are
    over-confident" means over-confident in the direction we are actually leaning.
    """
    return apply_rules(rules, facts, probability, include_shadow=True)


def record_firings(cur: Cursor, episode_id: UUID, firings: list[Firing], *, at: datetime) -> int:
    """Persist every firing, shadow included. A silent adjustment is a bug, not a feature.

    Shadow firings are the whole point of storing them: they are how a proposed rule earns the
    backtest evidence that the ``active_rules_require_a_backtest`` constraint demands before
    anybody may switch it on.
    """
    written = 0
    for firing in firings:
        cur.execute(
            """INSERT INTO wnba.rule_firings
               (firing_id,rule_id,episode_id,fired_at,shadow,action_kind,
                probability_before,probability_after)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid4(),
                firing.rule_id,
                episode_id,
                at,
                firing.shadow,
                firing.kind.value,
                firing.probability_before,
                firing.probability_after,
            ),
        )
        written += 1
    return written


def active_rule_count(rules: list[Rule]) -> int:
    """How many of these rules actually affect output, as opposed to being measured."""
    return sum(1 for rule in rules if rule.status is RuleStatus.ACTIVE)
