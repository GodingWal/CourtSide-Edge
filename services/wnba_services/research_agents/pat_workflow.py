"""Orchestration for a two-round PAT-style research run that degrades instead of collapsing."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb
from wnba_store.db import connect

from wnba_services.research_agents.advisor import Advisor
from wnba_services.research_agents.auditor import audit_projection, persist_audit
from wnba_services.research_agents.coordinator import build_plan, persist_plan, trigger_codes
from wnba_services.research_agents.deepseek import DeepSeekResearchClient, SkepticReview
from wnba_services.research_agents.organization import (
    persist_rule_proposal,
    persist_synthesis,
    synthesize,
)
from wnba_services.research_agents.precedents import retrieve_precedents
from wnba_services.research_agents.workflow import (
    AGENT_QUESTIONS,
    SKEPTIC_ROLE,
    run_projection_research,
)


@dataclass(frozen=True)
class PatBatch:
    research_run_id: UUID
    status: str
    round_one: int
    round_two: int
    precedents: int
    rule_proposed: bool


def _collect(
    advisor: Advisor,
    futures: dict[str, Future[tuple[Any, str, str]]],
    *,
    task: str,
    context: str,
    at: datetime,
) -> dict[str, Any]:
    """Gather agent results, keeping the ones that arrived.

    A round used to be all-or-nothing: the first role to raise took the other four with it, and
    the run was marked failed with nothing recorded. A research desk missing one analyst still
    holds a meeting.
    """
    collected: dict[str, Any] = {}
    for role, future in futures.items():
        result = advisor.attempt(task=task, subject=f"{context} {role}", call=future.result, now=at)
        if result is not None:
            collected[role] = result[0] if isinstance(result, tuple) else result
    return collected


def _blind_role(run_id: UUID, roles: Sequence[str]) -> str:
    """Which seat argues round two without seeing its peers, rotated by run.

    Deterministic in the run id so the choice is reproducible from the stored record, and
    rotating so the control condition does not permanently land on one role -- an agent that is
    always blind is not a control, it is a differently-configured agent.
    """
    return roles[run_id.int % len(roles)] if roles else ""


def _persist_skeptic_review(cur: Any, run_id: UUID, review: SkepticReview, *, at: datetime) -> None:
    """Store the skeptic's failure modes as an analysis with no probability attached.

    It reuses ``agent_analyses`` rather than gaining a table: a conclusion, a confidence and a
    set of risk flags is exactly the shape of that row. What it never becomes is an
    ``agent_forecasts`` row, because that is the table the consensus average reads.
    """
    severity_weight = {"low": 0.25, "moderate": 0.55, "high": 0.85}
    cur.execute(
        """INSERT INTO wnba.agent_analyses
           (analysis_id,research_run_id,agent_role,conclusion,confidence,risk_flags,
            evidence_ids,response_sha256)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            uuid4(),
            run_id,
            SKEPTIC_ROLE,
            review.summary,
            # "Confidence" here reads as confidence in the fragility finding, which is what the
            # skeptic is actually asserting. It is not a probability about the prop and it never
            # reaches a forecast.
            severity_weight.get(review.fragility, 0.5),
            Jsonb(
                [
                    *review.risk_flags,
                    *[f"{mode.severity}: {mode.description}" for mode in review.failure_modes],
                ]
            ),
            sorted(
                {evidence_id for mode in review.failure_modes for evidence_id in mode.evidence_ids},
                key=str,
            ),
            hashlib.sha256(review.model_dump_json().encode()).hexdigest(),
        ),
    )


def _credibility_weights(cur: Any, roles: Sequence[str]) -> dict[str, float]:
    """Latest measured credibility per role, for weighting the consensus.

    Absent measurement, a role weighs the same as every other -- the fallback is deliberate
    equality rather than a default that quietly favours whoever was scored first.
    """
    if not roles:
        return {}
    cur.execute(
        # Round two, because that is the round the consensus is taken over.
        """SELECT DISTINCT ON (agent_role) agent_role,credibility
           FROM wnba.agent_credibility
           WHERE agent_role = ANY(%s) AND domain='player_prop' AND round=2
           ORDER BY agent_role,calculated_at DESC""",
        (list(roles),),
    )
    return {str(row["agent_role"]): float(str(row["credibility"])) for row in cur.fetchall()}


