"""Running one planned investigation, end to end.

The old shape was: a human names a projection, five agents receive the same prompt at the same
moment, five paragraphs are stored. Everything about that is defensible except calling it
independent research -- the agents were independent only in the sense that they did not talk to
each other, and nothing downstream did anything with what they said.

The shape now:

1. **The auditor goes first**, deterministically, and can stop the whole thing. An investigation
   over contradictory inputs is not a cheaper investigation, it is a more expensive one, because
   its output looks like evidence.
2. **Round one is independent**, and the record says so. Each analyst sees evidence, the audit's
   non-blocking findings, and comparable historical decisions -- and nothing about its peers.
3. **Round two is adversarial.** Each analyst reads the others' first-round conclusions and may
   revise, concede, or restate its disagreement. Both rounds are kept: a revision is only
   meaningful next to what it revised.
4. **Claims are scheduled, not just expired.** Each claim gets a refresh time derived from how
   fast its domain actually goes stale, and a new claim about the same predicate supersedes the
   old one rather than sitting beside it.
5. **The synthesizer combines everything** into one posture stored beside the episode.

What stays exactly as it was: no research output touches a probability. The forecast this runs
against is already frozen, the synthesis has no column for a number of its own, and every
schema an agent can fill in lacks a field for a prop outcome probability. That is not enforced by
this module's good behaviour -- it is enforced by there being nowhere to put one.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb
from wnba_store.db import connect

from wnba_services.research_agents.auditor import audit_plan_inputs, record_audit
from wnba_services.research_agents.coordinator import PLAN_ROLES
from wnba_services.research_agents.deepseek import DeepSeekResearchClient
from wnba_services.research_agents.precedents import find_precedents, load_candidate_episodes
from wnba_services.research_agents.synthesis import (
    AgentPosition,
    SynthesisInputs,
    record_synthesis,
    synthesize,
)

__all__ = [
    "AGENT_QUESTIONS",
    "CLAIM_REFRESH",
    "ROLE_DOMAIN",
    "ResearchBatch",
    "execute_plan",
    "refresh_due_claims",
    "run_projection_research",
]

AGENT_QUESTIONS = {
    "availability": "Assess availability evidence and identify unresolved status risk.",
    "rotation": "Assess expected role, minutes, and closing-lineup evidence.",
    "matchup": "Assess pace, opponent defense, rest, margin, and blowout evidence.",
    "market": "Assess quote freshness and whether market context creates uncertainty.",
    "skeptic": "Challenge the supplied evidence and identify reasons the forecast may be fragile.",
}

# The domain each role speaks for. Credibility is scored per domain rather than per role overall,
# because an analyst can be reliable about rotations and useless about pace, and one number
# averaging those two describes neither.
ROLE_DOMAIN: dict[str, str] = {
    "availability": "availability",
    "rotation": "rotation",
    "matchup": "matchup",
    "market": "market",
    "skeptic": "fragility",
}

# How long a claim in each domain stays safe to rely on without re-checking. These are not expiry
# times -- every claim still formally expires at lock -- they are the point at which relying on it
# means relying on something nobody has looked at since. Availability is shortest because it is
# the input that changes latest and matters most.
CLAIM_REFRESH: dict[str, timedelta] = {
    "availability": timedelta(minutes=45),
    "rotation": timedelta(minutes=90),
    "market": timedelta(minutes=25),
    "matchup": timedelta(hours=6),
    "fragility": timedelta(minutes=90),
}
_DEFAULT_REFRESH = timedelta(minutes=60)


@dataclass(frozen=True)
class ResearchBatch:
    research_run_id: UUID | None
    analyses: int
    claims: int
    evidence: int
    status: str = "complete"
    posture: str | None = None
    blocked_by: tuple[str, ...] = ()


def _snapshot_evidence(
    projection: dict[str, Any],
    *,
    audit_context: list[dict[str, Any]],
    precedents: dict[str, Any] | None,
) -> tuple[str, dict[UUID, str]]:
    """Freeze everything the analysts are allowed to see into cited, hashed evidence.

    The audit's non-blocking findings and the precedent set are evidence groups like any other,
    which means an analyst that reasons from "the role model has no row for this player" has to
    cite the evidence saying so. Handing agents context they cannot cite is how uncited claims
    get written.
    """
    features = projection["features"]
    if not isinstance(features, dict):
        raise ValueError("feature snapshot is not an object")
    groups: dict[str, Any] = {
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
        "data_audit": {"findings": audit_context},
    }
    if precedents is not None:
        groups["historical_precedents"] = precedents

    document = json.dumps(groups, sort_keys=True, default=str)
    document_hash = hashlib.sha256(document.encode()).hexdigest()
    evidence = {
        uuid5(NAMESPACE_URL, f"courtside-edge:evidence:{document_hash}:{name}"): json.dumps(
            values, sort_keys=True, default=str
        )
        for name, values in groups.items()
    }
    return document, evidence


def _refresh_at(role: str, at: datetime, locks_at: datetime) -> datetime:
    domain = ROLE_DOMAIN.get(role, "fragility")
    return min(locks_at, at + CLAIM_REFRESH.get(domain, _DEFAULT_REFRESH))


def _supersede_prior_claims(
    cur: Any, *, subject_id: Any, predicate: str, role: str, at: datetime, new_claim_id: UUID
) -> None:
    """Point older claims about the same predicate at their replacement.

    Without this, an investigation that re-checks a claim leaves both versions live and every
    downstream reader has to guess which one is current -- and the contradiction detector counts
    a claim disagreeing with its own earlier self as a conflict.
    """
    cur.execute(
        """UPDATE wnba.research_claims
           SET superseded_by=%s,superseded_at=%s
           WHERE subject_id=%s AND predicate=%s AND generated_by=%s
             AND superseded_at IS NULL AND claim_id<>%s""",
        (new_claim_id, at, subject_id, predicate, role, new_claim_id),
    )


def _store_claims(
    cur: Any,
    *,
    analysis_id: UUID,
    claims: Any,
    role: str,
    subject_id: Any,
    at: datetime,
    locks_at: datetime,
) -> int:
    stored = 0
    for claim in claims:
        claim_id = uuid4()
        cur.execute(
            """INSERT INTO wnba.research_claims
               (claim_id,analysis_id,subject_id,predicate,value,confidence,evidence_ids,
                contradicting_evidence_ids,valid_from,expires_at,generated_by,refresh_after,
                domain)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                claim_id,
                analysis_id,
                subject_id,
                claim.predicate,
                claim.value,
                claim.confidence,
                claim.evidence_ids,
                claim.contradicting_evidence_ids,
                at,
                locks_at,
                role,
                _refresh_at(role, at, locks_at),
                ROLE_DOMAIN.get(role, "fragility"),
            ),
        )
        _supersede_prior_claims(
            cur,
            subject_id=subject_id,
            predicate=claim.predicate,
            role=role,
            at=at,
            new_claim_id=claim_id,
        )
        stored += 1
    return stored


