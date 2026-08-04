"""Settlement scoring: the decision every conclusion in the platform rests on.

Calibration, readiness, model evaluation and error attribution are all computed downstream of
"did this forecast hit, and what did it score?". A sign error or an inverted comparison here
would corrupt every one of them at once, and would do so while producing numbers that look
entirely reasonable -- which is exactly why it needs pinning down rather than eyeballing.

These tests were written before settlement had ever executed against real data.
"""

from __future__ import annotations

import pytest
from wnba_domain.decision import brier_score, log_loss
from wnba_services.learning_loop.settlement import Resolution, actual_stat, resolve_outcome


def resolve(side: str, actual: float, line: float, minutes: float | None = 30.0) -> Resolution:
    return resolve_outcome(side=side, actual=actual, forecast_line=line, minutes=minutes)


# --------------------------------------------------------------------------------------
# Direction -- the easiest thing to invert and the costliest to get wrong
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("side", "actual", "line", "expected_hit"),
    [
        ("over", 26.0, 21.5, True),
        ("over", 18.0, 21.5, False),
        ("under", 18.0, 21.5, True),
        ("under", 26.0, 21.5, False),
    ],
)
def test_side_comparison_runs_the_right_way(
    side: str, actual: float, line: float, expected_hit: bool
) -> None:
    assert resolve(side, actual, line).hit is expected_hit


def test_over_and_under_on_the_same_outcome_are_exact_opposites() -> None:
    """On any non-push result exactly one side wins. If both or neither did, the comparison
    is broken in a way that would quietly halve or double every measured hit rate."""
    for actual in (0.0, 5.0, 21.0, 22.0, 40.0):
        over = resolve("over", actual, 21.5)
        under = resolve("under", actual, 21.5)
        assert over.hit != under.hit, f"both sides agreed at actual={actual}"


def test_a_half_point_line_can_never_push() -> None:
    """The reason books use half points. A push here would mean the parser produced a line
    that cannot exist."""
    for actual in range(0, 45):
        assert resolve("over", float(actual), 21.5).pushed is False


# --------------------------------------------------------------------------------------
# Push
# --------------------------------------------------------------------------------------
def test_exact_landing_on_a_whole_line_is_a_push_not_a_loss() -> None:
    result = resolve("over", 21.0, 21.0)
    assert result.pushed is True
    assert result.hit is False
    assert result.voided is False


def test_a_push_is_excluded_from_scoring() -> None:
    """Scoring a push as either outcome invents information that does not exist."""
    assert resolve("over", 21.0, 21.0).is_scoreable is False


@pytest.mark.parametrize("side", ["over", "under"])
def test_push_is_symmetric_across_sides(side: str) -> None:
    assert resolve(side, 8.0, 8.0).pushed is True


# --------------------------------------------------------------------------------------
# Void -- the distinction that protects the model from a coach's decision
# --------------------------------------------------------------------------------------
def test_a_player_who_did_not_appear_voids_rather_than_loses() -> None:
    """The forecast never got a chance to be right or wrong.

    Counting a scratch as a loss would blame the model for a late rotation decision it could
    not have known about, and would drag measured accuracy down for a reason unrelated to
    forecast quality.
    """
    missing = resolve("over", 0.0, 21.5, minutes=None)
    assert missing.voided is True
    assert missing.hit is False
    assert missing.is_scoreable is False


def test_zero_minutes_voids_even_though_a_box_score_row_exists() -> None:
    """A DNP still produces a row, with zeros in every column. Read naively that is a
    spectacularly successful `under` on every market the player was priced in."""
    dnp = resolve("under", 0.0, 21.5, minutes=0.0)
    assert dnp.voided is True
    assert dnp.hit is False, "a DNP must never be recorded as a winning under"


def test_void_takes_precedence_over_push() -> None:
    """A scratch whose stat happens to equal the line is still a scratch."""
    assert resolve("over", 21.5, 21.5, minutes=0.0).voided is True


def test_a_player_who_appeared_briefly_still_scores() -> None:
    """Only zero minutes voids. One minute is a real, if unhelpful, appearance."""
    brief = resolve("under", 0.0, 21.5, minutes=1.0)
    assert brief.voided is False
    assert brief.hit is True
    assert brief.is_scoreable is True


# --------------------------------------------------------------------------------------
# Scores attached to resolutions
# --------------------------------------------------------------------------------------
def test_a_confident_correct_forecast_scores_better_than_a_hesitant_one() -> None:
    confident, hesitant = 0.90, 0.55
    assert brier_score(confident, 1) < brier_score(hesitant, 1)
    assert log_loss(confident, 1) < log_loss(hesitant, 1)


def test_confident_and_wrong_is_punished_hardest_by_log_loss() -> None:
    """The asymmetry that makes log loss the primary metric: Brier caps the penalty for
    confident wrongness, log loss does not."""
    assert log_loss(0.95, 0) > log_loss(0.55, 0) * 3
    assert brier_score(0.95, 0) < log_loss(0.95, 0)


def test_scores_are_only_computed_for_scoreable_resolutions() -> None:
    scoreable = [
        resolve("over", 26.0, 21.5),
        resolve("under", 18.0, 21.5),
    ]
    unscoreable = [
        resolve("over", 21.0, 21.0),
        resolve("over", 0.0, 21.5, minutes=None),
        resolve("over", 0.0, 21.5, minutes=0.0),
    ]
    assert all(r.is_scoreable for r in scoreable)
    assert not any(r.is_scoreable for r in unscoreable)


# --------------------------------------------------------------------------------------
# Combination markets
# --------------------------------------------------------------------------------------
def test_combination_markets_sum_their_components() -> None:
    line = {
        "points": 20,
        "rebounds_offensive": 2,
        "rebounds_defensive": 6,
        "assists": 5,
        "steals": 1,
        "blocks": 2,
        "minutes": 32.0,
    }
    assert actual_stat(line, "points") == 20
    assert actual_stat(line, "rebounds") == 8
    assert actual_stat(line, "points_rebounds") == 28
    assert actual_stat(line, "points_rebounds_assists") == 33
    assert actual_stat(line, "steals_blocks") == 3


def test_an_unsupported_market_raises_rather_than_guessing() -> None:
    """Silently settling a market we cannot compute would fabricate an outcome."""
    with pytest.raises(Exception, match=r"(?i)unsupported|unknown"):
        actual_stat({"points": 20, "minutes": 30.0}, "double_double")
