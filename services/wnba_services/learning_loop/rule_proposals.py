"""Turning repeated, measured failures into candidate rules and testable hypotheses.

The learning loop could see its failures -- error attributions were being written, and repeated
categories were already producing research proposals. What it could not do was turn any of that
into something the forecast would ever act on. The proposals were prose. The rules table was
empty. Nothing connected one to the other.

This module is that connection, and it is deliberately the least clever part of the loop.

**Candidate rules come from a curated catalogue, not from free generation.** Each entry pairs an
error category the system actually measures with a rule expressed in the closed vocabulary, plus
the rationale a reviewer needs. A language model can extend this catalogue through the same
proposal path -- and the DeepSeek research roles are the obvious author for new entries -- but
nothing here needs one, and nothing here would trust one more than it trusts the backtest. A rule
proposed by an agent and a rule proposed by a template are subject to identical evidence.

**Proposal is not adoption.** Everything written here lands as ``proposed``: no backtest, no
approver, and therefore -- by the schema's own constraints -- unable to affect a single forecast.
It becomes ``backtested`` only if the settled record supports it, and ``active`` only when a human
puts their name to it.

**Hypotheses carry a mechanism, not just a correlation.** The table requires a causal statement,
a mechanism and a list of confounders, which is the difference between "high-disagreement
forecasts do worse" and a claim somebody can argue with and design against.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from pydantic import ValidationError
from wnba_rules.dsl import RuleParseError
from wnba_store.db import connect

from wnba_services.forecasting.rules import parse_rule_definition
from wnba_services.research_agents.advisor import Advisor, advisory_summary

__all__ = [
    "CANDIDATE_RULES",
    "HYPOTHESIS_TEMPLATES",
    "HypothesisTemplate",
    "ProposalBatch",
    "RuleCandidate",
    "propose_from_measured_errors",
]

# Repeated failures needed before a category is worth proposing anything about. Below this the
# pattern is a run of bad luck with a name.
MINIMUM_FAILURES = 12


@dataclass(frozen=True)
class RuleCandidate:
    """A rule the system is prepared to argue for, in the form the DSL will accept."""

    rule_id: str
    title: str
    rationale: str
    conditions: list[dict[str, Any]]
    action: dict[str, Any]
    priority: int = 50
    combinator: str = "all"

    def definition(self) -> dict[str, Any]:
        return {
            "conditions": self.conditions,
            "combinator": self.combinator,
            "action": self.action,
        }


# Keyed by the `primary_error` values `classify_error` produces. Every action is a restriction,
# because the language has no other kind -- a proposal that wanted to raise a probability could
# not be expressed here even if the evidence appeared to support it.
CANDIDATE_RULES: dict[str, tuple[RuleCandidate, ...]] = {
    "minutes_projection": (
        RuleCandidate(
            rule_id="shrink_when_minutes_are_uncertain",
            title="Shrink when the minutes projection is itself uncertain",
            rationale=(
                "Repeated minutes-projection errors mean the forecast's largest input was wrong, "
                "and a stat forecast cannot be more reliable than the minutes it is multiplied "
                "by. Where the role model already says it is unsure, the probability should "
                "reflect that rather than inheriting the confidence of a point estimate."
            ),
            conditions=[{"field": "minutes_std", "operator": "gte", "value": 7.0}],
            action={
                "kind": "shrink_toward_even",
                "magnitude": 0.25,
                "note": "minutes uncertainty above one standard rotation change",
            },
            priority=70,
        ),
        RuleCandidate(
            rule_id="block_when_role_is_unresolved",
            title="Refuse a recommendation when the starter question is open",
            rationale=(
                "A player whose start probability sits near a coin flip has bimodal minutes, and "
                "the difference between the two regimes usually swamps the edge being claimed. "
                "Repeated minutes errors concentrated here are not a modelling problem to tune "
                "but a situation to decline."
            ),
            conditions=[
                {"field": "start_probability", "operator": "gte", "value": 0.35},
                {"field": "start_probability", "operator": "lte", "value": 0.65},
            ],
            action={"kind": "block", "note": "rotation status unresolved at forecast time"},
            priority=80,
        ),
    ),
    "rate_or_efficiency": (
        RuleCandidate(
            rule_id="shrink_when_components_disagree",
            title="Shrink when the components disagree about the rate",
            rationale=(
                "Repeated rate errors alongside wide component spread is the signature of a "
                "forecast whose confidence comes from pooling rather than from evidence. When "
                "the components cannot agree, the ensemble's sharpness is an artefact of the "
                "pool and should be given back."
            ),
            conditions=[{"field": "model_disagreement", "operator": "gt", "value": 0.10}],
            action={
                "kind": "shrink_toward_even",
                "magnitude": 0.30,
                "note": "component disagreement above the candidate gate",
            },
            priority=60,
        ),
    ),
    "market_movement": (
        RuleCandidate(
            rule_id="require_evidence_on_stale_quotes",
            title="Demand a live claim when the quote has aged",
            rationale=(
                "Errors attributed to market movement are usually errors of timing: the line "
                "moved on news the forecast had not seen. An aged quote is exactly the case "
                "where a cited, unexpired research claim is worth insisting on before acting."
            ),
            conditions=[{"field": "quote_age_seconds", "operator": "gte", "value": 5400}],
            action={"kind": "require_evidence", "note": "quote older than ninety minutes"},
            priority=65,
        ),
    ),
    "data_quality": (
        RuleCandidate(
            rule_id="block_on_thin_evidence",
            title="Decline when the evidence checklist is incomplete",
            rationale=(
                "A low data-quality score means a feature the model expects was missing, not "
                "that the situation was unusual. Acting on a forecast built from partial inputs "
                "converts an ingestion gap into a wager."
            ),
            conditions=[{"field": "data_quality_score", "operator": "lt", "value": 0.60}],
            action={"kind": "block", "note": "required features missing at forecast time"},
            priority=90,
        ),
    ),
}


@dataclass(frozen=True)
class HypothesisTemplate:
    """A causal claim with a mechanism -- arguable, rather than a bare correlation."""

    name: str
    causal_statement: str
    mechanism: str
    expected_direction: str
    required_features: list[str]
    confounders: list[str]


HYPOTHESIS_TEMPLATES: dict[str, HypothesisTemplate] = {
    "minutes_projection": HypothesisTemplate(
        name="Rotation uncertainty drives minutes error",
        causal_statement=(
            "When a player's role is unresolved before tipoff, projected minutes carry a "
            "bimodal error the point estimate hides, and the stat forecast inherits it."
        ),
        mechanism=(
            "Minutes enter the forecast multiplicatively, so an error in the minutes projection "
            "scales the whole distribution rather than shifting it. A starter/bench mixture has "
            "two modes; a point estimate lands between them and describes neither."
        ),
        expected_direction="conditional",
        required_features=["minutes_std", "start_probability", "closing_lineup_probability"],
        confounders=[
            "injury designation changing after the forecast",
            "blowouts shortening rotations independently of role",
            "back-to-back rest management",
        ],
    ),
    "rate_or_efficiency": HypothesisTemplate(
        name="Component disagreement predicts rate error",
        causal_statement=(
            "Forecasts where the ensemble components disagree are systematically overconfident, "
            "because pooling produces a sharp answer from inputs that do not agree."
        ),
        mechanism=(
            "Each component conditions on a different slice of history. Wide spread means those "
            "slices tell different stories, which is evidence about the player being hard to "
            "forecast rather than evidence about which component is right."
        ),
        expected_direction="increase",
        required_features=["model_disagreement", "dispersion"],
        confounders=[
            "small history samples inflating spread mechanically",
            "combination markets disagreeing more by construction",
        ],
    ),
    "market_movement": HypothesisTemplate(
        name="Quote staleness costs closing-line value",
        causal_statement=(
            "Forecasts made against an aged quote lose to the closing line because the market "
            "has already priced news the forecast had not seen."
        ),
        mechanism=(
            "Operators move lines on lineup and availability news within minutes. A forecast "
            "priced against a stale line is competing against a strictly better-informed price."
        ),
        expected_direction="increase",
        required_features=["quote_age_seconds"],
        confounders=[
            "quiet markets whose lines do not move regardless of news",
            "promotional or boosted lines that move for commercial reasons",
        ],
    ),
}


@dataclass(frozen=True)
class ProposalBatch:
    categories_reviewed: int
    hypotheses_created: int
    rules_proposed: int
    episodes_linked: int
    # Of `rules_proposed`, how many DeepSeek wrote. Reported separately so the catalogue's
    # contribution and the model's stay distinguishable in the weekly console line -- they are
    # subject to identical evidence, which is exactly why the difference is worth measuring.
    model_authored_rules: int = 0
    advisories: dict[str, int] = field(default_factory=dict)


def _existing(cur: Any, table: str, column: str) -> set[str]:
    cur.execute(f"SELECT {column} FROM wnba.{table}")
    return {str(row[column]) for row in cur.fetchall()}


def propose_from_measured_errors(
    *, now: datetime | None = None, advisor: Advisor | None = None
) -> ProposalBatch:
    """Create hypotheses and candidate rules from error categories that keep recurring.

    Everything created here is inert. A hypothesis is a claim awaiting evidence and a proposed
    rule cannot fire, in shadow or otherwise, until a human has read its backtest and signed for
    it. The point is to make the next step *possible*, not to take it.
    """
    at = now or datetime.now(UTC)
    reviewed = hypotheses = rules = links = model_rules = 0

    with connect() as conn, conn.cursor() as cur:
        assistant = advisor or Advisor(cur)
        cur.execute(
            """SELECT a.primary_error,
                      count(*) AS failures,
                      count(DISTINCT (d.player_id,d.game_id,d.prop_type)) AS markets,
                      array_agg(a.episode_id ORDER BY a.attributed_at DESC) AS episodes,
                      array_agg(DISTINCT d.prop_type) AS markets_affected
               FROM wnba.error_attributions a
               JOIN wnba.decision_episodes d ON d.episode_id=a.episode_id
               WHERE a.avoidable
               GROUP BY a.primary_error
               HAVING count(DISTINCT (d.player_id,d.game_id,d.prop_type))>=%s
               ORDER BY count(*) DESC""",
            (MINIMUM_FAILURES,),
        )
        categories = cur.fetchall()

        known_hypotheses = _existing(cur, "hypotheses", "name")
        known_rules = _existing(cur, "analyst_rules", "rule_id")

        for row in categories:
            reviewed += 1
            category = str(row["primary_error"])
            # Postgres array aggregates arrive typed as `object`; refusing anything that is not
            # actually a list keeps a driver or schema change from silently iterating a string
            # into single characters.
            raw_episodes = row["episodes"]
            raw_markets = row["markets_affected"]
            if not isinstance(raw_episodes, list) or not isinstance(raw_markets, list):
                raise ValueError("error-category aggregates are not arrays")
            episodes = [UUID(str(value)) for value in raw_episodes][:50]
            affected = [str(value) for value in raw_markets]

            hypothesis_id: UUID | None = None
            template = HYPOTHESIS_TEMPLATES.get(category)
            if template is not None and template.name not in known_hypotheses:
                hypothesis_id = uuid4()
                cur.execute(
                    """INSERT INTO wnba.hypotheses
                       (hypothesis_id,name,causal_statement,mechanism,target_markets,
                        expected_direction,required_features,confounders,status,confidence,
                        created_by,created_at,error_category)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'proposed',0.5,'research_director',%s,%s)""",
                    (
                        hypothesis_id,
                        template.name,
                        template.causal_statement,
                        template.mechanism,
                        affected,
                        template.expected_direction,
                        template.required_features,
                        template.confounders,
                        at,
                        # The claim this hypothesis is making, recorded so the weekly review can
                        # test it without reverse-engineering it from the name.
                        category,
                    ),
                )
                hypotheses += 1
                known_hypotheses.add(template.name)

                # The episodes that motivated the hypothesis are its supporting evidence, and
                # linking them is what lets a later review ask whether they still support it.
                for episode_id in episodes:
                    cur.execute(
                        """INSERT INTO wnba.hypothesis_episodes
                           (hypothesis_id,episode_id,relationship)
                           VALUES (%s,%s,'supporting') ON CONFLICT DO NOTHING""",
                        (hypothesis_id, episode_id),
                    )
                    links += cur.rowcount

            catalogue_added = 0
            for candidate in CANDIDATE_RULES.get(category, ()):
                if candidate.rule_id in known_rules:
                    continue
                cur.execute(
                    """INSERT INTO wnba.analyst_rules
                       (rule_id,title,rationale,definition,status,priority,proposed_by,proposed_at)
                       VALUES (%s,%s,%s,%s,'proposed',%s,'research_director',%s)""",
                    (
                        candidate.rule_id,
                        candidate.title,
                        candidate.rationale,
                        Jsonb(candidate.definition()),
                        candidate.priority,
                        at,
                    ),
                )
                known_rules.add(candidate.rule_id)
                rules += 1
                catalogue_added += 1

            # The catalogue is finite and this category has exhausted it, but the failures are
            # still recurring. This is the point the module's own docstring anticipated: a model
            # may extend the catalogue through the identical proposal path. What it writes lands
            # as `proposed` with no backtest and no approver, so it cannot touch a forecast, and
            # `run_rule_backtests` will judge it on exactly the evidence a template rule faces.
            if catalogue_added == 0 and _propose_with_model(
                cur,
                advisor=assistant,
                category=category,
                failures=int(str(row["failures"])),
                markets=int(str(row["markets"])),
                affected=affected,
                episodes=episodes,
                known_rules=known_rules,
                at=at,
            ):
                rules += 1
                model_rules += 1

        advisories = advisory_summary(assistant.outcomes)

    return ProposalBatch(
        categories_reviewed=reviewed,
        hypotheses_created=hypotheses,
        rules_proposed=rules,
        episodes_linked=links,
        model_authored_rules=model_rules,
        advisories=advisories,
    )


def _propose_with_model(
    cur: Any,
    *,
    advisor: Advisor,
    category: str,
    failures: int,
    markets: int,
    affected: list[str],
    episodes: list[UUID],
    known_rules: set[str],
    at: datetime,
) -> bool:
    """Ask DeepSeek for one new rule in the closed DSL. Returns whether one was stored.

    Three gates stand between the model and the table, and a proposal has to clear all of them:
    the strict ``RuleProposalDraft`` schema, the evidence check inside the client (a cited
    episode id that was not supplied is a hard error), and :func:`parse_rule_definition`, which
    re-validates the conditions and action against the same vocabulary the engine executes. A
    rule that cannot be expressed in that language cannot be stored, whatever the model meant.
    """
    evidence = {
        episode_id: (
            f"Settled WNBA prop decision attributed to a recurring {category} failure. "
            f"Markets affected: {', '.join(affected)}."
        )
        for episode_id in episodes[:12]
    }
    if not evidence:
        return False

    measured_failure = (
        f"The category '{category}' has produced {failures} avoidable errors across {markets} "
        f"independent markets in {', '.join(affected)}, and every rule the catalogue holds for "
        "it has already been proposed. Propose one further conservative rule that would have "
        "declined or softened these decisions."
    )
    try:
        draft, _, _ = advisor.client.propose_rule(
            measured_failure=measured_failure, evidence=evidence
        )
    except Exception as error:  # provider failure is a fallback, never a job failure
        advisor.reject(
            task="rule_authoring",
            subject=category,
            reason=f"{type(error).__name__}: {error}",
            now=at,
        )
        return False

    if draft.rule_id in known_rules:
        advisor.reject(
            task="rule_authoring",
            subject=category,
            reason=f"proposed rule_id {draft.rule_id!r} already exists",
            now=at,
        )
        return False

    definition = {
        "conditions": draft.conditions,
        "combinator": draft.combinator,
        "action": draft.action,
    }
    try:
        parse_rule_definition(
            {
                **definition,
                "rule_id": draft.rule_id,
                "title": draft.title,
                "rationale": draft.rationale,
                "status": "proposed",
                "priority": 50,
                "proposed_by": "deepseek",
                "approved_by": None,
            }
        )
    except (RuleParseError, ValidationError, ValueError, KeyError, TypeError) as error:
        advisor.reject(
            task="rule_authoring",
            subject=category,
            reason=f"definition does not parse against the closed vocabulary: {error}",
            now=at,
        )
        return False

    cur.execute(
        """INSERT INTO wnba.analyst_rules
           (rule_id,title,rationale,definition,status,priority,proposed_by,proposed_at,
            authored_by_model)
           VALUES (%s,%s,%s,%s,'proposed',50,'deepseek',%s,true)""",
        (draft.rule_id, draft.title, draft.rationale, Jsonb(definition), at),
    )
    known_rules.add(draft.rule_id)
    return True