def _track_records(cur: Any, roles: Sequence[str]) -> dict[str, str]:
    """Each role's latest measured track record, phrased for its own prompt.

    Credibility has been computed per role for as long as the table has existed and, until the
    consensus weighting, influenced nothing the agent itself could see. An agent that cannot
    see its own calibration history cannot correct for it. Rows with tiny samples are withheld
    rather than shown: quoting a Brier over four settled views teaches noise, not humility.
    """
    if not roles:
        return {}
    cur.execute(
        """SELECT DISTINCT ON (agent_role) agent_role,sample_size,brier,skill_vs_model
           FROM wnba.agent_credibility
           WHERE agent_role = ANY(%s) AND domain='player_prop' AND round=2
           ORDER BY agent_role,calculated_at DESC""",
        (list(roles),),
    )
    records: dict[str, str] = {}
    for row in cur.fetchall():
        sample = int(str(row["sample_size"]))
        if sample < 5:
            continue
        parts = [f"{sample} settled advisory views"]
        if row["brier"] is not None:
            parts.append(f"Brier {float(str(row['brier'])):.3f}")
        if row["skill_vs_model"] is not None:
            parts.append(f"skill vs model {float(str(row['skill_vs_model'])):+.3f}")
        records[str(row["agent_role"])] = "; ".join(parts)
    return records


def _load_projection(cur: Any, projection_id: UUID) -> dict[str, Any]:
    cur.execute(
        """SELECT f.*,fs.features,q.source,q.system_from AS observed_at,q.locks_at,
                  d.model_disagreement,p.full_name
           FROM wnba.stat_forecasts f
           JOIN wnba.feature_snapshots fs USING(feature_snapshot_id)
           JOIN wnba.prop_quotes q USING(quote_id)
           JOIN wnba.decision_episodes d ON d.quote_id=f.quote_id AND d.model_run_id=f.model_run_id
           JOIN wnba.players p ON p.player_id=f.player_id
           WHERE f.projection_id=%s""",
        (projection_id,),
    )
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown projection {projection_id}")
    return dict(row)


def _run_evidence(cur: Any, run_id: UUID) -> dict[UUID, str]:
    """Every evidence item from the documents this run's analysts drew on.

    Not merely the cited items. The corpus is built as a set of groups -- recent form, line
    movement, the de-vigged market price, precedents -- and an analyst declining to cite one is
    not a reason to withhold it from the agents that forecast next. Restricting to citations
    made the strongest base-rate group invisible whenever round one happened not to mention it.
    """
    cur.execute(
        """SELECT DISTINCT e.evidence_id,e.excerpt FROM wnba.evidence e
           WHERE e.document_id IN (
             SELECT DISTINCT cited.document_id FROM wnba.evidence cited
             JOIN wnba.agent_analyses a ON cited.evidence_id=ANY(a.evidence_ids)
             WHERE a.research_run_id=%s)""",
        (run_id,),
    )
    return {UUID(str(row["evidence_id"])): str(row["excerpt"]) for row in cur.fetchall()}


