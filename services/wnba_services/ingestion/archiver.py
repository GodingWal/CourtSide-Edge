"""The line archiver.

The most time-critical component in the platform, and the least glamorous. It runs in
**record-only** mode: it forecasts nothing, recommends nothing, and has no opinion about any
line. It only writes down what the market said and exactly when we saw it.

That is the point. Historical player-prop odds cannot be bought retroactively at any price, so
every hour this is not running is an hour of evaluation data that no amount of later effort
can recover. It should be the first thing deployed and the last thing switched off -- and it
is deliberately built to keep working when every model downstream of it is broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from wnba_domain.identity import SourceName
from wnba_store.db import connect
from wnba_store.repositories import quarantine_payload, record_quotes, register_player

from wnba_services.ingestion.adapters.underdog import UnderdogAdapter
from wnba_services.ingestion.http import BotDefenceError, SourceUnavailableError

__all__ = ["ArchiveResult", "run_archiver"]


@dataclass
class ArchiveResult:
    source: SourceName
    started_at: datetime
    fetched: int = 0
    inserted: int = 0
    rejected: int = 0
    players_seen: int = 0
    quarantine_id: str | None = None
    error: str | None = None
    rejects: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> str:
        if self.error:
            return f"{self.source.value}: FAILED - {self.error}"
        return (
            f"{self.source.value}: fetched={self.fetched} inserted={self.inserted} "
            f"rejected={self.rejected} players={self.players_seen}"
        )


def run_archiver(database_url: str | None = None) -> ArchiveResult:
    """Poll Underdog once and append every changed WNBA quote to the archive.

    Failure is contained on purpose. A source outage, a schema change or a bot-defence wall
    must not take down the process -- it records the reason and returns, so the next scheduled
    run tries again. Unchanged source states are ignored by the database uniqueness rule;
    the source's ``updated_at`` timestamp, not our polling cadence, defines a market change.
    """
    started = datetime.now(UTC)
    result = ArchiveResult(source=SourceName.UNDERDOG, started_at=started)
    adapter = UnderdogAdapter()

    try:
        payload = adapter.fetch()
    except BotDefenceError as exc:
        # The source has declined automated access. Do not retry, do not work around it.
        result.error = f"bot defence: {exc}"
        return result
    except SourceUnavailableError as exc:
        result.error = str(exc)
        return result

    if not isinstance(payload, dict):
        result.error = f"unexpected payload type {type(payload).__name__}"
        return result

    parsed, rejects = adapter.parse(payload, observed_at=started)
    result.fetched = len(parsed)
    result.rejected = len(rejects)
    result.rejects = rejects[:20]

    if not parsed:
        result.error = "no WNBA lines in payload"
        with connect(database_url) as conn:
            result.quarantine_id = str(
                quarantine_payload(
                    conn,
                    source=SourceName.UNDERDOG,
                    payload={"keys": list(payload)[:20], "rejects": rejects[:20]},
                    errors=rejects[:20] or ["no WNBA lines present"],
                )
            )
            conn.commit()
        return result

    with connect(database_url) as conn:
        # Resolve every source player to a canonical id before writing any quote, so a
        # foreign-key failure cannot leave the archive half-written for this snapshot.
        canonical: dict[str, object] = {}
        for item in parsed:
            if item.source_player_id not in canonical:
                canonical[item.source_player_id] = register_player(
                    conn,
                    source=SourceName.UNDERDOG,
                    source_player_id=item.source_player_id,
                    display_name=item.source_player_name,
                    observed_at=started,
                )
        result.players_seen = len(canonical)

        quotes = [
            item.quote.model_copy(
                update={"player_id": canonical[item.source_player_id], "game_id": None}
            )
            for item in parsed
        ]
        result.inserted = record_quotes(conn, quotes)

        if rejects:
            quarantine_payload(
                conn,
                source=SourceName.UNDERDOG,
                payload={"rejected_lines": rejects[:50]},
                errors=rejects[:50],
            )
        conn.commit()

    return result
