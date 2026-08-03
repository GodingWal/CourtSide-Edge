"""Audited cross-source player identity review."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from wnba_store.db import connect


@dataclass(frozen=True)
class IdentityApproval:
    approved: int
    ambiguous: int


def approve_unique_exact_names(*, verified_by: str) -> IdentityApproval:
    """Record unique normalized-name pairs after an operator explicitly requests review."""
    if not verified_by.strip():
        raise ValueError("verified_by is required")
    approved = ambiguous = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT u.player_id AS from_id, min(e.player_id::text)::uuid AS to_id,
                      count(DISTINCT e.player_id) AS candidates, u.normalized_name
               FROM wnba.player_aliases u
               JOIN wnba.player_aliases e ON e.source='espn' AND e.system_to IS NULL
                 AND e.normalized_name=u.normalized_name
               WHERE u.source='underdog' AND u.system_to IS NULL
               GROUP BY u.player_id,u.normalized_name"""
        )
        for row in cur.fetchall():
            if int(str(row["candidates"])) != 1:
                ambiguous += 1
                continue
            cur.execute(
                """INSERT INTO wnba.player_merges
                   (merge_id,from_player_id,to_player_id,reason,verified_by)
                   VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (
                    uuid4(),
                    UUID(str(row["from_id"])),
                    UUID(str(row["to_id"])),
                    f"unique exact normalized name: {row['normalized_name']}",
                    verified_by,
                ),
            )
            approved += max(0, cur.rowcount)
    return IdentityApproval(approved, ambiguous)