def run_pat_research(
    projection_id: UUID,
    *,
    client: DeepSeekResearchClient | None = None,
    now: datetime | None = None,
) -> PatBatch:
    at = now or datetime.now(UTC)
    provider = client or DeepSeekResearchClient()
    with connect() as conn, conn.cursor() as cur:
        projection = _load_projection(cur, projection_id)
        cur.execute(
            """SELECT research_run_id FROM wnba.research_plans
               WHERE projection_id=%s AND status='complete'
               ORDER BY created_at DESC LIMIT 1""",
            (projection_id,),
        )
        existing = cur.fetchone()
        if existing is not None and existing["research_run_id"] is not None:
            run_id = UUID(str(existing["research_run_id"]))
            cur.execute(
                """SELECT round,count(*) AS count FROM wnba.agent_forecasts
                   WHERE research_run_id=%s GROUP BY round""",
                (run_id,),
            )
            rounds = {int(str(row["round"])): int(str(row["count"])) for row in cur.fetchall()}
            cur.execute(
                "SELECT count(*) AS count FROM wnba.research_precedents WHERE research_run_id=%s",
                (run_id,),
            )
            count_row = cur.fetchone()
            return PatBatch(
                run_id,
                "complete",
                rounds.get(1, 0),
                rounds.get(2, 0),
                0 if count_row is None else int(str(count_row["count"])),
                False,
            )
        plan = build_plan(projection, now=at)
        audit = audit_projection(projection, now=at)
        persist_plan(cur, projection_id, plan, at=at)
        persist_audit(cur, projection_id, audit, at=at)
        if audit.blocked:
            run_id = uuid4()
            cur.execute(
                """INSERT INTO wnba.research_runs
                   (research_run_id,projection_id,provider,model,started_at,completed_at,status,
                    prompt_sha256,error)
                   VALUES (%s,%s,'policy','data-auditor',%s,%s,'blocked',%s,%s)""",
                (run_id, projection_id, at, at, "0" * 64, "; ".join(i.code for i in audit.issues)),
            )
            cur.execute(
                """UPDATE wnba.research_plans SET research_run_id=%s,status='blocked'
                   WHERE plan_id=%s""",
                (run_id, plan.plan_id),
            )
            cur.execute(
                "UPDATE wnba.research_audits SET research_run_id=%s WHERE audit_id=%s",
                (run_id, audit.audit_id),
            )
            return PatBatch(run_id, "blocked", 0, 0, 0, False)

    base = run_projection_research(projection_id, client=provider, now=at)
    with connect() as conn, conn.cursor() as cur:
        projection = _load_projection(cur, projection_id)
        evidence = _run_evidence(cur, base.research_run_id)
        precedents = retrieve_precedents(cur, projection)
        # The desk paid to retrieve these; until now they were stored and never shown to the
        # agents. Their summaries carry the settled outcome, which is the calibration signal a
        # probability-producing agent most lacks.
        precedent_summaries = [item.summary for item in precedents]
        for rank, precedent in enumerate(precedents, 1):
            cur.execute(
                """INSERT INTO wnba.research_precedents(research_run_id,episode_id,similarity,rank)
                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (base.research_run_id, precedent.episode_id, precedent.similarity, rank),
            )
        context = f"{projection['full_name']} {projection['prop_type']} line {projection['line']}"
        advisor = Advisor(cur, client=provider, enabled=True)
        forecasting_roles = {
            role: question for role, question in AGENT_QUESTIONS.items() if role != SKEPTIC_ROLE
        }
        track_records = _track_records(cur, sorted(forecasting_roles))
        with ThreadPoolExecutor(max_workers=len(forecasting_roles)) as executor:
            futures = {
                role: executor.submit(
                    provider.forecast,
                    role=role,
                    question=f"{context}. {question}",
                    evidence=evidence,
                    precedents=precedent_summaries,
                    track_record=track_records.get(role),
                )
                for role, question in forecasting_roles.items()
            }
            first = _collect(
                advisor, futures, task="agent_forecast_round_one", context=context, at=at
            )
        first_ids: dict[str, UUID] = {}
        for role, result in first.items():
            forecast_id = uuid4()
            first_ids[role] = forecast_id
            cur.execute(
                """INSERT INTO wnba.agent_forecasts
                   (agent_forecast_id,research_run_id,agent_role,round,advisory_probability,
                    rationale,evidence_ids,created_at) VALUES (%s,%s,%s,1,%s,%s,%s,%s)""",
                (
                    forecast_id,
                    base.research_run_id,
                    role,
                    result.advisory_probability,
                    result.rationale,
                    result.evidence_ids,
                    at,
                ),
            )
        peer_views = [
            {
                "agent_role": role,
                "advisory_probability": result.advisory_probability,
                "rationale": result.rationale,
                "forecast_id": str(first_ids[role]),
            }
            for role, result in first.items()
        ]
        # One seat argues round two without seeing its peers. Everything the synthesis reads --
        # the dispersion that triggers caution, the consensus that gets compared to the model --
        # is measured on round two, and showing every agent its peers' round-one views compresses
        # exactly that dispersion. Holding one seat blind leaves a control: the gap between the
        # blind view and the debated ones is the size of the herding, and it is now measurable
        # rather than assumed away. The seat rotates deterministically by run so no single role
        # permanently carries the control condition.
        blind_role = _blind_role(base.research_run_id, sorted(forecasting_roles))
        with ThreadPoolExecutor(max_workers=len(forecasting_roles)) as executor:
            futures = {
                role: executor.submit(
                    provider.forecast,
                    role=role,
                    question=f"Reconsider {context}. {question}",
                    evidence=evidence,
                    peers=None if role == blind_role else peer_views,
                    precedents=precedent_summaries,
                    track_record=track_records.get(role),
                )
                for role, question in forecasting_roles.items()
            }
            second = _collect(
                advisor, futures, task="agent_forecast_round_two", context=context, at=at
            )
        for role, result in second.items():
            cur.execute(
                """INSERT INTO wnba.agent_forecasts
                   (agent_forecast_id,research_run_id,agent_role,round,advisory_probability,
                    rationale,evidence_ids,peer_forecast_ids,created_at)
                   VALUES (%s,%s,%s,2,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    base.research_run_id,
                    role,
                    result.advisory_probability,
                    result.rationale,
                    result.evidence_ids,
                    # The blind seat cites no peers, because it saw none. Recording the debated
                    # seats' peer lists and the blind seat's empty one is what makes the control
                    # recoverable from the database months later.
                    [] if role == blind_role else list(first_ids.values()),
                    at,
                ),
            )
        review = advisor.attempt(
            task="skeptic_review",
            subject=context,
            call=lambda: provider.challenge(
                question=f"{context}. {AGENT_QUESTIONS[SKEPTIC_ROLE]}",
                evidence=evidence,
                peers=peer_views,
                precedents=precedent_summaries,
            ),
            now=at,
        )
        if review is not None:
            _persist_skeptic_review(cur, base.research_run_id, review, at=at)
        synthesis_result = synthesize(
            model_probability=float(projection["probability_over"]),
            round_two=list(second.values()),
            audit_blocked=False,
            data_quality_score=float(projection["data_quality_score"]),
            credibility=_credibility_weights(cur, sorted(second)),
            roles=sorted(second),
            skeptic_fragility=None if review is None else review.fragility,
            skeptic_flags=() if review is None else tuple(review.risk_flags),
        )
        persist_synthesis(cur, base.research_run_id, synthesis_result, at=at)
        proposed = False
        if synthesis_result.disposition in {"caution", "block"} and evidence:
            draft = advisor.attempt(
                task="rule_proposal_from_research",
                subject=context,
                call=lambda: provider.propose_rule(
                    measured_failure=synthesis_result.summary, evidence=evidence
                ),
                now=at,
            )
            proposed = draft is not None and persist_rule_proposal(cur, draft, at=at)
        cur.execute(
            "UPDATE wnba.research_plans SET research_run_id=%s,status='complete' WHERE plan_id=%s",
            (base.research_run_id, plan.plan_id),
        )
        cur.execute(
            "UPDATE wnba.research_audits SET research_run_id=%s WHERE audit_id=%s",
            (base.research_run_id, audit.audit_id),
        )
    return PatBatch(
        base.research_run_id, "complete", len(first), len(second), len(precedents), proposed
    )


def run_triggered_research(
    *, limit: int = 10, client: DeepSeekResearchClient | None = None
) -> list[PatBatch]:
    """Run only materially triggered, unlocked forecasts without a completed PAT plan."""
    at = datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT f.projection_id FROM wnba.stat_forecasts f
               JOIN wnba.prop_quotes q USING(quote_id)
               WHERE f.expires_at>%s AND q.locks_at>%s
                 AND NOT EXISTS (SELECT 1 FROM wnba.research_plans rp
                                 WHERE rp.projection_id=f.projection_id AND rp.status='complete')
               ORDER BY f.generated_at DESC LIMIT %s""",
            (at, at, limit * 5),
        )
        candidates = [UUID(str(row["projection_id"])) for row in cur.fetchall()]
        selected: list[UUID] = []
        for projection_id in candidates:
            projection = _load_projection(cur, projection_id)
            if trigger_codes(projection, now=at):
                selected.append(projection_id)
            if len(selected) >= limit:
                break
    return [run_pat_research(item, client=client, now=at) for item in selected]
