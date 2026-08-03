from wnba_services.learning_loop.readiness import build_gate_results


def decision(index: int, probability: float = 0.62, hit: bool = True) -> dict[str, object]:
    return {
        "game_id": f"game-{index // 15}",
        "player_id": f"player-{index % 50}",
        "prop_type": ("points", "rebounds", "assists", "three_pointers")[index % 4],
        "probability": probability,
        "hit": hit,
        "historical_quote_id": f"quote-{index}",
        "history_games": 20,
        "forecast_week": f"week-{index % 10}",
    }


def test_historical_evidence_is_provisional_not_final() -> None:
    gates = {gate.gate_id: gate for gate in build_gate_results([decision(i) for i in range(600)])}
    assert gates["out_of_sample_recommendations"].status == "provisional_pass"
    assert gates["positive_performance_after_pricing"].status == "pending"
    assert gates["drawdown_within_tolerance"].status == "pending"


def test_incomplete_lineage_fails_closed() -> None:
    rows = [decision(i) for i in range(600)]
    rows[0]["historical_quote_id"] = None
    gates = {gate.gate_id: gate for gate in build_gate_results(rows)}
    assert gates["complete_model_data_lineage"].status == "fail"
