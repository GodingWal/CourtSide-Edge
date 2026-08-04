"""Append-only paper forecast settlement and proper probability scoring."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from wnba_domain.decision import brier_score, log_loss
from wnba_store.db import connect

STAT_COLUMNS: dict[str, tuple[str, ...]] = {
    "points": ("points",),
    "rebounds": ("rebounds_offensive", "rebounds_defensive"),
    "assists": ("assists",),
    "three_pointers": ("three_pointers_made",),
    "steals": ("steals",),
    "blocks": ("blocks",),
    "turnovers": ("turnovers",),
    "points_rebounds_assists": (
        "points",
        "rebounds_offensive",
        "rebounds_defensive",
        "assists",
    ),
    "points_rebounds": ("points", "rebounds_offensive", "rebounds_defensive"),
    "points_assists": ("points", "assists"),
    "rebounds_assists": ("rebounds_offensive", "rebounds_defensive", "assists"),
    "steals_blocks": ("steals", "blocks"),
}


@dataclass(frozen=True)
class SettlementBatch:
    settled: int
    voided: int
    pushed: int
    unsupported: int


@dataclass(frozen=True)
class Resolution:
    """How one forecast resolved against reality."""

    voided: bool
    pushed: bool
    hit: bool

    @property
    def is_scoreable(self) -> bool:
        """Only a resolved win/loss carries information about forecast quality.

        Voids and pushes are excluded from Brier and log loss entirely. Scoring a void as a
        loss would punish the model for a coach's rotation decision, and scoring a push as
        either outcome invents information that does not exist.
        """
        return not self.voided and not self.pushed


def resolve_outcome(
    *, side: str, actual: float, forecast_line: float, minutes: float | None
) -> Resolution:
    """Decide void / push / hit for a single settled forecast.

    Extracted as a pure function because everything the platform concludes -- calibration,
    readiness, model evaluation, error attribution -- sits downstream of this decision. A sign
    error or an inverted comparison here would corrupt every one of them while continuing to
    look entirely plausible.

    A player who did not appear (no box-score row, or zero minutes) **voids**. That is not the
    same as losing: the forecast was never given a chance to be right or wrong, and counting
    it as a loss would blame the model for a scratch it could not have known about.
    """
    if minutes is None or minutes == 0.0:
        return Resolution(voided=True, pushed=False, hit=False)
    if actual == forecast_line:
        return Resolution(voided=False, pushed=True, hit=False)
    beat_the_line = actual > forecast_line if side == "over" else actual < forecast_line
    return Resolution(voided=False, pushed=False, hit=beat_the_line)


def actual_stat(line: dict[str, Any], prop_type: str) -> float:
    columns = STAT_COLUMNS.get(prop_type)
    if columns is None:
        raise ValueError(f"unsupported settlement market {prop_type!r}")
    return sum(float(str(line[column])) for column in columns)


def settle_paper_episodes(*, now: datetime | None = None) -> SettlementBatch:
    """Settle every unresolved episode whose canonical game is final.

    Missing player lines are voided rather than treated as losses. Closing line is the final
    archived observation available no later than lock for the same source market.
    """
    at = now or datetime.now(UTC)
    settled = voided = pushed = unsupported = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.*,oq.player_id AS quote_player_id,oq.locks_at
               FROM wnba.decision_episodes d
               JOIN wnba.games g ON g.game_id=d.game_id AND g.status='final'
               JOIN wnba.prop_quotes oq ON oq.quote_id=d.quote_id
               LEFT JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               WHERE d.is_paper AND o.episode_id IS NULL
               ORDER BY d.forecast_timestamp"""
        )
        for episode in cur.fetchall():
            columns = STAT_COLUMNS.get(str(episode["prop_type"]))
            if columns is None:
                unsupported += 1
                continue
            cur.execute(
                """SELECT * FROM wnba.player_game_lines
                   WHERE player_id=%s AND game_id=%s AND system_to IS NULL""",
                (episode["player_id"], episode["game_id"]),
            )
            line = cur.fetchone()
            value = 0.0 if line is None else actual_stat(line, str(episode["prop_type"]))
            resolution = resolve_outcome(
                side=str(episode["side"]),
                actual=value,
                forecast_line=float(str(episode["line"])),
                minutes=None if line is None else float(str(line["minutes"])),
            )
            is_void, is_push, hit = resolution.voided, resolution.pushed, resolution.hit
            cur.execute(
                """SELECT line,coalesce(over_multiplier,under_multiplier) AS multiplier
                   FROM wnba.prop_quotes
                   WHERE source=%s AND player_id=%s AND game_id=%s AND prop_type=%s
                     AND system_from<=coalesce(%s,system_from)
                   ORDER BY system_from DESC LIMIT 1""",
                (
                    episode["source"],
                    episode["quote_player_id"],
                    episode["game_id"],
                    episode["prop_type"],
                    episode["locks_at"],
                ),
            )
            closing = cur.fetchone()
            probability = float(str(episode["predicted_probability"]))
            score_brier = None if is_void or is_push else brier_score(probability, int(hit))
            score_log = None if is_void or is_push else log_loss(probability, int(hit))
            cur.execute(
                """INSERT INTO wnba.episode_outcomes
                   (episode_id,settled_at,actual_stat,actual_minutes,did_play,did_start,hit,
                    was_push,was_voided,closing_line,closing_multiplier,brier,log_loss)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    episode["episode_id"],
                    at,
                    value,
                    0.0 if line is None else line["minutes"],
                    not is_void,
                    False if line is None else line["started"],
                    hit,
                    is_push,
                    is_void,
                    None if closing is None else closing["line"],
                    None if closing is None else closing["multiplier"],
                    score_brier,
                    score_log,
                ),
            )
            cur.execute(
                """INSERT INTO wnba.ontology_actions
                   (action_id,action_type,actor,subject_id,previous_state,new_state,reason,
                    is_automated)
                   VALUES (%s,'settle_episode','settlement-service',%s,'unsettled',%s,%s,true)""",
                (
                    uuid4(),
                    episode["episode_id"],
                    "voided" if is_void else "settled",
                    "official canonical final box score available",
                ),
            )
            settled += 1
            voided += int(is_void)
            pushed += int(is_push)
    return SettlementBatch(settled, voided, pushed, unsupported)
