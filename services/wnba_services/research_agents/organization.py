"""Second-round debate, immutable synthesis, and research-memory maintenance."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import fmean, pstdev
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_rules.dsl import Action, ActionKind, Condition, Operator, Rule, RuleStatus
from wnba_store.db import connect

from wnba_services.research_agents.deepseek import AgentForecastDraft, RuleProposalDraft


@dataclass(frozen=True)
class Synthesis:
    disposition: str
    model_probability: float
    advisory_probability: float | None
    disagreement: float
    summary: str
    risk_flags: tuple[str, ...]
    policy_reasons: tuple[str, ...]


def synthesize(
    *,
    model_probability: float,
    round_two: list[AgentForecastDraft],
    audit_blocked: bool,
    data_quality_score: float,
    credibility: dict[str, float] | None = None,
    roles: Sequence[str] = (),
    skeptic_fragility: str | None = None,
    skeptic_flags: Sequence[str] = (),
) -> Synthesis:
    """Combine research metadata without ever replacing the statistical probability.

    Two changes from the plain average this used to be.

    **The consensus is credibility-weighted.** ``agent_credibility`` has been computed per role
    for as long as the table has existed and influenced nothing, which meant a role measured to
    add no information counted exactly as much as one measured to add a lot. Weights are
    floored so a poorly-scored agent is quieted rather than silenced, and a role with no
    measurement weighs the same as its peers.

    **Dispersion stays unweighted.** It is a measure of how divided the desk is, and weighting it
    would let a single trusted agent talk the desk into looking united when it is not. The
    caution gate reads disagreement, so this is the number least worth flattering.
    """
    probabilities = [item.advisory_probability for item in round_two]
    weights = _consensus_weights(roles, credibility, len(round_two))
    advisory = _weighted_mean(probabilities, weights) if probabilities else None
    disagreement = pstdev(probabilities) if len(probabilities) > 1 else 0.0
    risks = tuple(
        dict.fromkeys(
            [flag for item in round_two for flag in item.risk_flags] + list(skeptic_flags)
        )
    )
    reasons: list[str] = []
    if audit_blocked:
        reasons.append("blocking data audit")
    if data_quality_score < 0.75:
        reasons.append("data quality below research policy")
    if disagreement >= 0.12:
        reasons.append("independent agents remain materially divided")
    if skeptic_fragility == "high":
        reasons.append("skeptic rates the forecast highly fragile")
    if audit_blocked or data_quality_score < 0.60:
        disposition = "block"
    elif not round_two:
        disposition = "insufficient"
    elif (
        disagreement >= 0.12
        or skeptic_fragility == "high"
        or (advisory is not None and abs(advisory - model_probability) >= 0.12)
    ):
        # The skeptic cannot move the probability -- it has none to move it with -- but a
        # high-fragility review is exactly the signal 'caution' exists to carry.
        disposition = "caution"
    else:
        disposition = "support"
    summary = (
        f"Statistical model remains {model_probability:.3f}; advisory research consensus is "
        f"{advisory:.3f}."
        if advisory is not None
        else (
            f"Statistical model remains {model_probability:.3f}; "
            "no advisory consensus was available."
        )
    )
    return Synthesis(
        disposition, model_probability, advisory, disagreement, summary, risks, tuple(reasons)
    )


# A role measured to be useless still gets a fifth of a voice. Zero would be a silent removal of
# an agent by a scheduled job, which is the kind of change the rule lifecycle insists a human
# makes; this is a volume knob, not a delete key.
MINIMUM_CONSENSUS_WEIGHT = 0.2


def _consensus_weights(
    roles: Sequence[str], credibility: dict[str, float] | None, count: int
) -> list[float]:
    """One weight per round-two view, aligned to ``roles`` where they were supplied."""
    if not credibility or len(roles) != count:
        return [1.0] * count
    return [max(MINIMUM_CONSENSUS_WEIGHT, credibility.get(role, 1.0)) for role in roles]


def _weighted_mean(values: Sequence[float], weights: Sequence[float]) -> float:
    total = math.fsum(weights)
    if total <= 0 or len(values) != len(weights):
        return fmean(values)
    return math.fsum(value * weight for value, weight in zip(values, weights, strict=True)) / total


def persist_synthesis(cur: Any, run_id: UUID, result: Synthesis, *, at: datetime) -> UUID:
    synthesis_id = uuid4()
    cur.execute(
        """INSERT INTO wnba.decision_syntheses
           (synthesis_id,research_run_id,created_at,disposition,model_probability,
            advisory_probability,disagreement,summary,risk_flags,policy_reasons)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            synthesis_id,
            run_id,
            at,
            result.disposition,
            result.model_probability,
            result.advisory_probability,
            result.disagreement,
            result.summary,
            Jsonb(list(result.risk_flags)),
            Jsonb(list(result.policy_reasons)),
        ),
    )
    return synthesis_id


def validate_rule_proposal(draft: RuleProposalDraft) -> Rule:
    """Reparse every model token through the closed DSL; arbitrary code has nowhere to go."""
    conditions = [
        Condition(
            field=str(raw["field"]),
            operator=Operator(str(raw["operator"])),
            value=raw["value"],
        )
        for raw in draft.conditions
    ]
    action = Action(
        kind=ActionKind(str(draft.action["kind"])),
        magnitude=float(draft.action.get("magnitude", 0.0)),
        note=str(draft.action.get("note", "")),
    )
    return Rule(
        rule_id=draft.rule_id,
        title=draft.title,
        rationale=draft.rationale,
        conditions=conditions,
        combinator=draft.combinator,  # type: ignore[arg-type]
        action=action,
        status=RuleStatus.PROPOSED,
        proposed_by="deepseek",
        evidence_ids=[str(item) for item in draft.evidence_ids],
    )


