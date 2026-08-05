"""The production-validation gates, and the ways a flattering number could be manufactured.

Most of these tests exist because there is an easy wrong implementation that produces a better
result: counting rows instead of markets, counting quiet weeks as windows, assuming even money
for unpriced legs, scoring voids as losses. Each one is checked here directly.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wnba_services.learning_loop.validation import (
    CALIBRATION_THRESHOLD,
    MINIMUM_INDEPENDENT_RECOMMENDATIONS,
    MINIMUM_STABLE_WEEKS,
    MINIMUM_WEEK_MARKETS,
    drawdown_series,
    summarise_live_performance,
)

BASE = datetime(2026, 5, 4, tzinfo=UTC)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "episode_id": "e1",
        "player_id": "p1",
        "game_id": "g1",
        "prop_type": "points",
        "side": "over",
        "line": 14.5,
        "predicted_probability": 0.60,
        "shrunk_probability": 0.58,
        "breakeven_probability": 0.577,
        "system_recommendation": "candidate",
        "forecast_timestamp": BASE,
        "multiplier": 1.0,
        "source": "underdog",
        "projected_mean": 15.2,
        "hit": True,
        "was_voided": False,
        "was_push": False,
        "closing_line": None,
        "closing_multiplier": None,
        "actual_stat": 17.0,
        "settled_at": BASE + timedelta(hours=6),
        "scheduled_tipoff": BASE + timedelta(hours=3),
        "team": "LVA",
        "forecast_week": "2026-05-04",
    }
    row.update(overrides)
    return row


def _spread(count: int, **overrides: object) -> list[dict[str, object]]:
    """Distinct markets across distinct games, so nothing is collapsed by deduplication."""
    return [
        _row(
            episode_id=f"e{index}",
            player_id=f"p{index}",
            game_id=f"g{index}",
            hit=index % 2 == 0,
            **overrides,
        )
        for index in range(count)
    ]


# --------------------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------------------
def test_repeated_snapshots_of_one_market_are_one_recommendation() -> None:
    """The gate is about events. Re-scoring the board must not manufacture evidence."""
    snapshots = [
        _row(episode_id=f"e{index}", forecast_timestamp=BASE + timedelta(minutes=index * 30))
        for index in range(20)
    ]

    result = summarise_live_performance(snapshots)

    assert result.rows == 20
    assert result.recommendations == 1


def test_markets_sharing_a_game_carry_less_than_their_count() -> None:
    """Twenty props across one game is not twenty independent observations."""
    same_game = [
        _row(episode_id=f"e{index}", player_id=f"p{index}", game_id="shared") for index in range(20)
    ]

    result = summarise_live_performance(same_game)

    assert result.independent_markets == 20
    assert result.effective_sample < 20


def test_only_decisions_the_system_would_act_on_are_recommendations() -> None:
    rows = _spread(10) + [
        _row(
            episode_id=f"d{i}", player_id=f"q{i}", game_id=f"h{i}", system_recommendation="declined"
        )
        for i in range(40)
    ]

    result = summarise_live_performance(rows)

    assert result.recommendations == 10


# --------------------------------------------------------------------------------------
# Voids and pushes
# --------------------------------------------------------------------------------------
def test_voids_are_counted_as_handled_events_not_scored_as_losses() -> None:
    rows = _spread(10) + [
        _row(episode_id=f"v{i}", player_id=f"w{i}", game_id=f"x{i}", was_voided=True, hit=False)
        for i in range(4)
    ]

    result = summarise_live_performance(rows)

    assert result.voids == 4
    assert result.recommendations == 10  # the voids are not in the scored set


def test_pushes_are_excluded_from_scoring_and_reported() -> None:
    rows = _spread(6) + [
        _row(episode_id=f"u{i}", player_id=f"y{i}", game_id=f"z{i}", was_push=True)
        for i in range(3)
    ]

    result = summarise_live_performance(rows)

    assert result.pushes == 3
    assert result.recommendations == 6


# --------------------------------------------------------------------------------------
# Weekly windows
# --------------------------------------------------------------------------------------
def test_a_quiet_week_is_not_a_window_of_evidence() -> None:
    """Eight weeks containing four decisions each are not eight windows."""
    rows = []
    for week in range(8):
        for index in range(4):
            rows.append(
                _row(
                    episode_id=f"e{week}-{index}",
                    player_id=f"p{week}-{index}",
                    game_id=f"g{week}-{index}",
                    forecast_week=f"2026-05-{week + 1:02d}",
                )
            )

    result = summarise_live_performance(rows)

    assert len(result.weekly) == 8
    assert result.stable_weeks == 0
    assert MINIMUM_WEEK_MARKETS > 4


def test_a_busy_week_counts_as_a_window() -> None:
    rows = [
        _row(
            episode_id=f"e{index}",
            player_id=f"p{index}",
            game_id=f"g{index}",
            forecast_week="2026-05-04",
        )
        for index in range(MINIMUM_WEEK_MARKETS + 5)
    ]

    result = summarise_live_performance(rows)

    assert result.stable_weeks == 1


# --------------------------------------------------------------------------------------
# Closing-line value
# --------------------------------------------------------------------------------------
def test_closing_line_value_is_signed_by_the_side_taken() -> None:
    """Taking the over at 14.5 into a 15.5 close is favourable; the under is not."""
    over = summarise_live_performance([_row(side="over", line=14.5, closing_line=15.5)])
    under = summarise_live_performance([_row(side="under", line=14.5, closing_line=15.5)])

    assert over.mean_line_value == 1.0
    assert under.mean_line_value == -1.0


def test_no_closing_line_says_so_rather_than_reporting_zero() -> None:
    result = summarise_live_performance(_spread(10))

    assert result.mean_line_value is None
    assert result.line_value_sample == 0
    assert any("closing line" in note for note in result.notes)


# --------------------------------------------------------------------------------------
# Pricing
# --------------------------------------------------------------------------------------
def test_unpriced_legs_are_excluded_rather_than_assumed_even_money() -> None:
    """The assumption that turns an unprofitable record into a profitable one on paper."""
    result = summarise_live_performance(_spread(10))  # multiplier defaults to 1.0

    assert result.payout_return is None
    assert any("usable price" in note for note in result.notes)


def test_priced_returns_use_the_recorded_multiplier() -> None:
    rows = [
        _row(episode_id=f"e{i}", player_id=f"p{i}", game_id=f"g{i}", multiplier=2.0, hit=i < 6)
        for i in range(10)
    ]

    result = summarise_live_performance(rows)

    # Six wins at +1.0 and four losses at -1.0 over ten legs.
    assert result.payout_return is not None
    assert abs(result.payout_return - 0.2) < 1e-9


def test_the_flat_comparison_uses_the_breakeven_the_gate_actually_used() -> None:
    rows = [
        _row(episode_id=f"e{i}", player_id=f"p{i}", game_id=f"g{i}", hit=i < 5) for i in range(10)
    ]

    result = summarise_live_performance(rows)

    assert result.breakeven is not None
    assert result.flat_return is not None
    # 50% hit rate against a 57.7% requirement is a loss, however good it feels.
    assert result.flat_return < 0


# --------------------------------------------------------------------------------------
# Drawdown
# --------------------------------------------------------------------------------------
def test_drawdown_measures_peak_to_trough_not_final_loss() -> None:
    fraction, units = drawdown_series([1.0, 1.0, -1.0, -1.0, -1.0, 1.0])

    assert units == 3.0
    assert abs(fraction - 1.5) < 1e-9  # dropped 3 units from a peak of 2


def test_a_monotonic_winner_has_no_drawdown() -> None:
    assert drawdown_series([1.0, 1.0, 1.0]) == (0.0, 0.0)


# --------------------------------------------------------------------------------------
# Subgroup stability
# --------------------------------------------------------------------------------------
def test_an_aggregate_win_hiding_a_broken_market_is_flagged() -> None:
    good = [
        _row(
            episode_id=f"a{i}",
            player_id=f"pa{i}",
            game_id=f"ga{i}",
            predicted_probability=0.6,
            hit=i % 10 < 6,
        )
        for i in range(120)
    ]
    # Rebounds: stated 0.9, lands 10% of the time.
    bad = [
        _row(
            episode_id=f"b{i}",
            player_id=f"pb{i}",
            game_id=f"gb{i}",
            prop_type="rebounds",
            predicted_probability=0.9,
            hit=i % 10 == 0,
        )
        for i in range(40)
    ]

    result = summarise_live_performance(good + bad)
    degraded = {group.value for group in result.degraded_subgroups}

    assert "rebounds" in degraded


def test_a_thin_subgroup_cannot_be_called_degraded() -> None:
    rows = _spread(60) + [
        _row(
            episode_id=f"t{i}",
            player_id=f"pt{i}",
            game_id=f"gt{i}",
            prop_type="assists",
            predicted_probability=0.95,
            hit=False,
        )
        for i in range(5)
    ]

    result = summarise_live_performance(rows)

    assert "assists" not in {group.value for group in result.degraded_subgroups}


def test_thresholds_are_the_roadmap_numbers() -> None:
    assert MINIMUM_INDEPENDENT_RECOMMENDATIONS == 500
    assert MINIMUM_STABLE_WEEKS == 8
    assert CALIBRATION_THRESHOLD == 0.08


def test_an_empty_record_measures_nothing_rather_than_passing() -> None:
    result = summarise_live_performance([])

    assert result.recommendations == 0
    assert result.calibration_error is None
    assert result.payout_return is None
    assert result.stable_weeks == 0
