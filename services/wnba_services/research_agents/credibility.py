"""Who has earned trust, about what -- measured rather than assumed.

Three scores, all computed the same way and all pooled toward ignorance by sample size:

``agent_credibility``
    Per role *and domain*. An analyst can be reliable about rotations and useless about pace, and
    a single number averaging those describes neither. Scored on whether the agent's stated
    confidence matched what happened to the decisions its claims attached to -- which is a
    calibration question, not an accuracy one. An analyst who says 0.6 and is right 60% of the
    time is doing its job perfectly.

``evidence_rankings``
    Which evidence analysts actually found useful, from the labels they leave on episodes. This
    is the feedback edge that was specified and never built: ``analyst_feedback`` has carried
    ``evidence_ids_useful`` and ``evidence_ids_misleading`` since migration 018 and nothing has
    ever read them, so the retrieval order has been fixed since the day it was written.

``source_reliability``
    Per source and domain, from the same labels plus how often a source's evidence ends up
    contradicted.

THE POOLING RULE
----------------
Every score is ``(successes + k*prior) / (trials + k)``. With no evidence a score is exactly the
prior, and it takes real volume to move. This matters more here than in most places: these
numbers weight future decisions, so a role that got lucky twice in its first week must not
arrive at 1.0 credibility and start dominating the agreement statistic. The pooling constant is
deliberately large relative to the volume this system produces.

WHAT CREDIBILITY DOES NOT DO
----------------------------
It weights confidences in the agreement statistic. It does not gate claims, silence roles, or
promote one agent's conclusion over another's. A cited claim is evidence regardless of who found
it, and a good track record is not a reason to stop reading the citation. Nothing in this module
writes to a forecast, a rule, or a recommendation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb
from wnba_store.db import connect

__all__ = [
    "CREDIBILITY_PRIOR",
    "POOLING_STRENGTH",
    "CredibilityBatch",
    "expected_calibration",
    "pooled_score",
    "score_research_credibility",
]

# Everything starts at "no reason to trust or distrust". 0.5 rather than a flattering default,
# because an unmeasured agent has earned nothing.
CREDIBILITY_PRIOR = 0.5

# Observations required before a score reaches halfway to its raw value. Large on purpose: this
# system settles tens of decisions a week, and a score that swings on five of them would make the
# agreement statistic a random number generator with a good story.
POOLING_STRENGTH = 20.0

# Minimum observations before a score is reported as anything but provisional.
MINIMUM_SAMPLE = 10


@dataclass(frozen=True)
class CredibilityBatch:
    agent_scores: int
    evidence_ranked: int
    source_scores: int
    claims_scored: int


def pooled_score(
    successes: float,
    trials: float,
    *,
    prior: float = CREDIBILITY_PRIOR,
    strength: float = POOLING_STRENGTH,
) -> float:
    """Partial pooling toward the prior. Zero trials returns the prior exactly."""
    if trials <= 0:
        return prior
    return (successes + strength * prior) / (trials + strength)


def expected_calibration(pairs: Sequence[tuple[float, bool]]) -> float | None:
    """Mean absolute gap between stated confidence and observed frequency, in ten buckets.

    Returned as a *gap*, so lower is better. Callers convert to a credibility by subtracting from
    one; keeping the raw gap here means the number stored alongside is the diagnostic rather than
    a score whose direction has to be remembered.
    """
    if not pairs:
        return None
    buckets: dict[int, list[tuple[float, bool]]] = {}
    for confidence, outcome in pairs:
        buckets.setdefault(min(9, int(confidence * 10)), []).append((confidence, outcome))
    return math.fsum(
        len(values)
        / len(pairs)
        * abs(
            math.fsum(value[0] for value in values) / len(values)
            - math.fsum(float(value[1]) for value in values) / len(values)
        )
        for values in buckets.values()
    )


def _score_agents(cur: Any, at: datetime) -> tuple[int, int]:
    """Credibility per (role, domain) from claims whose episodes have settled.

    A claim is 'right' if the decision it was attached to landed. That is a blunt instrument --
    an availability analyst can be entirely correct about a player's status on a prop that missed
    for unrelated reasons -- which is exactly why the score is calibration-based and heavily
    pooled rather than treated as an accuracy percentage. It is a weak signal handled as one.
    """
    cur.execute(
        """SELECT a.agent_role,coalesce(c.domain,'unassigned') AS domain,
                  c.confidence,o.hit
           FROM wnba.research_claims c
           JOIN wnba.agent_analyses a ON a.analysis_id=c.analysis_id
           JOIN wnba.research_runs r ON r.research_run_id=a.research_run_id
           JOIN wnba.stat_forecasts f ON f.projection_id=r.projection_id
           JOIN wnba.decision_episodes d ON d.quote_id=f.quote_id
             AND d.model_run_id=f.model_run_id
           JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
           WHERE NOT o.was_voided AND NOT o.was_push"""
    )
    grouped: dict[tuple[str, str], list[tuple[float, bool]]] = {}
    for row in cur.fetchall():
        key = (str(row["agent_role"]), str(row["domain"]))
        grouped.setdefault(key, []).append((float(str(row["confidence"])), bool(row["hit"])))

    written = scored = 0
    for (role, domain), pairs in sorted(grouped.items()):
        gap = expected_calibration(pairs)
        successes = math.fsum(float(hit) for _, hit in pairs)
        accuracy = pooled_score(successes, len(pairs))
        # Credibility is mostly calibration, with a light accuracy term. A perfectly calibrated
        # analyst who only ever says 0.5 is honest and not very useful; the accuracy term is what
        # separates it from one that is calibrated *and* discriminating.
        calibration_component = 1.0 if gap is None else max(0.0, 1.0 - gap * 2.0)
        raw = 0.7 * calibration_component + 0.3 * accuracy
        credibility = pooled_score(raw * len(pairs), len(pairs))
        cur.execute(
            """INSERT INTO wnba.agent_credibility
               (agent_role,domain,sample_size,calibration,evidence_accuracy,credibility,
                calculated_at,claims_scored,method,detail)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT DO NOTHING""",
            (
                role,
                domain,
                len(pairs),
                gap,
                accuracy,
                credibility,
                at,
                len(pairs),
                "calibration-weighted, pooled",
                Jsonb(
                    {
                        "pooling_strength": POOLING_STRENGTH,
                        "prior": CREDIBILITY_PRIOR,
                        "calibration_gap": None if gap is None else round(gap, 4),
                        "provisional": len(pairs) < MINIMUM_SAMPLE,
                        "weights_confidences_only": True,
                    }
                ),
            ),
        )
        written += 1
        scored += len(pairs)
    return written, scored


def _rank_evidence(cur: Any, at: datetime) -> int:
    """Turn analyst usefulness labels into a retrieval ranking.

    The join is deliberately over *every* piece of evidence any analysis cited, not only labelled
    ones: evidence that has been cited thirty times and never called misleading has earned
    something, and restricting to labelled rows would rank only the evidence somebody happened to
    have an opinion about.
    """
    cur.execute(
        """WITH citations AS (
               SELECT unnest(a.evidence_ids) AS evidence_id
               FROM wnba.agent_analyses a
           ), useful AS (
               SELECT unnest(fb.evidence_ids_useful) AS evidence_id FROM wnba.analyst_feedback fb
           ), misleading AS (
               SELECT unnest(fb.evidence_ids_misleading) AS evidence_id
               FROM wnba.analyst_feedback fb
           )
           SELECT e.evidence_id,
                  (SELECT count(*) FROM citations c WHERE c.evidence_id=e.evidence_id) AS cited,
                  (SELECT count(*) FROM useful u WHERE u.evidence_id=e.evidence_id) AS useful,
                  (SELECT count(*) FROM misleading m WHERE m.evidence_id=e.evidence_id) AS bad
           FROM wnba.evidence e"""
    )
    rows = [dict(row) for row in cur.fetchall()]
    written = 0
    for row in rows:
        cited = int(str(row["cited"]))
        useful = int(str(row["useful"]))
        bad = int(str(row["bad"]))
        labels = useful + bad
        if cited == 0 and labels == 0:
            continue
        # Labels dominate; citation count contributes a weak positive only when nobody has
        # objected. Citation is a measure of what retrieval already surfaced, so letting it drive
        # the ranking would make the ordering self-reinforcing.
        usefulness = pooled_score(useful + (0.25 * cited if bad == 0 else 0.0), labels + cited)
        cur.execute(
            """INSERT INTO wnba.evidence_rankings
               (evidence_id,calculated_at,useful_labels,misleading_labels,citation_count,
                usefulness,sample_size)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (evidence_id) DO UPDATE SET
                 calculated_at=EXCLUDED.calculated_at,
                 useful_labels=EXCLUDED.useful_labels,
                 misleading_labels=EXCLUDED.misleading_labels,
                 citation_count=EXCLUDED.citation_count,
                 usefulness=EXCLUDED.usefulness,
                 sample_size=EXCLUDED.sample_size""",
            (row["evidence_id"], at, useful, bad, cited, usefulness, labels),
        )
        written += 1
    return written


def _score_sources(cur: Any, at: datetime) -> int:
    """Reliability per source and domain, from labels and contradiction rate."""
    cur.execute(
        """SELECT d.source,coalesce(c.domain,'unassigned') AS domain,
                  count(DISTINCT d.document_id) AS documents,
                  count(*) AS citations,
                  count(*) FILTER (
                      WHERE cardinality(c.contradicting_evidence_ids)>0) AS contradicted,
                  coalesce(sum((
                      SELECT count(*) FROM wnba.analyst_feedback fb
                      WHERE e.evidence_id = ANY(fb.evidence_ids_useful))),0) AS useful,
                  coalesce(sum((
                      SELECT count(*) FROM wnba.analyst_feedback fb
                      WHERE e.evidence_id = ANY(fb.evidence_ids_misleading))),0) AS misleading
           FROM wnba.research_claims c
           JOIN wnba.evidence e ON e.evidence_id = ANY(c.evidence_ids)
           JOIN wnba.source_documents d ON d.document_id=e.document_id
           GROUP BY d.source,coalesce(c.domain,'unassigned')"""
    )
    written = 0
    for raw in cur.fetchall():
        row = dict(raw)
        citations = int(str(row["citations"]))
        contradicted = int(str(row["contradicted"]))
        useful = int(str(row["useful"]))
        misleading = int(str(row["misleading"]))
        contradiction_rate = contradicted / citations if citations else None
        labels = useful + misleading
        base = pooled_score(useful, labels)
        # A source whose evidence keeps ending up contradicted is unreliable regardless of how
        # anybody felt about it at the time.
        penalty = 0.0 if contradiction_rate is None else min(0.4, contradiction_rate * 0.5)
        reliability = max(0.0, min(1.0, base - penalty))
        cur.execute(
            """INSERT INTO wnba.source_reliability
               (source,domain,calculated_at,documents,citations,useful_labels,misleading_labels,
                contradiction_rate,reliability,sample_size)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
            (
                str(row["source"]),
                str(row["domain"]),
                at,
                int(str(row["documents"])),
                citations,
                useful,
                misleading,
                contradiction_rate,
                reliability,
                labels,
            ),
        )
        written += 1
    return written