def persist_rule_proposal(
    cur: Any,
    draft: RuleProposalDraft,
    *,
    at: datetime,
    ttl_days: int = 30,
) -> bool:
    rule = validate_rule_proposal(draft)
    cur.execute(
        """INSERT INTO wnba.analyst_rules
           (rule_id,title,rationale,definition,status,priority,proposed_by,proposed_at,
            evidence_ids,mechanism,confounders,expires_at,withdrawal_criteria)
           VALUES (%s,%s,%s,%s,'proposed',50,'deepseek',%s,%s,%s,%s,%s,%s)
           ON CONFLICT (rule_id) DO NOTHING RETURNING rule_id""",
        (
            rule.rule_id,
            rule.title,
            rule.rationale,
            Jsonb(
                {
                    "conditions": [
                        condition.model_dump(mode="json") for condition in rule.conditions
                    ],
                    "combinator": rule.combinator,
                    "action": rule.action.model_dump(mode="json"),
                }
            ),
            at,
            draft.evidence_ids,
            draft.mechanism,
            draft.confounders,
            at + timedelta(days=ttl_days),
            draft.withdrawal_criteria,
        ),
    )
    return cur.fetchone() is not None


@dataclass(frozen=True)
class MaintenanceBatch:
    expired_claims: int
    evidence_rankings: int
    source_scores: int
    credibility_scores: int


def refresh_research_memory(*, now: datetime | None = None) -> MaintenanceBatch:
    """Expire claims and recompute conservative feedback-derived trust scores."""
    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE wnba.research_claims SET status='expired'
               WHERE expires_at<=%s AND status NOT IN ('expired','rejected')""",
            (at,),
        )
        expired = cur.rowcount
        cur.execute(
            """INSERT INTO wnba.evidence_rankings(evidence_id,score,interactions,calculated_at)
               SELECT e.evidence_id,coalesce(sum(i.weight),0)::float,
                      count(i.interaction_id)::int,%s
               FROM wnba.evidence e LEFT JOIN wnba.evidence_interactions i USING(evidence_id)
               GROUP BY e.evidence_id
               ON CONFLICT(evidence_id) DO UPDATE SET score=excluded.score,
                 interactions=excluded.interactions,calculated_at=excluded.calculated_at""",
            (at,),
        )
        rankings = cur.rowcount
        cur.execute(
            """INSERT INTO wnba.source_reliability
               (source,domain,sample_size,agreement_rate,timeliness_score,reliability,calculated_at)
               SELECT source,'market',count(*)::int,NULL,
                      greatest(0,least(1,1-avg(extract(epoch from (system_from-valid_from)))/7200)),
                      greatest(0.25,least(0.95,
                        1-avg(extract(epoch from (system_from-valid_from)))/14400)),%s
               FROM wnba.prop_quotes GROUP BY source""",
            (at,),
        )
        sources = cur.rowcount
        # Brier and skill against the model, per role *and per round*.
        #
        # The previous version scored mean absolute error, which pays a hedging agent to answer
        # 0.5 forever: that agent books a 0.5 MAE and a 0.5 credibility, comfortably ahead of a
        # bold agent that is right four times in five. Brier is proper -- it cannot be gamed by
        # shading toward the middle -- and `skill_vs_model` asks the question an advisory seat
        # exists to answer: did this agent beat the number it was advising on? An agent that
        # merely restates the model scores zero skill however accurate it is, which is the
        # correct verdict on a seat that adds nothing.
        #
        # Splitting by round is what makes the blind-seat control readable: round one is
        # independent, round two has seen its peers, and averaging them together would hide
        # whether the debate helps.
        cur.execute(
            """INSERT INTO wnba.agent_credibility
               (agent_role,domain,sample_size,calibration,evidence_accuracy,credibility,
                calculated_at,brier,skill_vs_model,round)
               SELECT af.agent_role,'player_prop',count(*)::int,
                      avg(abs(af.advisory_probability-(o.hit::int))),
                      avg(CASE WHEN cardinality(af.evidence_ids)>0 THEN 1.0 ELSE 0.0 END),
                      greatest(0.25,least(0.95,
                        0.5+greatest(-0.5,least(0.5,
                          CASE WHEN avg(power(d.predicted_probability-(o.hit::int),2))>0
                               THEN 1-avg(power(af.advisory_probability-(o.hit::int),2))
                                    /avg(power(d.predicted_probability-(o.hit::int),2))
                               ELSE 0 END)))),
                      %s,
                      avg(power(af.advisory_probability-(o.hit::int),2)),
                      CASE WHEN avg(power(d.predicted_probability-(o.hit::int),2))>0
                           THEN 1-avg(power(af.advisory_probability-(o.hit::int),2))
                                /avg(power(d.predicted_probability-(o.hit::int),2))
                           ELSE NULL END,
                      af.round
               FROM wnba.agent_forecasts af
               JOIN wnba.research_runs rr USING(research_run_id)
               JOIN wnba.stat_forecasts sf USING(projection_id)
               JOIN wnba.decision_episodes d
                 ON d.quote_id=sf.quote_id AND d.model_run_id=sf.model_run_id
               JOIN wnba.episode_outcomes o USING(episode_id)
               WHERE NOT o.was_voided AND NOT o.was_push
               GROUP BY af.agent_role,af.round""",
            (at,),
        )
        credibility = cur.rowcount
    return MaintenanceBatch(expired, rankings, sources, credibility)


def finite_probability(value: float) -> bool:
    return math.isfinite(value) and 0 <= value <= 1
