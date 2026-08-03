"""ESPN WNBA schedule and complete final-box-score adapter.

ESPN's public site endpoint is undocumented, so payloads are archived before normalization and
schema changes fail closed. Unlike the rescued legacy rows, ESPN summaries include starter,
offensive/defensive rebounds, fouls, and plus/minus—the fields required for canonical outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from wnba_services.ingestion.http import PoliteClient

__all__ = ["ESPN_BASE", "EspnGame", "EspnPlayerLine", "EspnTeam", "EspnWnbaAdapter"]

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/wnba"


@dataclass(frozen=True)
class EspnTeam:
    source_id: str
    abbreviation: str
    full_name: str
    market: str


@dataclass(frozen=True)
class EspnGame:
    source_id: str
    scheduled_tipoff: datetime
    home: EspnTeam
    away: EspnTeam
    status: str
    period: int
    home_points: int | None
    away_points: int | None


@dataclass(frozen=True)
class EspnPlayerLine:
    source_player_id: str
    player_name: str
    team_source_id: str
    minutes: float
    started: bool
    points: int
    rebounds_offensive: int
    rebounds_defensive: int
    assists: int
    steals: int
    blocks: int
    turnovers: int
    personal_fouls: int
    field_goals_made: int
    field_goals_attempted: int
    three_pointers_made: int
    three_pointers_attempted: int
    free_throws_made: int
    free_throws_attempted: int
    plus_minus: int | None


def _integer(value: object, *, field: str) -> int:
    try:
        return int(str(value).replace("+", ""))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _number(value: object, *, field: str) -> float:
    try:
        return float(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _made_attempted(value: object, *, field: str) -> tuple[int, int]:
    try:
        made, attempted = str(value).split("-", maxsplit=1)
        return int(made), int(attempted)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"invalid event timestamp: {value!r}")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _stat(stats: list[object], positions: dict[str, int], name: str) -> object:
    return stats[positions[name]]


class EspnWnbaAdapter:
    def __init__(self, client: PoliteClient | None = None) -> None:
        self.client = client or PoliteClient(source="espn", min_interval_seconds=0.35)

    def fetch_scoreboard(self, game_date: date) -> dict[str, Any]:
        payload = self.client.get_json(
            f"{ESPN_BASE}/scoreboard", params={"dates": game_date.strftime("%Y%m%d")}
        )
        if not isinstance(payload, dict):
            raise ValueError("ESPN scoreboard was not an object")
        return payload

    def fetch_summary(self, event_id: str) -> dict[str, Any]:
        payload = self.client.get_json(f"{ESPN_BASE}/summary", params={"event": event_id})
        if not isinstance(payload, dict):
            raise ValueError("ESPN summary was not an object")
        return payload

    def parse_scoreboard(self, payload: dict[str, Any]) -> list[EspnGame]:
        games: list[EspnGame] = []
        for event in payload.get("events", []):
            competition = event["competitions"][0]
            competitors = competition["competitors"]
            home_raw = next(c for c in competitors if c["homeAway"] == "home")
            away_raw = next(c for c in competitors if c["homeAway"] == "away")
            home = self._team(home_raw["team"])
            away = self._team(away_raw["team"])
            state = str(event.get("status", {}).get("type", {}).get("state", "pre"))
            games.append(
                EspnGame(
                    source_id=str(event["id"]),
                    scheduled_tipoff=_timestamp(event["date"]),
                    home=home,
                    away=away,
                    status={"pre": "scheduled", "in": "live", "post": "final"}.get(
                        state, "scheduled"
                    ),
                    period=_integer(event.get("status", {}).get("period", 0), field="period"),
                    home_points=_integer(home_raw["score"], field="home score")
                    if home_raw.get("score") not in (None, "")
                    else None,
                    away_points=_integer(away_raw["score"], field="away score")
                    if away_raw.get("score") not in (None, "")
                    else None,
                )
            )
        return games

    @staticmethod
    def _team(raw: dict[str, Any]) -> EspnTeam:
        return EspnTeam(
            source_id=str(raw["id"]),
            abbreviation=str(raw["abbreviation"]).upper(),
            full_name=str(raw["displayName"]),
            market=str(raw.get("location") or raw["displayName"]),
        )

    def parse_summary(self, payload: dict[str, Any]) -> list[EspnPlayerLine]:
        output: list[EspnPlayerLine] = []
        for team_group in payload.get("boxscore", {}).get("players", []):
            team_source_id = str(team_group["team"]["id"])
            for statistics in team_group.get("statistics", []):
                keys = [str(key) for key in statistics.get("keys", [])]
                positions = {key: index for index, key in enumerate(keys)}
                required = {
                    "minutes",
                    "points",
                    "fieldGoalsMade-fieldGoalsAttempted",
                    "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                    "freeThrowsMade-freeThrowsAttempted",
                    "offensiveRebounds",
                    "defensiveRebounds",
                    "assists",
                    "turnovers",
                    "steals",
                    "blocks",
                    "fouls",
                }
                missing = required - positions.keys()
                if missing:
                    raise ValueError(f"ESPN box-score keys missing: {sorted(missing)}")
                for row in statistics.get("athletes", []):
                    if row.get("didNotPlay") or not row.get("stats"):
                        continue
                    stats = row["stats"]

                    fgm, fga = _made_attempted(
                        _stat(stats, positions, "fieldGoalsMade-fieldGoalsAttempted"),
                        field="field goals",
                    )
                    tpm, tpa = _made_attempted(
                        _stat(
                            stats,
                            positions,
                            "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
                        ),
                        field="three pointers",
                    )
                    ftm, fta = _made_attempted(
                        _stat(stats, positions, "freeThrowsMade-freeThrowsAttempted"),
                        field="free throws",
                    )
                    plus_minus = None
                    if "plusMinus" in positions and _stat(stats, positions, "plusMinus") not in (
                        None,
                        "",
                    ):
                        plus_minus = _integer(
                            _stat(stats, positions, "plusMinus"), field="plus/minus"
                        )
                    athlete = row["athlete"]
                    output.append(
                        EspnPlayerLine(
                            source_player_id=str(athlete["id"]),
                            player_name=str(athlete["displayName"]),
                            team_source_id=team_source_id,
                            minutes=_number(_stat(stats, positions, "minutes"), field="minutes"),
                            started=bool(row.get("starter", False)),
                            points=_integer(_stat(stats, positions, "points"), field="points"),
                            rebounds_offensive=_integer(
                                _stat(stats, positions, "offensiveRebounds"),
                                field="offensive rebounds",
                            ),
                            rebounds_defensive=_integer(
                                _stat(stats, positions, "defensiveRebounds"),
                                field="defensive rebounds",
                            ),
                            assists=_integer(_stat(stats, positions, "assists"), field="assists"),
                            steals=_integer(_stat(stats, positions, "steals"), field="steals"),
                            blocks=_integer(_stat(stats, positions, "blocks"), field="blocks"),
                            turnovers=_integer(
                                _stat(stats, positions, "turnovers"), field="turnovers"
                            ),
                            personal_fouls=_integer(
                                _stat(stats, positions, "fouls"), field="fouls"
                            ),
                            field_goals_made=fgm,
                            field_goals_attempted=fga,
                            three_pointers_made=tpm,
                            three_pointers_attempted=tpa,
                            free_throws_made=ftm,
                            free_throws_attempted=fta,
                            plus_minus=plus_minus,
                        )
                    )
        return output
