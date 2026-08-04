"""The loop, end to end: a quote becomes a forecast, a decision, and eventually a score.

Every stage of this pipeline had unit tests and the pipeline itself had none, which left the
most expensive failure mode uncovered: the one where every component works, reports success, and
the loop produces nothing. That had already happened three times in production -- role projection
returning ``projected=0 skipped=9``, the forecaster returning ``forecasts=0 skipped=231`` -- and
each was found by a human noticing an empty screen rather than by a test.

These tests exercise the real functions against a scripted database. They are not a substitute
for running PostgreSQL in CI, and they cannot catch a query that is subtly wrong. What they do
catch is a stage that silently skips its entire input, a decision written without the evidence
that produced it, and a settlement that scores something it should have voided -- which is the
class of bug that actually occurred.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from wnba_domain.enums import RecommendationStatus
from wnba_services.forecasting import baseline
from wnba_services.forecasting.baseline import run_baseline
from wnba_services.learning_loop import settlement
from wnba_services.learning_loop.settlement import settle_paper_episodes

from tests.fixtures.fake_db import FakeDatabase

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)
LOCKS_AT = NOW + timedelta(hours=6)
GAME_ID = UUID("22222222-2222-2222-2222-222222222222")
PLAYER_ID = UUID("33333333-3333-3333-3333-333333333333")
QUOTE_ID = UUID("44444444-4444-4444-4444-444444444444")
TEAM_ID = UUID("55555555-5555-5555-5555-555555555555")


def _quote(**overrides: Any) -> dict[str, Any]:
    row = {
        "quote_id": QUOTE_ID,
        "source": "underdog",
        "source_quote_id": "ud-1",
        "player_id": PLAYER_ID,
        "canonical_player_id": PLAYER_ID,
        "game_id": GAME_ID,
        "season_year": 2026,
        "prop_type": "points",
        "line": Decimal("15.5"),
        "market_kind": "pickem",
        "locks_at": LOCKS_AT,
        "over_american_odds": None,
        "under_american_odds": None,
        "over_multiplier": Decimal("1.00"),
        "under_multiplier": Decimal("1.00"),
    }
    row.update(overrides)
    return row


def _history(count: int = 20) -> list[dict[str, Any]]:
    """A steady 30-minute starter scoring in the mid-teens."""
    return [
        {
            "line_id": uuid4(),
            "player_id": PLAYER_ID,
            "team_id": TEAM_ID,
            "game_id": uuid4(),
            "minutes": Decimal("30.0"),
            "started": True,
            "points": 15 + (index % 5),
            "rebounds_offensive": 1,
            "rebounds_defensive": 4,
            "assists": 3,
            "three_pointers_made": 2,
            "three_pointers_attempted": 5,
            "field_goals_attempted": 12,
            "free_throws_attempted": 3,
            "steals": 1,
            "blocks": 0,
            "turnovers": 2,
            "scheduled_tipoff": NOW - timedelta(days=count - index),
        }
        for index in range(count)
    ]


def _forecast_database(
    *,
    history: list[dict[str, Any]] | None = None,
    quotes: list[dict[str, Any]] | None = None,
    prior_season: list[dict[str, Any]] | None = None,
) -> FakeDatabase:
    """Script the four reads the forecaster makes per quote.

    Ordering matters: three of these queries select from the same two tables, so the most
    specific matcher has to be registered first. The prior-season aggregate is distinguished by
    its ``season_year`` bound and the league aggregate by summing minutes, which leaves the
    plain ``SELECT l.*`` history read as the only one starting that way.
    """
    database = FakeDatabase()
    database.when(
        "DISTINCT ON (q.source,q.source_quote_id)", quotes if quotes is not None else [_quote()]
    )
    database.when("g.season_year<%s", prior_season or [])
    database.when(
        "AS total_minutes",
        [{"total_stat": Decimal("100000"), "total_minutes": Decimal("200000")}],
    )
    database.when("SELECT l.team_id FROM wnba.player_game_lines", [{"team_id": TEAM_ID}])
    database.when(
        "SELECT l.*,g.scheduled_tipoff FROM wnba.player_game_lines", history or _history()
    )
    return database


# --------------------------------------------------------------------------------------
# Quote -> forecast -> decision
# --------------------------------------------------------------------------------------
def test_a_quoted_market_produces_a_forecast_and_a_paper_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    batch = run_baseline(now=NOW)

    assert batch.forecasts == 1
    assert batch.episodes == 1
    assert batch.skipped == 0
    assert database.ran("INSERT INTO wnba.stat_forecasts")
    assert database.ran("INSERT INTO wnba.decision_episodes")


def test_the_stages_run_in_an_order_that_preserves_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A decision must reference a snapshot and a forecast that already exist."""
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    run_baseline(now=NOW)

    version, run, snapshot, forecast, episode = database.order_of(
        "INSERT INTO wnba.model_versions",
        "INSERT INTO wnba.model_runs",
        "INSERT INTO wnba.feature_snapshots",
        "INSERT INTO wnba.stat_forecasts",
        "INSERT INTO wnba.decision_episodes",
    )
    assert version < run < snapshot < forecast < episode