def score_research_credibility(*, now: datetime | None = None) -> CredibilityBatch:
    """Refresh every trust score from settled outcomes and analyst labels.

    Appends rather than overwrites for agents and sources -- the history of what a role's
    credibility *was* is how anybody later checks whether the score moved for a reason. Evidence
    rankings are upserted because retrieval reads exactly one current value per row and keeping
    the series would trade a large table for a question nobody asks.
    """
    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        agent_scores, claims_scored = _score_agents(cur, at)
        evidence_ranked = _rank_evidence(cur, at)
        source_scores = _score_sources(cur, at)
    return CredibilityBatch(
        agent_scores=agent_scores,
        evidence_ranked=evidence_ranked,
        source_scores=source_scores,
        claims_scored=claims_scored,
    )


def evidence_ranking(cur: Any, evidence_ids: Sequence[UUID]) -> dict[UUID, float]:
    """Current usefulness for a set of evidence ids, defaulting to the prior when unscored."""
    if not evidence_ids:
        return {}
    cur.execute(
        "SELECT evidence_id,usefulness FROM wnba.evidence_rankings WHERE evidence_id = ANY(%s)",
        (list(evidence_ids),),
    )
    scored = {
        UUID(str(row["evidence_id"])): float(str(row["usefulness"])) for row in cur.fetchall()
    }
    return {evidence_id: scored.get(evidence_id, CREDIBILITY_PRIOR) for evidence_id in evidence_ids}
