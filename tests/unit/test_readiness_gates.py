"""Readiness gates, and the difference between a lot of rows and a lot of evidence."""

from __future__ import annotations

from wnba_services.learning_loop.readiness import build_gate_results


def decision(
    index: int,
    *,
    markets_per_game: int = 5,
    probability: float = 0.62,
    hit: bool = True,
    snapshot: int = 0,
) -> dict[str, object]:
    """One replay row. ``snapshot`` varies the timestamp without changing which market it is."""
    return {
        "game_id": f"game-{index // markets_per_game}",
        "player_id": f"player-{index % markets_per_game}",
        "prop_type": ("points", "rebounds", "assists", "three_pointers")[index % 4],
        "probability": probability,
        "hit": hit,
        "historical_quote_id": f"quote-{index}",
        "history_games": 20,
        "forecast_week": f"week-{index % 10}",
        "forecast_as_of": snapshot,
    }


def _rows(count: int, *, markets_per_game: int = 5) -> list[dict[str, object]]:
    return [decision(i, markets_per_game=markets_per_game) for i in range(count)]


def test_historical_evidence_is_provisional_not_final() -> None:
    gates = {gate.gate_id: gate for gate in build_gate_results(_rows(1500))}
    assert gates["out_of_sample_recommendations"].status == "provisional_pass"
    assert gates["positive_performance_after_pricing"].status == "pending"
    assert gates["drawdown_within_tolerance"].status == "pending"


def test_repeated_snapshots_of_one_market_are_not_new_evidence() -> None:
    """Five pre-tip snapshots of a market are five looks at one event, not five events."""
    markets = _rows(1500)
    with_snapshots = [
        decision(i, markets_per_game=5, snapshot=s) for s in range(5) for i in range(1500)
    ]
    assert len(with_snapshots) == 5 * len(markets)

    once = build_gate_results(markets)[0]
    repeatedly = build_gate_results(with_snapshots)[0]
    assert once.observed_value == repeatedly.observed_value


def test_markets_crowded_into_few_games_carry_less_weight() -> None:
    """The audit's finding: thousands of forecast rows over a handful of independent games."""
    spread = build_gate_results(_rows(1500, markets_per_game=5))[0]
    crowded = build_gate_results(_rows(1500, markets_per_game=50))[0]

    assert spread.observed_value is not None
    assert crowded.observed_value is not None
    assert crowded.observed_value < spread.observed_value
    assert crowded.status == "pending"


def test_the_count_gate_reports_what_it_actually_counted() -> None:
    gate = build_gate_results(_rows(1500))[0]
    assert "independent markets" in gate.detail
    assert "effective sample" in gate.detail


def test_incomplete_lineage_fails_closed() -> None:
    rows = _rows(1500)
    rows[0]["historical_quote_id"] = None
    gates = {gate.gate_id: gate for gate in build_gate_results(rows)}
    assert gates["complete_model_data_lineage"].status == "fail"


def test_an_empty_replay_passes_nothing() -> None:
    gates = {gate.gate_id: gate for gate in build_gate_results([])}
    assert gates["out_of_sample_recommendations"].status == "pending"
    assert all(gate.status != "pass" for gate in gates.values())