def test_every_component_of_the_ensemble_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Component-level scoring, and therefore weight fitting, depends on these rows existing."""
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    run_baseline(now=NOW)

    assert database.count("INSERT INTO wnba.forecast_components") == 5


def test_the_episode_records_the_numbers_the_gate_actually_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    run_baseline(now=NOW)

    statement, params = database.executed("INSERT INTO wnba.decision_episodes")[0]
    assert "shrunk_probability" in statement
    assert "breakeven_probability" in statement
    assert "decision_reason" in statement
    assert params[-1]  # a reason is always given
    assert 0.0 <= float(params[-3]) <= 1.0  # the shrunk probability
    assert 0.0 <= float(params[-2]) <= 1.0  # the break-even it was compared against


def test_the_forecast_keeps_both_the_raw_and_calibrated_probability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    run_baseline(now=NOW)

    statement, _ = database.executed("INSERT INTO wnba.stat_forecasts")[0]
    assert "probability_over_raw" in statement
    assert "dispersion" in statement
    assert "scorer_version" in statement


def test_the_run_is_marked_complete_with_a_countable_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A run that ends without this looks identical to one that crashed halfway."""
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    run_baseline(now=NOW)

    _, params = database.executed("UPDATE wnba.model_runs SET completed_at")[0]
    assert "forecasts=1" in str(params[1])
    assert "candidates=" in str(params[1])


# --------------------------------------------------------------------------------------
# The skip paths, which is where the silent failures lived
# --------------------------------------------------------------------------------------
def test_a_player_with_too_little_history_is_skipped_not_guessed_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _forecast_database(history=_history(4))
    monkeypatch.setattr(baseline, "connect", database.connect)

    batch = run_baseline(now=NOW)

    assert batch.forecasts == 0
    assert batch.skipped == 1
    assert not database.ran("INSERT INTO wnba.stat_forecasts")


