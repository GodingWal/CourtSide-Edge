"""Joint teammate-absence effects, and the double-counting the marginal version produced."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from wnba_services.feature_engine.teammate_effects import estimate_joint_effects


def _games(count: int, minutes: float, points: float, start: int = 0) -> list[dict[str, Any]]:
    return [
        {"game_id": uuid4(), "minutes": minutes, "points": points}
        for _ in range(start, start + count)
    ]


def _single_teammate_history() -> tuple[list[dict[str, Any]], dict[UUID, set[UUID]]]:
    """Twelve games together at 15 points in 30 minutes, eight without at 20 in 34."""
    together = _games(12, 30.0, 15.0)
    apart = _games(8, 34.0, 20.0)
    teammate = uuid4()
    return together + apart, {teammate: {UUID(str(row["game_id"])) for row in together}}


def test_a_single_absence_effect_is_shrunk_and_bounded() -> None:
    rows, presence = _single_teammate_history()
    effects = estimate_joint_effects(rows, presence, ("points",))
    teammate = next(iter(presence))
    assert teammate in effects

    estimate = effects[teammate]
    assert 1.0 < estimate.rate_multiplier <= 1.3
    assert 0.0 < estimate.minutes_delta <= 5.0
    assert 0.0 < estimate.confidence <= 0.8
    assert estimate.games_with == 12
    assert estimate.games_without == 8


def test_a_teammate_who_never_missed_a_game_yields_no_estimate() -> None:
    rows, _ = _single_teammate_history()
    always_present = {uuid4(): {UUID(str(row["game_id"])) for row in rows}}
    assert estimate_joint_effects(rows, always_present, ("points",)) == {}


def test_a_teammate_who_never_played_yields_no_estimate() -> None:
    rows, _ = _single_teammate_history()
    assert estimate_joint_effects(rows, {uuid4(): set()}, ("points",)) == {}


def test_effects_are_not_double_counted_when_absences_coincide() -> None:
    """The defect the joint fit exists to remove.

    Two teammates miss almost exactly the same games, and the player's rate rises on those
    nights. A marginal estimator credits each of them with the whole lift; multiplying the two
    then claims roughly the square of an effect the data only ever showed once. Fitting them
    together must split the credit, so their product stays near the observed lift.
    """
    together = _games(14, 30.0, 15.0)
    apart = _games(8, 30.0, 19.5)
    first, second = uuid4(), uuid4()
    together_ids = {UUID(str(row["game_id"])) for row in together}
    # The second teammate also played one of the "apart" games, so the design is not singular.
    presence = {
        first: set(together_ids),
        second: together_ids | {UUID(str(apart[0]["game_id"]))},
    }

    effects = estimate_joint_effects(together + apart, presence, ("points",))
    assert set(effects) == {first, second}

    observed_lift = 19.5 / 15.0
    combined = effects[first].rate_multiplier * effects[second].rate_multiplier
    assert combined < observed_lift + 0.05


def test_perfectly_collinear_teammates_split_the_credit_evenly() -> None:
    """Two players absent for exactly the same games have no separable individual effects.

    The ridge penalty resolves the tie the only defensible way -- equal shares -- rather than
    handing one of them the whole effect because it happened to be fitted first. What must not
    happen is each receiving the full lift, which is precisely what the marginal estimator did
    and what made the product of two multipliers meaningless.
    """
    together = _games(14, 30.0, 15.0)
    apart = _games(8, 30.0, 19.0)
    first, second = uuid4(), uuid4()
    together_ids = {UUID(str(row["game_id"])) for row in together}
    presence = {first: set(together_ids), second: set(together_ids)}

    effects = estimate_joint_effects(together + apart, presence, ("points",))
    assert set(effects) == {first, second}
    assert effects[first].rate_multiplier == pytest.approx(effects[second].rate_multiplier)

    observed_lift = 19.0 / 15.0
    combined = effects[first].rate_multiplier * effects[second].rate_multiplier
    assert combined < observed_lift
    assert combined < observed_lift**2 * 0.8


def test_an_absence_that_changes_nothing_produces_a_neutral_multiplier() -> None:
    together = _games(14, 30.0, 15.0)
    apart = _games(8, 30.0, 15.0)
    teammate = uuid4()
    presence = {teammate: {UUID(str(row["game_id"])) for row in together}}

    estimate = estimate_joint_effects(together + apart, presence, ("points",))[teammate]
    assert abs(estimate.rate_multiplier - 1.0) < 0.02
    assert abs(estimate.minutes_delta) < 0.5


def test_games_the_player_missed_are_excluded_from_the_fit() -> None:
    rows, presence = _single_teammate_history()
    scratched = _games(6, 0.0, 0.0)
    teammate = next(iter(presence))

    with_scratches = estimate_joint_effects(rows + scratched, presence, ("points",))[teammate]
    without_scratches = estimate_joint_effects(rows, presence, ("points",))[teammate]
    assert with_scratches.games_with == without_scratches.games_with
    assert with_scratches.games_without == without_scratches.games_without


def test_too_little_history_yields_nothing() -> None:
    rows = _games(4, 30.0, 15.0)
    presence = {uuid4(): {UUID(str(rows[0]["game_id"]))}}
    assert estimate_joint_effects(rows, presence, ("points",)) == {}