def _credibility(cur: Any) -> dict[str, float]:
    """Latest credibility per role, keyed by the role's own domain. Absent means 0.5."""
    cur.execute(
        """SELECT DISTINCT ON (agent_role,domain) agent_role,domain,credibility
           FROM wnba.agent_credibility ORDER BY agent_role,domain,calculated_at DESC"""
    )
    scores: dict[str, float] = {}
    for row in cur.fetchall():
        role = str(row["agent_role"])
        if ROLE_DOMAIN.get(role) == str(row["domain"]):
            scores[role] = float(str(row["credibility"]))
    return scores


def _policy_state(cur: Any, episode_id: Any) -> tuple[bool, list[str]]:
    """What policy did to this decision, read from the firings rather than re-derived."""
    cur.execute(
        """SELECT r.rule_id,r.action_kind,ar.title
           FROM wnba.rule_firings r
           JOIN wnba.analyst_rules ar ON ar.rule_id=r.rule_id
           WHERE r.episode_id=%s AND NOT r.shadow""",
        (episode_id,),
    )
    reasons: list[str] = []
    blocked = False
    for row in cur.fetchall():
        kind = str(row["action_kind"])
        reasons.append(f"{row['rule_id']} ({kind}): {row['title']}")
        if kind in {"block", "require_evidence"}:
            blocked = True
    return blocked, reasons


