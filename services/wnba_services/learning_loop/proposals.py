"""Generate conservative research proposals from repeated measured errors.

This is the panel the loop never fed. `generate_research_proposals` existed and was correct, and
no systemd unit ever ran it -- the weekly unit ran `propose-rules`, a different function writing
different tables, one hyphenated suffix away in the CLI. So the "prioritised research proposals"
panel rendered its empty state from the day it shipped.

Wiring it up exposed the second problem: every proposal carried the same four sentences of
boilerplate as its experiment design, differing only in the category name substituted into the
title. A queue of identical proposals is not a prioritised research agenda, it is a to-do list
with a count.

DeepSeek writes the design now -- what to build, what to measure it against, what would falsify
it, and what the confounders are -- from the actual shape of the failures. Two things it does not
touch: the proposal still lands at status ``proposed`` with no approver, and ``estimated_value``
and ``estimated_failure_reduction`` are still computed from the failure counts rather than
supplied by the model. A prioritisation score a model can set is a prioritisation score a model
can game.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field
from wnba_store.db import connect

from wnba_services.research_agents.advisor import Advisor, advisory_summary

__all__ = ["ProposalBatch", "ProposedExperiment", "generate_research_proposals"]

_FALLBACK_DESIGN = (
    "Create a shadow challenger targeting this error category. Declare Brier "
    "score as the primary metric, run rolling-origin evaluation, and reject "
    "the challenger if any major market subgroup materially degrades."
)


@dataclass(frozen=True)
class ProposalBatch:
    proposed: int
    categories_reviewed: int
    model_authored: int = 0
    advisories: dict[str, int] = field(default_factory=dict)


class ProposedExperiment(BaseModel):
    """A research design specific enough to argue with."""

    model_config = ConfigDict(strict=True, extra="forbid")

    title: Annotated[str, Field(min_length=10, max_length=180)]
    experiment_design: Annotated[str, Field(min_length=80, max_length=1800)]
    # What result would make this line of work wrong. A proposal that cannot say this is a
    # direction, not an experiment.
    falsification_criteria: Annotated[str, Field(min_length=20, max_length=800)]
    confounders: Annotated[
        list[Annotated[str, Field(min_length=3, max_length=200)]], Field(max_length=6)
    ]
    implementation_cost: Annotated[str, Field(pattern=r"^(low|medium|high)$")]


def generate_research_proposals(
    *, now: datetime | None = None, advisor: Advisor | None = None
) -> ProposalBatch:
    """Create testable proposals; never approve or implement them automatically."""
    at = now or datetime.now(UTC)
    proposed = reviewed = model_authored = 0
    with connect() as conn, conn.cursor() as cur:
        assistant = advisor or Advisor(cur)
        cur.execute(
            """SELECT a.primary_error,
                      array_agg(a.episode_id ORDER BY a.attributed_at DESC) episodes,
                      count(*) AS failures,array_agg(DISTINCT d.prop_type) markets,
                      count(*) FILTER (WHERE a.secondary_error IS NOT NULL) AS with_secondary,
                      array_agg(DISTINCT a.secondary_error) FILTER (
                          WHERE a.secondary_error IS NOT NULL) AS secondary_causes
               FROM wnba.error_attributions a
               JOIN wnba.decision_episodes d ON d.episode_id=a.episode_id
               WHERE a.avoidable GROUP BY a.primary_error HAVING count(*)>=5
               ORDER BY count(*) DESC"""
        )
        for row in cur.fetchall():
            reviewed += 1
            category = str(row["primary_error"])
            cur.execute(
                """SELECT 1 FROM wnba.research_proposals WHERE error_category=%s
                   AND status IN ('proposed','approved','testing') LIMIT 1""",
                (category,),
            )
            if cur.fetchone() is not None:
                continue
            raw_episodes = row["episodes"]
            raw_markets = row["markets"]
            if not isinstance(raw_episodes, list) or not isinstance(raw_markets, list):
                raise ValueError("proposal aggregates are not arrays")
            episodes = [UUID(str(value)) for value in raw_episodes[:50]]
            markets = [str(value) for value in raw_markets]
            failures = int(str(row["failures"]))
            raw_secondary = row["secondary_causes"]
            secondary = (
                [str(value) for value in raw_secondary]
                if isinstance(raw_secondary, list)
                else []
            )

            design = _design(
                assistant,
                category=category,
                failures=failures,
                markets=markets,
                secondary_causes=secondary,
                at=at,
            )
            authored = design is not None
            model_authored += authored

            # Prioritisation stays arithmetic. The model describes the work; it does not rank it.
            estimated_reduction = min(0.3, 0.05 + failures / 1000)
            cur.execute(
                """INSERT INTO wnba.research_proposals
                   (proposal_id,title,proposed_at,proposed_by,motivating_episode_ids,
                    estimated_value,estimated_failure_reduction,implementation_cost,
                    affected_markets,experiment_design,error_category,authored_by_model,
                    model_rationale)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    design.title
                    if design
                    else f"Reduce repeated {category.replace('_', ' ')} errors",
                    at,
                    "deepseek" if authored else "research_director",
                    episodes,
                    min(0.9, 0.5 + failures / 500),
                    estimated_reduction,
                    design.implementation_cost if design else "medium",
                    markets,
                    design.experiment_design if design else _FALLBACK_DESIGN,
                    category,
                    authored,
                    _rationale(design) if design else None,
                ),
            )
            proposed += 1
        advisories = advisory_summary(assistant.outcomes)
    return ProposalBatch(proposed, reviewed, model_authored, advisories)


def _rationale(design: ProposedExperiment) -> str:
    confounders = "; ".join(design.confounders) or "none stated"
    return f"Falsified by: {design.falsification_criteria}\nConfounders: {confounders}"


def _design(
    advisor: Advisor,
    *,
    category: str,
    failures: int,
    markets: list[str],
    secondary_causes: list[str],
    at: datetime,
) -> ProposedExperiment | None:
    return advisor.advise(
        task="research_proposal",
        subject=category,
        system=(
            "You are the research director of a WNBA player-prop forecasting system, writing one "
            "experiment proposal for a human review queue. Return JSON only. Propose measurement "
            "and modelling work only: you may not propose placing wagers, changing stake sizing, "
            "or any change that would raise a forecast's confidence automatically. Every "
            "proposal must be falsifiable and must name what would make it wrong. Do not output "
            "a probability for any prop."
        ),
        question={
            "recurring_error_category": category,
            "avoidable_failures_measured": failures,
            "affected_markets": markets,
            "secondary_causes_seen_underneath_this_one": secondary_causes,
            "available_actions": [
                "add or change a forecast component",
                "add a feature to the point-in-time feature store",
                "open a champion/challenger experiment with a new model family",
                "propose an analyst rule over the closed restriction vocabulary",
                "improve data quality or ingestion coverage for a named source",
            ],
            "constraints": [
                "the system is analysis-only and never places a wager",
                "no change may automatically increase a forecast's confidence",
                "promotion of any model or rule requires a named human",
                "evaluation is rolling-origin on independent markets after game clustering",
            ],
        },
        schema=ProposedExperiment,
        now=at,
    )
