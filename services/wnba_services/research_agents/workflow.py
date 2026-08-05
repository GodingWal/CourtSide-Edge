"""Evidence construction and controlled multi-agent research persistence.

The run happens in two stages. Four analysts read the frozen forecast evidence independently and
concurrently; the skeptic then reads the same evidence *plus* their conclusions, handed to it as
ordinary cited evidence rows. That ordering is the whole point. A skeptic that sees only what
everyone else saw is not reviewing anyone -- it is a fifth analyst with a pessimistic prompt, and
its ``contradicting_evidence_ids`` can never name the thing it actually disagrees with.

The provider is never called while a database transaction is open. Evidence and the ``running``
row are committed before the first request, so a run that dies mid-flight leaves a record of what
it was doing rather than vanishing, and a second caller can see that spending is already underway.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import psycopg
from psycopg.types.json import Jsonb
from wnba_store.db import connect

from wnba_services.research_agents.deepseek import (
    AgentAnalysis,
    AgentResponse,
    DeepSeekResearchClient,
)
from wnba_services.research_agents.synthesis import ResearchVerdict, synthesize

ANALYST_QUESTIONS = {
    "availability": "Assess availability evidence and identify unresolved status risk.",
    "rotation": "Assess expected role, minutes, and closing-lineup evidence.",
    "matchup": "Assess pace, opponent defense, rest, margin, and blowout evidence.",
    "market": "Assess quote freshness and whether market context creates uncertainty.",
}

SKEPTIC_ROLE = "skeptic"
SKEPTIC_QUESTION = (
    "Challenge the supplied evidence and the four analyst conclusions drawn from it. Identify "
    "reasons the forecast may be fragile."
)
SKEPTIC_DIRECTIVE = (
    "Evidence items whose ids are named in the analyst_conclusions group are the stage-one "
    "conclusions of the other agents. Treat them as claims to be tested, not as facts. When you "
    "judge a conclusion or an evidence item to be unsupported, overstated, or contradicted, name "
    "its evidence_id in contradicting_evidence_ids. Objections that are not recorded that way are "
    "not counted."
)

# The full roster. The staged workflow below deliberately does not use this -- it runs the four
# analysts first so the skeptic has something to review. The PAT forecast rounds do, because there
# every role is polled at once for an advisory probability and there is no conclusion to challenge.
AGENT_QUESTIONS = {**ANALYST_QUESTIONS, SKEPTIC_ROLE: SKEPTIC_QUESTION}


@dataclass(frozen=True)
class ResearchBatch:
    research_run_id: UUID
    analyses: int
    claims: int
    evidence: int
    verdict: ResearchVerdict | None


@dataclass(frozen=True)
class _PreparedRun:
    """A committed run header with its frozen evidence, ready for the provider."""

    run_id: UUID
    projection: dict[str, Any]
    document_id: UUID
    evidence: dict[UUID, str]
    locks_at: datetime


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


def _peer_evidence(
    run_id: UUID,
    stage_one: dict[str, AgentResponse],
) -> tuple[str, dict[str, UUID], dict[UUID, str]]:
    """Render stage-one conclusions as citable evidence for the skeptic.

    Ids are scoped to the run because the conclusions belong to this run: the same projection
    reviewed again produces different sentences and must not silently reuse the earlier ids.
    """
    conclusions = {
        role: {
            "role": role,
            "conclusion": response.analysis.conclusion,
            "stated_confidence": response.analysis.confidence,
            "risk_flags": response.analysis.risk_flags,
            "claims": [
                {"predicate": claim.predicate, "value": claim.value}
                for claim in response.analysis.claims
            ],
        }
        for role, response in sorted(stage_one.items())
    }
    document = json.dumps(conclusions, sort_keys=True, default=str)
    ids = {
        role: uuid5(NAMESPACE_URL, f"courtside-edge:evidence:{run_id}:analyst_conclusions:{role}")
        for role in conclusions
    }
    excerpts = {
        ids[role]: json.dumps(payload, sort_keys=True, default=str)
        for role, payload in conclusions.items()
    }
    return document, ids, excerpts


def _stale_after(provider: DeepSeekResearchClient) -> timedelta:
    """How long a ``running`` row may sit before it is treated as abandoned rather than active."""
    worst_case = provider.timeout_seconds * provider.max_attempts * 2
    return timedelta(seconds=worst_case + 60)


def _load_verdict(
    cur: psycopg.Cursor[dict[str, Any]],
    run_id: UUID,
) -> ResearchVerdict | None:
    cur.execute(
        "SELECT * FROM wnba.research_verdicts WHERE research_run_id=%s",
        (run_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return ResearchVerdict(
        caution=str(row["caution"]),
        agreement=float(str(row["agreement"])),
        total_claims=int(str(row["total_claims"])),
        contested_claims=int(str(row["contested_claims"])),
        contested_predicates=tuple(str(v) for v in list(row["contested_predicates"] or [])),
        risk_flags=tuple(str(v) for v in list(row["risk_flags"] or [])),
        skeptic_confidence=float(str(row["skeptic_confidence"])),
        rationale=str(row["rationale"]),
    )


def _completed_batch(
    cur: psycopg.Cursor[dict[str, Any]],
    run_id: UUID,
) -> ResearchBatch:
    cur.execute(
        """SELECT count(*) AS analyses,
                  coalesce(sum((SELECT count(*) FROM wnba.research_claims c
                    WHERE c.analysis_id=a.analysis_id)),0) AS claims
           FROM wnba.agent_analyses a WHERE research_run_id=%s""",
        (run_id,),
    )
    counts = cur.fetchone()
    if counts is None:
        raise RuntimeError("research count aggregate returned no row")
    return ResearchBatch(
        run_id,
        int(str(counts["analyses"])),
        int(str(counts["claims"])),
        0,
        _load_verdict(cur, run_id),
    )


def _prepare(
    projection_id: UUID,
    provider: DeepSeekResearchClient,
    at: datetime,
) -> _PreparedRun | ResearchBatch:
    """Claim the run and commit its evidence, or return the batch that already exists."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT f.*,fs.features,q.source,q.system_from AS observed_at,q.locks_at,
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
            return _completed_batch(cur, UUID(str(existing["research_run_id"])))

        # A run that is genuinely in flight must not be paid for twice. One that has outlived
        # every retry its provider could have made is not in flight; it is wreckage, and saying so
        # is what lets the next attempt proceed.
        cur.execute(
            """SELECT research_run_id,started_at FROM wnba.research_runs
               WHERE projection_id=%s AND status='running'
               ORDER BY started_at DESC LIMIT 1""",
            (projection_id,),
        )
        in_flight = cur.fetchone()
        if in_flight is not None:
            started_at = in_flight["started_at"]
            if isinstance(started_at, datetime) and started_at > at - _stale_after(provider):
                raise ValueError(
                    f"research is already running for projection {projection_id}; "
                    "wait for it to finish rather than paying for it twice"
                )
            cur.execute(
                """UPDATE wnba.research_runs
                   SET status='failed',completed_at=%s,
                       error='abandoned: no result before the provider timeout budget elapsed'
                   WHERE research_run_id=%s""",
                (at, in_flight["research_run_id"]),
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
        conn.commit()
    return _PreparedRun(
        run_id=run_id,
        projection=dict(projection),
        document_id=document_id,
        evidence=evidence,
        locks_at=locks_at,
    )


def _run_agents(
    prepared: _PreparedRun,
    provider: DeepSeekResearchClient,
) -> tuple[dict[str, AgentResponse], AgentResponse, dict[str, UUID], dict[UUID, str], str]:
    """Stage one concurrently, then the skeptic over stage one's conclusions."""
    projection = prepared.projection
    context = (
        f"Player: {projection['full_name']}; market: {projection['prop_type']}; "
        f"line: {projection['line']}. "
    )
    with ThreadPoolExecutor(max_workers=len(ANALYST_QUESTIONS)) as executor:
        futures = {
            role: executor.submit(
                provider.analyze,
                role=role,
                question=context + question,
                evidence=prepared.evidence,
            )
            for role, question in ANALYST_QUESTIONS.items()
        }
        stage_one = {role: future.result() for role, future in futures.items()}

    peer_document, peer_ids, peer_excerpts = _peer_evidence(prepared.run_id, stage_one)
    skeptic = provider.analyze(
        role=SKEPTIC_ROLE,
        question=context + SKEPTIC_QUESTION,
        evidence={**prepared.evidence, **peer_excerpts},
        directive=SKEPTIC_DIRECTIVE,
    )
    return stage_one, skeptic, peer_ids, peer_excerpts, peer_document


def _persist(
    prepared: _PreparedRun,
    stage_one: dict[str, AgentResponse],
    skeptic: AgentResponse,
    peer_ids: dict[str, UUID],
    peer_excerpts: dict[UUID, str],
    peer_document: str,
    verdict: ResearchVerdict,
    at: datetime,
) -> tuple[int, int]:
    projection = prepared.projection
    responses: dict[str, AgentResponse] = {**stage_one, SKEPTIC_ROLE: skeptic}
    with connect() as conn, conn.cursor() as cur:
        peer_hash = hashlib.sha256(peer_document.encode()).hexdigest()
        peer_document_id = uuid5(NAMESPACE_URL, f"courtside-edge:document:{peer_hash}")
        cur.execute(
            """INSERT INTO wnba.source_documents
               (document_id,source,title,content_sha256,content_excerpt,retrieved_at)
               VALUES (%s,'derived',%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (
                peer_document_id,
                f"Stage-one analyst conclusions: {projection['full_name']} "
                f"{projection['prop_type']}",
                peer_hash,
                peer_document[:4000],
                at,
            ),
        )
        for role, evidence_id in sorted(peer_ids.items()):
            cur.execute(
                """INSERT INTO wnba.evidence
                   (evidence_id,document_id,subject_id,excerpt,observed_at,reliability)
                   VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (
                    evidence_id,
                    peer_document_id,
                    projection["player_id"],
                    peer_excerpts[evidence_id],
                    at,
                    stage_one[role].analysis.confidence,
                ),
            )

        analyses = claims = 0
        for role, response in [*sorted(stage_one.items()), (SKEPTIC_ROLE, skeptic)]:
            analysis = response.analysis
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
                    prepared.run_id,
                    role,
                    analysis.conclusion,
                    analysis.confidence,
                    Jsonb(analysis.risk_flags),
                    cited,
                    response.response_sha256,
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
                        prepared.locks_at,
                        role,
                    ),
                )
                claims += 1

        cur.execute(
            """INSERT INTO wnba.research_verdicts
               (verdict_id,research_run_id,caution,agreement,total_claims,contested_claims,
                contested_predicates,risk_flags,skeptic_confidence,rationale)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                uuid4(),
                prepared.run_id,
                verdict.caution,
                verdict.agreement,
                verdict.total_claims,
                verdict.contested_claims,
                Jsonb(list(verdict.contested_predicates)),
                Jsonb(list(verdict.risk_flags)),
                verdict.skeptic_confidence,
                verdict.rationale,
            ),
        )

        usages = [response.usage for response in responses.values()]
        complete = all(usage is not None for usage in usages)
        prompt_tokens = sum(u.prompt_tokens for u in usages if u is not None) if complete else None
        completion_tokens = (
            sum(u.completion_tokens for u in usages if u is not None) if complete else None
        )
        cur.execute(
            """UPDATE wnba.research_runs
               SET status='complete',completed_at=%s,agent_calls=%s,provider_calls=%s,
                   prompt_tokens=%s,completion_tokens=%s
               WHERE research_run_id=%s""",
            (
                datetime.now(UTC),
                len(responses),
                sum(response.attempts for response in responses.values()),
                prompt_tokens,
                completion_tokens,
                prepared.run_id,
            ),
        )
        conn.commit()
    return analyses, claims


def _mark_failed(run_id: UUID, error: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE wnba.research_runs SET status='failed',completed_at=%s,error=%s
               WHERE research_run_id=%s""",
            (datetime.now(UTC), error[:1000], run_id),
        )
        conn.commit()


def run_projection_research(
    projection_id: UUID,
    *,
    client: DeepSeekResearchClient | None = None,
    now: datetime | None = None,
) -> ResearchBatch:
    """Run independent agents over a frozen forecast snapshot and persist cited claims."""
    at = now or datetime.now(UTC)
    provider = client or DeepSeekResearchClient()
    prepared = _prepare(projection_id, provider, at)
    if isinstance(prepared, ResearchBatch):
        return prepared
    try:
        stage_one, skeptic, peer_ids, peer_excerpts, peer_document = _run_agents(prepared, provider)
        stage_one_analyses: dict[str, AgentAnalysis] = {
            role: response.analysis for role, response in stage_one.items()
        }
        verdict = synthesize(
            analyses=stage_one_analyses,
            skeptic=skeptic.analysis,
            peer_evidence=peer_ids,
        )
        analyses, claims = _persist(
            prepared,
            stage_one,
            skeptic,
            peer_ids,
            peer_excerpts,
            peer_document,
            verdict,
            at,
        )
    except Exception as exc:
        _mark_failed(prepared.run_id, str(exc))
        raise
    return ResearchBatch(
        prepared.run_id,
        analyses,
        claims,
        len(prepared.evidence) + len(peer_excerpts),
        verdict,
    )
