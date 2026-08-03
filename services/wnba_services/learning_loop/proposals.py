"""Generate conservative research proposals from repeated measured errors."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from wnba_store.db import connect


@dataclass(frozen=True)
class ProposalBatch:
    proposed: int
    categories_reviewed: int


def generate_research_proposals(*, now: datetime | None = None) -> ProposalBatch:
    """Create testable proposals; never approve or implement them automatically."""
    at = now or datetime.now(UTC)
    proposed = reviewed = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT a.primary_error,
                      array_agg(a.episode_id ORDER BY a.attributed_at DESC) episodes,
                      count(*) AS failures,array_agg(DISTINCT d.prop_type) markets
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
            estimated_reduction = min(0.3, 0.05 + failures / 1000)
            cur.execute(
                """INSERT INTO wnba.research_proposals
                   (proposal_id,title,proposed_at,proposed_by,motivating_episode_ids,
                    estimated_value,estimated_failure_reduction,implementation_cost,
                    affected_markets,experiment_design,error_category)
                   VALUES (%s,%s,%s,'research_director',%s,%s,%s,'medium',%s,%s,%s)""",
                (
                    uuid4(),
                    f"Reduce repeated {category.replace('_', ' ')} errors",
                    at,
                    episodes,
                    min(0.9, 0.5 + failures / 500),
                    estimated_reduction,
                    markets,
                    (
                        "Create a shadow challenger targeting this error category. Declare Brier "
                        "score as the primary metric, run rolling-origin evaluation, and reject "
                        "the challenger if any major market subgroup materially degrades."
                    ),
                    category,
                ),
            )
            proposed += 1
    return ProposalBatch(proposed, reviewed)
