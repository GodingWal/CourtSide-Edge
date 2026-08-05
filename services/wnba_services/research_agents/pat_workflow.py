"""Orchestration for a fail-closed, two-round PAT-style research run."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from wnba_store.db import connect

from wnba_services.research_agents.auditor import audit_projection, persist_audit
from wnba_services.research_agents.coordinator import build_plan, persist_plan, trigger_codes
from wnba_services.research_agents.deepseek import DeepSeekResearchClient
from wnba_services.research_agents.organization import (
    persist_rule_proposal,
    persist_synthesis,
    synthesize,
)
from wnba_services.research_agents.precedents import retrieve_precedents
from wnba_services.research_agents.workflow import AGENT_QUESTIONS, run_projection_research


@dataclass(frozen=True)
class PatBatch:
    research_run_id: UUID
    status: str
    round_one: int
    round_two: int
    precedents: int
    rule_proposed: bool


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
    cur.execute(
        """SELECT DISTINCT e.evidence_id,e.excerpt FROM wnba.evidence e
           JOIN wnba.agent_analyses a ON e.evidence_id=ANY(a.evidence_ids)
           WHERE a.research_run_id=%s""",
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
        for rank, precedent in enumerate(precedents, 1):
            cur.execute(
                """INSERT INTO wnba.research_precedents(research_run_id,episode_id,similarity,rank)
                   VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (base.research_run_id, precedent.episode_id, precedent.similarity, rank),
            )
        context = f"{projection['full_name']} {projection['prop_type']} line {projection['line']}"
        with ThreadPoolExecutor(max_workers=len(AGENT_QUESTIONS)) as executor:
            futures = {
                role: executor.submit(
                    provider.forecast,
                    role=role,
                    question=f"{context}. {question}",
                    evidence=evidence,
                )
                for role, question in AGENT_QUESTIONS.items()
            }
            first = {role: future.result()[0] for role, future in futures.items()}
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
        with ThreadPoolExecutor(max_workers=len(AGENT_QUESTIONS)) as executor:
            futures = {
                role: executor.submit(
                    provider.forecast,
                    role=role,
                    question=f"Reconsider {context}. {question}",
                    evidence=evidence,
                    peers=peer_views,
                )
                for role, question in AGENT_QUESTIONS.items()
            }
            second = {role: future.result()[0] for role, future in futures.items()}
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
                    list(first_ids.values()),
                    at,
                ),
            )
        synthesis_result = synthesize(
            model_probability=float(projection["probability_over"]),
            round_two=list(second.values()),
            audit_blocked=False,
            data_quality_score=float(projection["data_quality_score"]),
        )
        persist_synthesis(cur, base.research_run_id, synthesis_result, at=at)
        proposed = False
        if synthesis_result.disposition in {"caution", "block"} and evidence:
            draft, _, _ = provider.propose_rule(
                measured_failure=synthesis_result.summary, evidence=evidence
            )
            proposed = persist_rule_proposal(cur, draft, at=at)
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
