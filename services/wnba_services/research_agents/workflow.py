"""Evidence construction and controlled multi-agent research persistence."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import NAMESPACE_URL as _DOCUMENT_NAMESPACE
from uuid import UUID, uuid4, uuid5

from psycopg.types.json import Jsonb
from wnba_store.db import connect

from wnba_services.research_agents.advisor import Advisor
from wnba_services.research_agents.deepseek import DeepSeekResearchClient
from wnba_services.research_agents.evidence import build_evidence_document

# The one role that does not forecast. It reviews on its own schema (`SkepticReview`) and its
# output never enters the consensus average -- see `deepseek.SkepticReview` for why a skeptic
# forced to emit a probability stops being a skeptic.
SKEPTIC_ROLE = "skeptic"

AGENT_QUESTIONS = {
    "availability": "Assess availability evidence and identify unresolved status risk.",
    "rotation": "Assess expected role, minutes, and closing-lineup evidence.",
    "matchup": "Assess pace, opponent defense, rest, margin, and blowout evidence.",
    "market": "Assess quote freshness and whether market context creates uncertainty.",
    "skeptic": "Challenge the supplied evidence and identify reasons the forecast may be fragile.",
}


@dataclass(frozen=True)
class ResearchBatch:
    research_run_id: UUID
    analyses: int
    claims: int
    evidence: int


def run_projection_research(
    projection_id: UUID,
    *,
    client: DeepSeekResearchClient | None = None,
    now: datetime | None = None,
) -> ResearchBatch:
    """Run independent agents over a frozen forecast snapshot and persist cited claims."""
    at = now or datetime.now(UTC)
    provider = client or DeepSeekResearchClient()
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT f.*,fs.features,q.source,q.system_from AS observed_at,q.locks_at,
                      q.over_multiplier,q.under_multiplier,
                      d.model_disagreement,
                      p.full_name
               FROM wnba.stat_forecasts f
               JOIN wnba.feature_snapshots fs ON fs.feature_snapshot_id=f.feature_snapshot_id
               JOIN wnba.prop_quotes q ON q.quote_id=f.quote_id
               JOIN wnba.decision_episodes d ON d.quote_id=f.quote_id
                 AND d.model_run_id=f.model_run_id
               JOIN wnba.players p ON p.player_id=f.player_id
               WHERE f.projection_id=%s""",
            (projection_id,),
        )
        projection = cur.fetchone()
        if projection is None:
            raise ValueError(f"unknown projection {projection_id}")
        locks_at = projection["locks_at"]
        if not isinstance(locks_at, datetime):
            raise ValueError("projection lock time is invalid")
        if locks_at <= at:
            raise ValueError("research cannot run after the market locks")
        cur.execute(
            """SELECT research_run_id FROM wnba.research_runs
               WHERE projection_id=%s AND status='complete' ORDER BY completed_at DESC LIMIT 1""",
            (projection_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            cur.execute(
                """SELECT count(*) AS analyses,
                          coalesce(sum((SELECT count(*) FROM wnba.research_claims c
                            WHERE c.analysis_id=a.analysis_id)),0) AS claims
                   FROM wnba.agent_analyses a WHERE research_run_id=%s""",
                (existing["research_run_id"],),
            )
            counts = cur.fetchone()
            if counts is None:
                raise RuntimeError("research count aggregate returned no row")
            return ResearchBatch(
                UUID(str(existing["research_run_id"])),
                int(str(counts["analyses"])),
                int(str(counts["claims"])),
                0,
            )
        document, evidence = build_evidence_document(cur, dict(projection), now=at)
        document_hash = hashlib.sha256(document.encode()).hexdigest()
        document_id = uuid5(_DOCUMENT_NAMESPACE, f"courtside-edge:document:{document_hash}")
        cur.execute(
            """INSERT INTO wnba.source_documents
               (document_id,source,title,content_sha256,content_excerpt,retrieved_at)
               VALUES (%s,'derived',%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (
                document_id,
                f"Frozen forecast evidence: {projection['full_name']} {projection['prop_type']}",
                document_hash,
                document[:4000],
                at,
            ),
        )
        for evidence_id, excerpt in evidence.items():
            cur.execute(
                """INSERT INTO wnba.evidence
                   (evidence_id,document_id,subject_id,excerpt,observed_at,reliability)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (
                    evidence_id,
                    document_id,
                    projection["player_id"],
                    excerpt,
                    at,
                    projection["data_quality_score"],
                ),
            )
        workflow_hash = hashlib.sha256(
            f"{projection_id}:{document_hash}:{provider.model}".encode()
        ).hexdigest()
        run_id = uuid4()
        cur.execute(
            """INSERT INTO wnba.research_runs
               (research_run_id,projection_id,provider,model,started_at,status,prompt_sha256)
               VALUES (%s,%s,'deepseek',%s,%s,'running',%s)""",
            (run_id, projection_id, provider.model, at, workflow_hash),
        )
        analyses = claims = 0
        question_context = (
            f"Player: {projection['full_name']}; market: {projection['prop_type']}; "
            f"line: {projection['line']}. "
        )
        # `enabled=True` because a provider was supplied: the Advisor's usual key check answers
        # "should we call at all", and that question is already settled by the time a client is
        # in hand. A missing key still fails every call, and is recorded as such.
        advisor = Advisor(cur, client=provider, enabled=True)
        try:
            with ThreadPoolExecutor(max_workers=len(AGENT_QUESTIONS)) as executor:
                futures = {
                    role: executor.submit(
                        provider.analyze,
                        role=role,
                        question=question_context + question,
                        evidence=evidence,
                    )
                    for role, question in AGENT_QUESTIONS.items()
                }
                # One role failing is a missing voice, not a lost run. Each result is collected
                # separately and its failure recorded as an advisory fallback, so a provider
                # hiccup during the fourth of five calls no longer discards the other four.
                generated: dict[str, Any] = {}
                for role, future in futures.items():
                    result = advisor.attempt(
                        task="research_analysis",
                        subject=f"{projection['full_name']} {projection['prop_type']} {role}",
                        call=future.result,
                        now=at,
                    )
                    if result is not None:
                        generated[role] = result
            if not generated:
                raise RuntimeError("every research agent failed; nothing to record")
            for role in generated:
                analysis, _, response_hash = generated[role]
                analysis_id = uuid4()
                cited = sorted(
                    {
                        evidence_id
                        for claim in analysis.claims
                        for evidence_id in claim.evidence_ids + claim.contradicting_evidence_ids
                    },
                    key=str,
                )
                cur.execute(
                    """INSERT INTO wnba.agent_analyses
                       (analysis_id,research_run_id,agent_role,conclusion,confidence,risk_flags,
                        evidence_ids,response_sha256)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        analysis_id,
                        run_id,
                        role,
                        analysis.conclusion,
                        analysis.confidence,
                        Jsonb(analysis.risk_flags),
                        cited,
                        response_hash,
                    ),
                )
                analyses += 1
                for claim in analysis.claims:
                    cur.execute(
                        """INSERT INTO wnba.research_claims
                           (claim_id,analysis_id,subject_id,predicate,value,confidence,evidence_ids,
                            contradicting_evidence_ids,valid_from,expires_at,generated_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            uuid4(),
                            analysis_id,
                            projection["player_id"],
                            claim.predicate,
                            claim.value,
                            claim.confidence,
                            claim.evidence_ids,
                            claim.contradicting_evidence_ids,
                            at,
                            locks_at,
                            role,
                        ),
                    )
                    claims += 1
            cur.execute(
                """UPDATE wnba.research_runs SET status='complete',completed_at=%s
                   WHERE research_run_id=%s""",
                (datetime.now(UTC), run_id),
            )
        except Exception as exc:
            cur.execute(
                """UPDATE wnba.research_runs SET status='failed',completed_at=%s,error=%s
                   WHERE research_run_id=%s""",
                (datetime.now(UTC), str(exc)[:1000], run_id),
            )
            conn.commit()
            raise
    return ResearchBatch(run_id, analyses, claims, len(evidence))
