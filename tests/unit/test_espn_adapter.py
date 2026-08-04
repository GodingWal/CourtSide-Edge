from __future__ import annotations

from wnba_services.ingestion.adapters.espn import EspnWnbaAdapter


def test_scoreboard_and_complete_box_score_parse() -> None:
    adapter = EspnWnbaAdapter()
    scoreboard = {
        "events": [
            {
                "id": "401857107",
                "date": "2026-08-02T19:00Z",
                "status": {"period": 4, "type": {"state": "post"}},
                "competitions": [
                    {
                        "competitors": [
                            {
                                "homeAway": "home",
                                "score": "80",
                                "team": {
                                    "id": "16",
                                    "abbreviation": "MIN",
                                    "displayName": "Minnesota Lynx",
                                    "location": "Minnesota",
                                },
                            },
                            {
                                "homeAway": "away",
                                "score": "78",
                                "team": {
                                    "id": "5",
                                    "abbreviation": "IND",
                                    "displayName": "Indiana Fever",
                                    "location": "Indiana",
                                },
                            },
                        ]
                    }
                ],
            }
        ]
    }
    games = adapter.parse_scoreboard(scoreboard)
    assert len(games) == 1
    assert games[0].status == "final"
    assert games[0].home_points == 80

    keys = [
        "minutes",
        "points",
        "fieldGoalsMade-fieldGoalsAttempted",
        "threePointFieldGoalsMade-threePointFieldGoalsAttempted",
        "freeThrowsMade-freeThrowsAttempted",
        "rebounds",
        "assists",
        "turnovers",
        "steals",
        "blocks",
        "offensiveRebounds",
        "defensiveRebounds",
        "fouls",
        "plusMinus",
    ]
    summary = {
        "boxscore": {
            "players": [
                {
                    "team": {"id": "16"},
                    "statistics": [
                        {
                            "keys": keys,
                            "athletes": [
                                {
                                    "starter": True,
                                    "didNotPlay": False,
                                    "athlete": {"id": "3142255", "displayName": "Example Player"},
                                    "stats": [
                                        "30",
                                        "13",
                                        "5-11",
                                        "1-4",
                                        "2-2",
                                        "5",
                                        "3",
                                        "2",
                                        "1",
                                        "0",
                                        "1",
                                        "4",
                                        "3",
                                        "+7",
                                    ],
                                },
                                {
                                    "starter": False,
                                    "didNotPlay": True,
                                    "athlete": {"id": "2", "displayName": "DNP Player"},
                                    "stats": [],
                                },
                                {
                                    "starter": False,
                                    "didNotPlay": False,
                                    "athlete": {
                                        "id": "3",
                                        "displayName": "Placeholder DNP",
                                    },
                                    "stats": [
                                        "--",
                                        "0",
                                        "0-0",
                                        "0-0",
                                        "0-0",
                                        "0",
                                        "0",
                                        "0",
                                        "0",
                                        "0",
                                        "0",
                                        "0",
                                        "0",
                                        "0",
                                    ],
                                },
                            ],
                        }
                    ],
                }
            ]
        }
    }
    lines = adapter.parse_summary(summary)
    assert len(lines) == 1
    line = lines[0]
    assert line.started is True
    assert line.rebounds_offensive == 1
    assert line.rebounds_defensive == 4
    assert line.personal_fouls == 3
    assert line.plus_minus == 7


def test_box_score_schema_change_fails_closed() -> None:
    adapter = EspnWnbaAdapter()
    summary = {
        "boxscore": {
            "players": [
                {"team": {"id": "16"}, "statistics": [{"keys": ["points"], "athletes": []}]}
            ]
        }
    }
    try:
        adapter.parse_summary(summary)
    except ValueError as exc:
        assert "keys missing" in str(exc)
    else:  # pragma: no cover - explicit failure message is more useful than a bare assert
        raise AssertionError("schema drift was silently accepted")


def test_a_date_with_live_games_is_never_marked_complete() -> None:
    """Regression: a partially-played date must stay open for re-ingest.

    Recording completion while games are still in progress permanently strands them. The
    ledger check skips the date on every future run, so those games never go final, never
    settle, and the entire learning loop starves behind them showing empty panels.

    This happened in production: a manual ingest just after midnight, with two games still in
    the fourth quarter, found zero finals and closed the date. The daily job skipped it
    indefinitely until the games were force-re-ingested.
    """
    from wnba_services.ingestion.espn import _date_is_settled

    live = [("final", "ATL", "LV"), ("in", "NY", "SEA")]
    done = [("final", "ATL", "LV"), ("final", "NY", "SEA")]

    assert _date_is_settled([s for s, _, _ in done]) is True
    assert _date_is_settled([s for s, _, _ in live]) is False, (
        "a date with a live game must remain open so the finished games can be re-ingested"
    )
    assert _date_is_settled([]) is False, (
        "an empty scoreboard means ESPN has not published yet -- not that the day is done"
    )
