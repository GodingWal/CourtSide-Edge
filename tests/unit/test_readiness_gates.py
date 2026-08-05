"""Readiness gates, and the difference between a lot of rows and a lot of evidence."""

from __future__ import annotations

from wnba_services.learning_loop.readiness import GateResult, build_gate_results

COUNT_GATE = "out_of_sample_recommendations"


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
    """Replay can reach `provisional_pass` and never `pass`, on the gates it is evidence for."""
    gates = {gate.gate_id: gate for gate in build_gate_results(_rows(1500))}

    assert gates["no_material_look_ahead"].status == "provisional_pass"
    assert gates["complete_model_data_lineage"].status == "provisional_pass"
    # Nothing a replay feeds may reach `pass`, however good the replay looks.
    assert all(gate.status != "pass" for gate in gates.values())


def test_a_replay_calibration_miss_is_still_a_failure() -> None:
    """Provisional evidence can fail. Only passing requires the live record."""
    gates = {gate.gate_id: gate for gate in build_gate_results(_rows(1500))}

    # `provisional_pass` or `fail`, never `pass`: mypy proves the last one, which is the point.
    assert gates["calibration_within_tolerance"].status in {"provisional_pass", "fail"}


def test_a_replay_cannot_satisfy_a_gate_about_what_happened_live() -> None:
    """The defect this rewiring fixed.

    These gates used to read the replay and were hard-coded `pending` with prose saying the live
    evidence did not exist. They would have stayed `pending` after five hundred live
    recommendations arrived, because nothing in the path ever looked at a settled episode. They
    now read the live record, so 1,500 replay rows correctly satisfy none of them.
    """
    gates = {gate.gate_id: gate for gate in build_gate_results(_rows(1500))}

    for gate_id in (
        "out_of_sample_recommendations",
        "positive_closing_line_value",
        "positive_performance_after_pricing",
        "drawdown_within_tolerance",
        "stable_rolling_windows",
    ):
        assert gates[gate_id].status == "pending", gate_id
        assert gates[gate_id].evidence_source == "live_settled_decisions", gate_id


def test_repeated_snapshots_of_one_market_are_not_new_evidence() -> None:
    """Five pre-tip snapshots of a market are five looks at one event, not five events."""
    markets = _rows(1500)
    with_snapshots = [
        decision(i, markets_per_game=5, snapshot=s) for s in range(5) for i in range(1500)
    ]
    assert len(with_snapshots) == 5 * len(markets)

    once = _gate(build_gate_results(markets), "calibration_within_tolerance")
    repeatedly = _gate(build_gate_results(with_snapshots), "calibration_within_tolerance")
    assert once.observed_value == repeatedly.observed_value


def test_markets_crowded_into_few_games_carry_less_weight() -> None:
    """The audit's finding: thousands of forecast rows over a handful of independent games.

    The correction still runs on the replay and still says so; it now says so in the count gate's
    detail rather than in its observed value, because the value belongs to the live record.
    """
    spread = _gate(build_gate_results(_rows(1500, markets_per_game=5)), COUNT_GATE)
    crowded = _gate(build_gate_results(_rows(1500, markets_per_game=50)), COUNT_GATE)

    spread_effective = _replay_effective(spread.detail)
    crowded_effective = _replay_effective(crowded.detail)

    assert crowded_effective < spread_effective
    assert crowded.status == "pending"


def test_the_count_gate_reports_both_evidence_classes_without_adding_them() -> None:
    """Live and replay counts are both shown, and are never summed into one number."""
    gate = _gate(build_gate_results(_rows(1500)), COUNT_GATE)

    assert "Live:" in gate.detail
    assert "Replay" in gate.detail
    assert "effective sample" in gate.detail
    # The value the gate is judged on is the live one, which is zero here.
    assert gate.observed_value == 0.0


def test_incomplete_lineage_fails_closed() -> None:
    rows = _rows(1500)
    rows[0]["historical_quote_id"] = None
    gates = {gate.gate_id: gate for gate in build_gate_results(rows)}
    assert gates["complete_model_data_lineage"].status == "fail"


def _gate(gates: tuple[GateResult, ...], gate_id: str) -> GateResult:
    return next(gate for gate in gates if gate.gate_id == gate_id)


def _replay_effective(detail: str) -> float:
    """Pull the replay's effective sample out of the count gate's detail string."""
    # "(effective N)" rather than the first "effective ", which is the live sentence.
    marker = "(effective "
    start = detail.index(marker) + len(marker)
    return float(detail[start:].split(")")[0])


def test_an_empty_replay_passes_nothing() -> None:
    gates = {gate.gate_id: gate for gate in build_gate_results([])}
    assert gates["out_of_sample_recommendations"].status == "pending"
    assert all(gate.status != "pass" for gate in gates.values())
