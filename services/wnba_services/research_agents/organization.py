"""Second-round debate, immutable synthesis, and research-memory maintenance."""

from __future__ import annotations

import math
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
) -> Synthesis:
    """Combine research metadata without ever replacing the statistical probability."""
    probabilities = [item.advisory_probability for item in round_two]
    advisory = fmean(probabilities) if probabilities else None
    disagreement = pstdev(probabilities) if len(probabilities) > 1 else 0.0
    risks = tuple(dict.fromkeys(flag for item in round_two for flag in item.risk_flags))
    reasons: list[str] = []
    if audit_blocked:
        reasons.append("blocking data audit")
    if data_quality_score < 0.75:
        reasons.append("data quality below research policy")
    if disagreement >= 0.12:
        reasons.append("independent agents remain materially divided")
    if audit_blocked or data_quality_score < 0.60:
        disposition = "block"
    elif not round_two:
        disposition = "insufficient"
    elif disagreement >= 0.12 or (
        advisory is not None and abs(advisory - model_probability) >= 0.12
    ):
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
        cur.execute(
            """INSERT INTO wnba.agent_credibility
               (agent_role,domain,sample_size,calibration,evidence_accuracy,credibility,calculated_at)
               SELECT af.agent_role,'player_prop',count(*)::int,
                      avg(abs(af.advisory_probability-(o.hit::int))),
                      avg(CASE WHEN cardinality(af.evidence_ids)>0 THEN 1.0 ELSE 0.0 END),
                      greatest(0.25,least(0.95,1-avg(abs(af.advisory_probability-(o.hit::int))))),%s
               FROM wnba.agent_forecasts af
               JOIN wnba.research_runs rr USING(research_run_id)
               JOIN wnba.stat_forecasts sf USING(projection_id)
               JOIN wnba.decision_episodes d
                 ON d.quote_id=sf.quote_id AND d.model_run_id=sf.model_run_id
               JOIN wnba.episode_outcomes o USING(episode_id)
               WHERE af.round=2 AND NOT o.was_voided AND NOT o.was_push
               GROUP BY af.agent_role""",
            (at,),
        )
        credibility = cur.rowcount
    return MaintenanceBatch(expired, rankings, sources, credibility)


def finite_probability(value: float) -> bool:
    return math.isfinite(value) and 0 <= value <= 1