def execute_plan(
    plan_id: UUID,
    *,
    client: DeepSeekResearchClient | None = None,
    now: datetime | None = None,
    adversarial_round: bool = True,
) -> ResearchBatch:
    """Audit, investigate in two rounds, and synthesise one planned investigation."""
    at = now or datetime.now(UTC)
    provider = client or DeepSeekResearchClient()

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT p.*,f.projection_id,f.player_id,f.game_id,f.prop_type,f.line,f.mean,
                      f.median,f.stddev,f.data_quality_score,f.generated_at,
                      fs.features,q.source,q.system_from AS observed_at,q.locks_at,
                      d.episode_id,d.model_disagreement,d.side,d.predicted_probability,
                      pl.full_name
               FROM wnba.research_plans p
               JOIN wnba.stat_forecasts f ON f.projection_id=p.projection_id
               JOIN wnba.feature_snapshots fs ON fs.feature_snapshot_id=f.feature_snapshot_id
               JOIN wnba.prop_quotes q ON q.quote_id=f.quote_id
               JOIN wnba.decision_episodes d ON d.quote_id=f.quote_id
                 AND d.model_run_id=f.model_run_id
               JOIN wnba.players pl ON pl.player_id=f.player_id
               WHERE p.plan_id=%s FOR UPDATE OF p""",
            (plan_id,),
        )
        plan = cur.fetchone()
        if plan is None:
            raise ValueError(f"unknown research plan {plan_id}")
        if str(plan["status"]) not in {"planned", "auditing"}:
            raise ValueError(f"plan {plan_id} is {plan['status']}, not runnable")

        locks_at = plan["locks_at"]
        if not isinstance(locks_at, datetime):
            raise ValueError("plan lock time is invalid")

        features = plan["features"] if isinstance(plan["features"], dict) else {}
        cur.execute(
            """SELECT count(*) AS contradicting FROM wnba.research_claims c
               WHERE c.subject_id=%s AND c.superseded_at IS NULL AND c.expires_at>%s
                 AND cardinality(c.contradicting_evidence_ids)>0""",
            (plan["player_id"], at),
        )
        conflict_row = cur.fetchone()

        # ---- the audit gate -------------------------------------------------------------
        cur.execute(
            "UPDATE wnba.research_plans SET status='auditing',started_at=%s WHERE plan_id=%s",
            (at, plan_id),
        )
        report = audit_plan_inputs(
            {
                "locks_at": locks_at,
                "quote_observed_at": plan["observed_at"],
                "injury_designation": features.get("injury_designation"),
                "availability_probability": features.get("availability_probability"),
                "start_probability": features.get("start_probability"),
                "expected_minutes": features.get("expected_minutes"),
                "history_games": features.get("history_games"),
                "data_quality_score": plan["data_quality_score"],
                "contradicting_claims": (
                    0 if conflict_row is None else int(str(conflict_row["contradicting"]))
                ),
            },
            now=at,
        )
        record_audit(cur, plan_id, report, at=at)
        if report.is_blocked:
            return ResearchBatch(
                research_run_id=None,
                analyses=0,
                claims=0,
                evidence=0,
                status="blocked",
                blocked_by=tuple(finding.code for finding in report.blocking),
            )

        # ---- evidence, including precedents ----------------------------------------------
        precedent_set = find_precedents(
            {
                "episode_id": plan["episode_id"],
                "prop_type": plan["prop_type"],
                "line": plan["line"],
                "predicted_probability": plan["predicted_probability"],
                "projected_minutes": features.get("expected_minutes"),
                "model_disagreement": plan["model_disagreement"],
                "start_probability": features.get("start_probability"),
                "data_quality_score": plan["data_quality_score"],
            },
            load_candidate_episodes(
                cur, prop_type=str(plan["prop_type"]), before=plan["generated_at"]
            ),
        )
        document, evidence = _snapshot_evidence(
            dict(plan),
            audit_context=report.context_for_agents(),
            precedents=precedent_set.to_payload() if precedent_set.count else None,
        )
        document_hash = hashlib.sha256(document.encode()).hexdigest()
        document_id = uuid5(NAMESPACE_URL, f"courtside-edge:document:{document_hash}")
        cur.execute(
            """INSERT INTO wnba.source_documents
               (document_id,source,title,content_sha256,content_excerpt,retrieved_at)
               VALUES (%s,'derived',%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (
                document_id,
                f"Frozen forecast evidence: {plan['full_name']} {plan['prop_type']}",
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
                    plan["player_id"],
                    excerpt,
                    at,
                    plan["data_quality_score"],
                ),
            )

        # The stored roles arrive from a Postgres text[] typed as `object`. Refusing anything
        # that is not actually a list stops a driver change from iterating a string into single
        # characters and then running an investigation with five one-letter "roles".
        raw_roles = plan["roles"]
        roles = (
            [str(role) for role in raw_roles if str(role) in AGENT_QUESTIONS]
            if isinstance(raw_roles, list)
            else []
        )
        if not roles:
            roles = list(PLAN_ROLES["manual"])
        workflow_hash = hashlib.sha256(
            f"{plan['projection_id']}:{document_hash}:{provider.model}:{sorted(roles)}".encode()
        ).hexdigest()
        run_id = uuid4()
        cur.execute(
            """INSERT INTO wnba.research_runs
               (research_run_id,projection_id,provider,model,started_at,status,prompt_sha256)
               VALUES (%s,%s,'deepseek',%s,%s,'running',%s)""",
            (run_id, plan["projection_id"], provider.model, at, workflow_hash),
        )
        cur.execute(
            "UPDATE wnba.research_plans SET status='running',research_run_id=%s WHERE plan_id=%s",
            (run_id, plan_id),
        )

        context = (
            f"Player: {plan['full_name']}; market: {plan['prop_type']}; line: {plan['line']}. "
        )
        analyses = claims = 0
        try:
            # ---- round one: independent -------------------------------------------------
            with ThreadPoolExecutor(max_workers=max(1, len(roles))) as executor:
                futures = {
                    role: executor.submit(
                        provider.analyze,
                        role=role,
                        question=context + AGENT_QUESTIONS[role],
                        evidence=evidence,
                    )
                    for role in roles
                }
                first_round = {role: future.result() for role, future in futures.items()}

            analysis_ids: dict[str, UUID] = {}
            for role in roles:
                analysis, _, response_hash = first_round[role]
                analysis_id = uuid4()
                analysis_ids[role] = analysis_id
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
                        evidence_ids,response_sha256,round,saw_peers)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,false)""",
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
                claims += _store_claims(
                    cur,
                    analysis_id=analysis_id,
                    claims=analysis.claims,
                    role=role,
                    subject_id=plan["player_id"],
                    at=at,
                    locks_at=locks_at,
                )

            positions: dict[str, AgentPosition] = {
                role: AgentPosition(
                    role=role,
                    conclusion=first_round[role][0].conclusion,
                    confidence=first_round[role][0].confidence,
                    round_number=1,
                    risk_flags=tuple(first_round[role][0].risk_flags),
                )
                for role in roles
            }

            # ---- round two: adversarial -------------------------------------------------
            if adversarial_round and len(roles) > 1:
                peer_view = {
                    role: [
                        {
                            "role": other,
                            "conclusion": first_round[other][0].conclusion,
                            "confidence": first_round[other][0].confidence,
                            "risk_flags": first_round[other][0].risk_flags,
                        }
                        for other in roles
                        if other != role
                    ]
                    for role in roles
                }
                with ThreadPoolExecutor(max_workers=max(1, len(roles))) as executor:
                    revision_futures = {
                        role: executor.submit(
                            provider.revise,
                            role=role,
                            question=context + AGENT_QUESTIONS[role],
                            evidence=evidence,
                            own_conclusion=first_round[role][0].conclusion,
                            own_confidence=first_round[role][0].confidence,
                            peer_positions=peer_view[role],
                        )
                        for role in roles
                    }
                    second_round = {
                        role: future.result() for role, future in revision_futures.items()
                    }

                for role in roles:
                    revision, _, response_hash = second_round[role]
                    revision_id = uuid4()
                    cited = sorted(
                        {
                            evidence_id
                            for claim in revision.claims
                            for evidence_id in claim.evidence_ids + claim.contradicting_evidence_ids
                        },
                        key=str,
                    )
                    cur.execute(
                        """INSERT INTO wnba.agent_analyses
                           (analysis_id,research_run_id,agent_role,conclusion,confidence,
                            risk_flags,evidence_ids,response_sha256,round,saw_peers,
                            revised_from,prior_confidence,revision_reason)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,2,true,%s,%s,%s)""",
                        (
                            revision_id,
                            run_id,
                            role,
                            revision.conclusion,
                            revision.revised_confidence,
                            Jsonb(revision.risk_flags),
                            cited,
                            response_hash,
                            analysis_ids[role],
                            first_round[role][0].confidence,
                            revision.revision_reason,
                        ),
                    )
                    analyses += 1
                    claims += _store_claims(
                        cur,
                        analysis_id=revision_id,
                        claims=revision.claims,
                        role=role,
                        subject_id=plan["player_id"],
                        at=at,
                        locks_at=locks_at,
                    )
                    positions[role] = AgentPosition(
                        role=role,
                        conclusion=revision.conclusion,
                        confidence=revision.revised_confidence,
                        round_number=2,
                        concessions=len(revision.concessions),
                        unresolved=len(revision.unresolved_disagreements),
                        risk_flags=tuple(revision.risk_flags),
                    )

            # ---- synthesis ---------------------------------------------------------------
            credibility = _credibility(cur)
            weighted_positions = tuple(
                AgentPosition(
                    role=position.role,
                    conclusion=position.conclusion,
                    confidence=position.confidence,
                    round_number=position.round_number,
                    credibility=credibility.get(position.role, 0.5),
                    concessions=position.concessions,
                    unresolved=position.unresolved,
                    risk_flags=position.risk_flags,
                )
                for position in positions.values()
            )
            cur.execute(
                """SELECT c.claim_id,cardinality(c.contradicting_evidence_ids) AS against
                   FROM wnba.research_claims c
                   JOIN wnba.agent_analyses a ON a.analysis_id=c.analysis_id
                   WHERE a.research_run_id=%s AND c.superseded_at IS NULL""",
                (run_id,),
            )
            supporting: list[UUID] = []
            contradicting: list[UUID] = []
            for row in cur.fetchall():
                target = contradicting if int(str(row["against"])) > 0 else supporting
                target.append(UUID(str(row["claim_id"])))

            blocked, policy_reasons = _policy_state(cur, plan["episode_id"])
            observed_at = plan["observed_at"]
            synthesis_inputs = SynthesisInputs(
                episode_id=UUID(str(plan["episode_id"])),
                model_probability=float(str(plan["predicted_probability"])),
                side=str(plan["side"]),
                prop_type=str(plan["prop_type"]),
                positions=weighted_positions,
                supporting_claim_ids=tuple(supporting),
                contradicting_claim_ids=tuple(contradicting),
                policy_blocked=blocked,
                policy_reasons=tuple(policy_reasons),
                quote_age_seconds=(
                    (at - observed_at).total_seconds()
                    if isinstance(observed_at, datetime)
                    else None
                ),
                audit_findings=tuple(report.context_for_agents()),
                precedents=precedent_set,
            )
            synthesis = synthesize(synthesis_inputs)
            record_synthesis(
                cur,
                research_run_id=run_id,
                inputs=synthesis_inputs,
                synthesis=synthesis,
                at=at,
            )

            completed = datetime.now(UTC)
            cur.execute(
                """UPDATE wnba.research_runs SET status='complete',completed_at=%s
                   WHERE research_run_id=%s""",
                (completed, run_id),
            )
            cur.execute(
                """UPDATE wnba.research_plans
                   SET status='complete',completed_at=%s,detail=%s WHERE plan_id=%s""",
                (completed, f"posture={synthesis.posture}", plan_id),
            )
        except Exception as exc:
            cur.execute(
                """UPDATE wnba.research_runs SET status='failed',completed_at=%s,error=%s
                   WHERE research_run_id=%s""",
                (datetime.now(UTC), str(exc)[:1000], run_id),
            )
            cur.execute(
                """UPDATE wnba.research_plans SET status='failed',completed_at=%s,detail=%s
                   WHERE plan_id=%s""",
                (datetime.now(UTC), str(exc)[:500], plan_id),
            )
            conn.commit()
            raise

    return ResearchBatch(
        research_run_id=run_id,
        analyses=analyses,
        claims=claims,
        evidence=len(evidence),
        posture=synthesis.posture,
    )


def refresh_due_claims(*, now: datetime | None = None) -> int:
    """Queue a re-investigation for every projection whose claims have gone stale.

    Separate from the coordinator's own ``claim_expiry`` trigger only in framing: this is the
    entry point a timer calls, and it delegates the actual decision so there is one place where
    "should we look at this again" is answered.
    """
    from wnba_services.research_agents.coordinator import plan_investigations

    at = now or datetime.now(UTC)
    return plan_investigations(now=at).planned


def run_projection_research(
    projection_id: UUID,
    *,
    client: DeepSeekResearchClient | None = None,
    now: datetime | None = None,
) -> ResearchBatch:
    """Investigate one projection on request, through the same path the coordinator uses.

    A manual request creates a plan and executes it rather than bypassing the machinery. An
    investigation somebody asked for should be audited, two-rounded and synthesised exactly like
    one the coordinator chose, and it should appear in the same record.
    """
    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT f.projection_id,f.expires_at AS locks_at,d.episode_id
               FROM wnba.stat_forecasts f
               JOIN wnba.decision_episodes d ON d.quote_id=f.quote_id
                 AND d.model_run_id=f.model_run_id
               WHERE f.projection_id=%s""",
            (projection_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown projection {projection_id}")
        locks_at = row["locks_at"]
        if not isinstance(locks_at, datetime):
            raise ValueError("projection lock time is invalid")
        if locks_at <= at:
            raise ValueError("research cannot run after the market locks")

        cur.execute(
            """SELECT plan_id FROM wnba.research_plans
               WHERE projection_id=%s AND status IN ('planned','auditing','running')""",
            (projection_id,),
        )
        open_plan = cur.fetchone()
        if open_plan is not None:
            plan_id = UUID(str(open_plan["plan_id"]))
        else:
            plan_id = uuid4()
            cur.execute(
                """INSERT INTO wnba.research_plans
                   (plan_id,projection_id,episode_id,planned_at,planned_by,trigger_kind,
                    trigger_detail,priority,roles,questions,reason,locks_at,status)
                   VALUES (%s,%s,%s,%s,'analyst','manual',%s,60,%s,%s,%s,%s,'planned')""",
                (
                    plan_id,
                    projection_id,
                    row["episode_id"],
                    at,
                    Jsonb({"requested": True}),
                    list(PLAN_ROLES["manual"]),
                    Jsonb({}),
                    "Requested directly by an analyst.",
                    locks_at,
                ),
            )
    return execute_plan(plan_id, client=client, now=at)