def test_an_unsupported_market_is_skipped_without_stopping_the_board(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _forecast_database(
        quotes=[_quote(prop_type="double_doubles", source_quote_id="ud-2"), _quote()]
    )
    monkeypatch.setattr(baseline, "connect", database.connect)

    batch = run_baseline(now=NOW)

    assert batch.skipped == 1
    assert batch.forecasts == 1


def test_a_quote_without_a_lock_time_is_refused_rather_than_risking_leakage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every point-in-time cutoff downstream depends on it."""
    database = _forecast_database(quotes=[_quote(locks_at=None)])
    monkeypatch.setattr(baseline, "connect", database.connect)

    batch = run_baseline(now=NOW)

    assert batch.forecasts == 0
    assert batch.skipped == 1


def test_an_empty_board_is_a_clean_no_op_rather_than_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _forecast_database(quotes=[])
    monkeypatch.setattr(baseline, "connect", database.connect)

    batch = run_baseline(now=NOW)

    assert (batch.forecasts, batch.episodes, batch.skipped) == (0, 0, 0)
    assert database.ran("UPDATE wnba.model_runs SET completed_at")


def test_history_is_always_bounded_by_the_lock_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """The leakage guarantee, checked at the boundary rather than trusted."""
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    run_baseline(now=NOW)

    reads = database.executed("SELECT l.*,g.scheduled_tipoff FROM wnba.player_game_lines")
    assert reads
    for statement, params in reads:
        assert "g.scheduled_tipoff<%s" in statement
        assert LOCKS_AT in params


# --------------------------------------------------------------------------------------
# Settlement
# --------------------------------------------------------------------------------------
def _episode_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "episode_id": uuid4(),
        "player_id": PLAYER_ID,
        "quote_player_id": PLAYER_ID,
        "game_id": GAME_ID,
        "prop_type": "points",
        "side": "over",
        "line": Decimal("15.5"),
        "source": "underdog",
        "quote_id": QUOTE_ID,
        "locks_at": LOCKS_AT,
        "predicted_probability": 0.62,
        "forecast_timestamp": NOW,
    }
    row.update(overrides)
    return row


def _settlement_database(line_row: dict[str, Any] | None) -> FakeDatabase:
    database = FakeDatabase()
    database.when("FROM wnba.decision_episodes d JOIN wnba.games g", [_episode_row()])
    database.when(
        "SELECT * FROM wnba.player_game_lines", [line_row] if line_row is not None else []
    )
    return database


def test_a_winning_forecast_is_settled_and_scored(monkeypatch: pytest.MonkeyPatch) -> None:
    database = _settlement_database({"points": 22, "minutes": Decimal("31.0"), "started": True})
    monkeypatch.setattr(settlement, "connect", database.connect)

    batch = settle_paper_episodes(now=NOW)

    assert batch.settled == 1
    assert batch.voided == 0
    _, params = database.executed("INSERT INTO wnba.episode_outcomes")[0]
    assert params[6] is True  # hit
    assert params[11] is not None  # brier
    assert params[12] is not None  # log loss


def test_a_scratch_is_voided_rather_than_counted_as_a_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scoring a void as a loss would blame the model for a coach's decision."""
    database = _settlement_database({"points": 0, "minutes": Decimal("0.0"), "started": False})
    monkeypatch.setattr(settlement, "connect", database.connect)

    batch = settle_paper_episodes(now=NOW)

    assert batch.voided == 1
    _, params = database.executed("INSERT INTO wnba.episode_outcomes")[0]
    assert params[8] is True  # was_voided
    assert params[11] is None  # unscored
    assert params[12] is None


def test_a_missing_box_score_row_voids_rather_than_settling_at_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _settlement_database(None)
    monkeypatch.setattr(settlement, "connect", database.connect)

    batch = settle_paper_episodes(now=NOW)

    assert batch.voided == 1
    assert batch.settled == 1


def test_an_exact_landing_on_the_line_is_a_push_and_is_not_scored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = FakeDatabase()
    database.when(
        "FROM wnba.decision_episodes d JOIN wnba.games g",
        [_episode_row(line=Decimal("16"))],
    )
    database.when(
        "SELECT * FROM wnba.player_game_lines",
        [{"points": 16, "minutes": Decimal("30.0"), "started": True}],
    )
    monkeypatch.setattr(settlement, "connect", database.connect)

    batch = settle_paper_episodes(now=NOW)

    assert batch.pushed == 1
    _, params = database.executed("INSERT INTO wnba.episode_outcomes")[0]
    assert params[7] is True  # was_push
    assert params[11] is None


def test_settlement_records_an_auditable_action_for_every_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _settlement_database({"points": 22, "minutes": Decimal("31.0"), "started": True})
    monkeypatch.setattr(settlement, "connect", database.connect)

    settle_paper_episodes(now=NOW)

    assert database.count("INSERT INTO wnba.ontology_actions") == 1


def test_nothing_settled_is_a_clean_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    database = FakeDatabase()
    monkeypatch.setattr(settlement, "connect", database.connect)

    batch = settle_paper_episodes(now=NOW)

    assert (batch.settled, batch.voided, batch.pushed, batch.unsupported) == (0, 0, 0, 0)


# --------------------------------------------------------------------------------------
# The whole loop
# --------------------------------------------------------------------------------------
def test_forecast_then_settle_produces_a_scoreable_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the system: a quote in, a scored forecast out, with lineage intact."""
    forecast_db = _forecast_database()
    monkeypatch.setattr(baseline, "connect", forecast_db.connect)
    forecast_batch = run_baseline(now=NOW)
    assert forecast_batch.episodes == 1

    _, episode_params = forecast_db.executed("INSERT INTO wnba.decision_episodes")[0]
    side = episode_params[5]
    line = episode_params[6]
    assert side in {"over", "under"}

    settle_db = _settlement_database({"points": 22, "minutes": Decimal("31.0"), "started": True})
    monkeypatch.setattr(settlement, "connect", settle_db.connect)
    settle_batch = settle_paper_episodes(now=NOW)

    assert settle_batch.settled == 1
    assert float(line) == pytest.approx(15.5)


def test_a_recommendation_is_never_written_without_a_status_the_domain_knows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    run_baseline(now=NOW)

    _, params = database.executed("INSERT INTO wnba.decision_episodes")[0]
    statuses = {status.value for status in RecommendationStatus}
    assert params[19] in statuses


def test_forecasts_are_always_written_as_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    """Analysis-only is the platform's central promise; it must not depend on a config flag."""
    database = _forecast_database()
    monkeypatch.setattr(baseline, "connect", database.connect)

    run_baseline(now=NOW)

    forecast_sql, _ = database.executed("INSERT INTO wnba.stat_forecasts")[0]
    episode_sql, _ = database.executed("INSERT INTO wnba.decision_episodes")[0]
    assert "true" in forecast_sql  # is_shadow
    assert "true" in episode_sql  # is_paper
