"""Evidence construction and controlled multi-agent research persistence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb
from wnba_store.db import connect

from wnba_services.research_agents.deepseek import DeepSeekResearchClient

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


def _snapshot_evidence(
    projection: dict[str, object],
) -> tuple[str, dict[UUID, str]]:
    features = projection["features"]
    if not isinstance(features, dict):
        raise ValueError("feature snapshot is not an object")
    groups = {
        "availability": {
            key: features.get(key)
            for key in (
                "injury_designation",
                "injury_detail",
                "expected_minutes",
                "start_probability",
                "closing_lineup_probability",
            )
        },
        "role_effects": {
            key: features.get(key)
            for key in (
                "history_games",
                "teammate_effect_count",
                "teammate_rate_multiplier",
                "teammate_effect_confidence",
            )
        },
        "matchup": {
            key: features.get(key)
            for key in (
                "expected_possessions",
                "pace_multiplier",
                "defense_multiplier",
                "expected_margin",
                "blowout_probability",
                "team_rest_days",
                "opponent_rest_days",
            )
        },
        "market": {
            "source": projection["source"],
            "line": projection["line"],
            "observed_at": str(projection["observed_at"]),
            "locks_at": str(projection["locks_at"]),
            "prop_type": projection["prop_type"],
        },
        "forecast_audit": {
            "model_mean": projection["mean"],
            "model_median": projection["median"],
            "model_stddev": projection["stddev"],
            "model_disagreement": projection["model_disagreement"],
            "data_quality_score": projection["data_quality_score"],
        },
    }
    document = json.dumps(groups, sort_keys=True, default=str)
    document_hash = hashlib.sha256(document.encode()).hexdigest()
    evidence = {
        uuid5(NAMESPACE_URL, f"courtside-edge:evidence:{document_hash}:{name}"): json.dumps(
            values, sort_keys=True, default=str
        )
        for name, values in groups.items()
    }
    return document, evidence


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
            """SELECT f.*,fs.features,q.source,q.observed_at,q.locks_at,d.model_disagreement,
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
        document, evidence = _snapshot_evidence(dict(projection))
        document_hash = hashlib.sha256(document.encode()).hexdigest()
        document_id = uuid5(NAMESPACE_URL, f"courtside-edge:document:{document_hash}")
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
        try:
            for role, question in AGENT_QUESTIONS.items():
                analysis, _, response_hash = provider.analyze(
                    role=role,
                    question=question_context + question,
                    evidence=evidence,
                )
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
