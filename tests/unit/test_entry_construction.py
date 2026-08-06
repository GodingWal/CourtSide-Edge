"""Leg correlation and entry construction: the part that decides which pick to take.

The board ranked legs; nothing chose an entry. These tests pin the two properties that make the
new answer trustworthy rather than merely present: correlation must be a measurement with a sign
that follows the sides taken, and a suggestion must never be produced by ignoring how wrong that
measurement could be.
"""

from __future__ import annotations

from datetime import UTC, datetime

from wnba_marketmath import prizepicks_payout_table
from wnba_services.market_engine.correlation import (
    CorrelationEstimate,
    LegKey,
    PairObservation,
    classify_relation,
    entry_correlation,
    fit_pair_correlations,
    outcome_correlation,
)
from wnba_services.market_engine.entries import CandidateLeg, construct_entries

BUNDLED_AT = datetime(2026, 8, 3, tzinfo=UTC)


def leg(
    name: str,
    prop: str,
    probability: float,
    *,
    side: str = "over",
    player: str | None = None,
    team: str = "LVA",
    game: str = "g1",
) -> CandidateLeg:
    return CandidateLeg(
        projection_id=f"{name}-{prop}",
        player_name=name,
        prop_type=prop,
        side=side,
        line=20.5,
        probability=probability,
        player_id=player or name,
        team=team,
        game_id=game,
    )


# --------------------------------------------------------------------------------------
# Relations name a mechanism
# --------------------------------------------------------------------------------------
def test_relations_nest_from_tightest_true_statement_outward() -> None:
    same_player = classify_relation(
        LegKey("points", "over", "a", "LVA", "g1"), LegKey("rebounds", "over", "a", "LVA", "g1")
    )
    teammates = classify_relation(
        LegKey("points", "over", "a", "LVA", "g1"), LegKey("points", "over", "b", "LVA", "g1")
    )
    opponents = classify_relation(
        LegKey("points", "over", "a", "LVA", "g1"), LegKey("points", "over", "c", "NYL", "g1")
    )
    elsewhere = classify_relation(
        LegKey("points", "over", "a", "LVA", "g1"), LegKey("points", "over", "d", "SEA", "g2")
    )
    assert (same_player, teammates, opponents, elsewhere) == (
        "same_player",
        "same_team",
        "opposing_team",
        "different_game",
    )


def test_opposing_sides_invert_the_ticket_correlation() -> None:
    """A hedge has to look like a hedge, or the pricer overstates both legs landing."""
    over_over = LegKey("points", "over", "a"), LegKey("rebounds", "over", "a")
    over_under = LegKey("points", "over", "a"), LegKey("rebounds", "under", "a")

    assert outcome_correlation(0.3, *over_over) == 0.3
    assert outcome_correlation(0.3, *over_under) == -0.3


# --------------------------------------------------------------------------------------
# Fitting is a measurement, and says when it is not
# --------------------------------------------------------------------------------------
def test_a_thin_cell_returns_the_prior_and_admits_it() -> None:
    observations = [
        PairObservation("same_player", "points", "rebounds", True, True) for _ in range(5)
    ]

    estimate = fit_pair_correlations(observations, minimum_pairs=30)[0]

    assert not estimate.is_fitted
    assert estimate.sample_size == 5
    assert estimate.correlation == 0.30, "the stated same-player prior, not a measured zero"


def test_a_perfectly_associated_cell_fits_near_one() -> None:
    observations = [
        PairObservation("same_player", "points", "rebounds", index % 2 == 0, index % 2 == 0)
        for index in range(60)
    ]

    estimate = fit_pair_correlations(observations, minimum_pairs=30)[0]

    assert estimate.is_fitted
    assert estimate.correlation > 0.9


def test_no_variation_falls_back_rather_than_dividing_by_zero() -> None:
    """Every observed pair cleared. That is not a correlation of one; it is no information."""
    observations = [PairObservation("same_team", "points", "points", True, True) for _ in range(50)]

    estimate = fit_pair_correlations(observations, minimum_pairs=30)[0]

    assert not estimate.is_fitted


def test_market_pairs_are_stored_in_one_canonical_order() -> None:
    """The relationship is symmetric, so points/rebounds and rebounds/points are one cell."""
    observations = [
        PairObservation("same_player", "rebounds", "points", True, False) for _ in range(40)
    ] + [PairObservation("same_player", "points", "rebounds", True, False) for _ in range(40)]

    estimates = fit_pair_correlations(observations, minimum_pairs=30)

    assert len(estimates) == 1
    assert (estimates[0].prop_a, estimates[0].prop_b) == ("points", "rebounds")
    assert estimates[0].sample_size == 80


