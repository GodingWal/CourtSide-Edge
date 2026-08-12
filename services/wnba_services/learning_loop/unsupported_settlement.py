"""Terminally void historical markets the canonical box score cannot score."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from wnba_store.db import connect

from wnba_services.learning_loop.settlement import STAT_COLUMNS

__all__ = ["UnsupportedSettlementBatch", "void_unsupported_episodes"]


@dataclass(frozen=True)
class UnsupportedSettlementBatch:
    voided: int


def void_unsupported_episodes(
    *, now: datetime | None = None, limit: int = 10_000
) -> UnsupportedSettlementBatch:
    """Void unsupported final-game episodes without inventing a stat mapping."""
    at = now or datetime.now(UTC)
    supported = sorted(STAT_COLUMNS)
    voided = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.episode_id,d.prop_type
               FROM wnba.decision_episodes d
               JOIN wnba.games g ON g.game_id=d.game_id AND g.status='final'
               LEFT JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               WHERE d.is_paper AND o.episode_id IS NULL
                 AND NOT (d.prop_type=ANY(%s))
               ORDER BY d.forecast_timestamp
               LIMIT %s""",
            (supported, limit),
        )
        for episode in cur.fetchall():
            cur.execute(
                """INSERT INTO wnba.episode_outcomes
                   (episode_id,settled_at,actual_stat,actual_minutes,did_play,did_start,hit,
                    was_push,was_voided,closing_line,closing_multiplier,brier,log_loss)
                   VALUES (%s,%s,0,0,false,false,false,false,true,NULL,NULL,NULL,NULL)
                   ON CONFLICT (episode_id) DO NOTHING""",
                (episode["episode_id"], at),
            )
            if cur.rowcount == 0:
                continue
            cur.execute(
                """INSERT INTO wnba.ontology_actions
                   (action_id,action_type,actor,subject_id,previous_state,new_state,reason,
                    is_automated)
                   VALUES (%s,'settle_episode','settlement-service',%s,'unsettled','voided',
                           %s,true)""",
                (
                    uuid4(),
                    episode["episode_id"],
                    f"unsupported settlement market: {episode['prop_type']}",
                ),
            )
            voided += 1
    return UnsupportedSettlementBatch(voided)