def test_entry_correlation_reports_the_pairs_behind_the_scalar() -> None:
    """A '0.1 entry' is often one strong pair diluted by weak ones, and that changes which leg
    a human drops."""
    keys = [
        LegKey("points", "over", "a", "LVA", "g1"),
        LegKey("rebounds", "over", "a", "LVA", "g1"),
        LegKey("assists", "over", "z", "SEA", "g9"),
    ]

    estimate = entry_correlation(keys)

    assert len(estimate.pairs) == 3
    assert max(pair.correlation for pair in estimate.pairs) == 0.30
    assert estimate.mean < 0.30, "the same-player pair is diluted by two unrelated ones"
    assert not estimate.is_fitted


def test_fitted_estimates_are_preferred_over_priors() -> None:
    fitted = {
        ("same_player", "points", "rebounds"): CorrelationEstimate(
            "same_player", "points", "rebounds", 0.62, 0.04, 900, True
        )
    }
    keys = [
        LegKey("points", "over", "a", "LVA", "g1"),
        LegKey("rebounds", "over", "a", "LVA", "g1"),
    ]

    estimate = entry_correlation(keys, fitted)

    assert estimate.is_fitted
    assert estimate.mean == 0.62
    assert estimate.high - estimate.low < 0.1, "a 900-pair sample earns a narrow band"


# --------------------------------------------------------------------------------------
# Construction can only decline
# --------------------------------------------------------------------------------------
def test_the_same_player_and_market_is_never_two_legs() -> None:
    """Both sides of one market is a guaranteed loss; the same side twice is one position."""
    candidates = [
        leg("Wilson", "points", 0.70),
        leg("Wilson", "points", 0.68, side="under"),
        leg("Clark", "assists", 0.66, player="clark", team="IND", game="g2"),
    ]

    entries = construct_entries(candidates, prizepicks_payout_table(BUNDLED_AT), simulations=2_000)

    for entry in entries:
        markets = [(item.player_id, item.prop_type) for item in entry.legs]
        assert len(markets) == len(set(markets))


def test_the_game_cap_limits_exposure_to_the_least_measured_correlation() -> None:
    candidates = [
        leg("A", "points", 0.70, player="a"),
        leg("B", "rebounds", 0.69, player="b"),
        leg("C", "assists", 0.68, player="c"),
        leg("D", "points", 0.67, player="d", team="SEA", game="g2"),
    ]

    entries = construct_entries(
        candidates, prizepicks_payout_table(BUNDLED_AT), max_per_game=2, simulations=2_000
    )

    assert entries
    for entry in entries:
        per_game: dict[str, int] = {}
        for item in entry.legs:
            per_game[item.game_id or ""] = per_game.get(item.game_id or "", 0) + 1
        assert max(per_game.values()) <= 2


def test_an_unpriced_entry_is_declined_rather_than_ranked_on_its_midpoint() -> None:
    """Every suggestion here rests on priors, so every one of them must say so."""
    candidates = [
        leg("A", "points", 0.62, player="a"),
        leg("B", "rebounds", 0.61, player="b", team="SEA", game="g2"),
        leg("C", "assists", 0.60, player="c", team="NYL", game="g3"),
    ]

    entries = construct_entries(candidates, prizepicks_payout_table(BUNDLED_AT), simulations=2_000)

    assert entries
    assert all(entry.verdict == "decline" for entry in entries)
    assert all(any("stated prior" in gate for gate in entry.gates) for entry in entries), (
        "an entry priced off beliefs must never present as a measurement"
    )


def test_the_verdict_is_taken_on_the_worst_end_of_the_band() -> None:
    candidates = [
        leg("A", "points", 0.95, player="a"),
        leg("B", "rebounds", 0.95, player="b", team="SEA", game="g2"),
    ]

    entries = construct_entries(candidates, prizepicks_payout_table(BUNDLED_AT), simulations=4_000)

    assert entries
    entry = entries[0]
    assert entry.worst_case_expected_value <= entry.expected_value


def test_a_board_with_nothing_on_it_suggests_nothing() -> None:
    assert construct_entries([], prizepicks_payout_table(BUNDLED_AT)) == []
    assert construct_entries([leg("A", "points", 0.9)], prizepicks_payout_table(BUNDLED_AT)) == []
