"""The console's pricing surface: the engines that existed and had no caller.

`/api/price` had been able to price a correlated entry since the first week of the project, and
nothing in the console ever called it. These tests cover the wiring and, more importantly, the
honesty properties of the numbers it now shows: an assumed correlation must announce itself, a
prior must not present as a measurement, and the board must rank on the edge the gate actually
decided on rather than on the probability before shrinkage.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from wnba_apps.api import main
from wnba_apps.api.main import _edge_over_breakeven, app

client = TestClient(app)

PLAYER_ONE = "11111111-1111-1111-1111-111111111111"
PLAYER_TWO = "22222222-2222-2222-2222-222222222222"
PLAYER_THREE = "33333333-3333-3333-3333-333333333333"


def _price(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "source": "prizepicks",
        "entry_type": "power",
        "leg_probabilities": [0.62, 0.61],
    }
    body.update(overrides)
    response = client.post("/api/price", json=body)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


# --------------------------------------------------------------------------------------
# The board ranks on the number the gate decided on
# --------------------------------------------------------------------------------------
def test_edge_is_the_shrunk_probability_over_break_even() -> None:
    assert _edge_over_breakeven(
        {"shrunk_probability": 0.60, "breakeven_probability": 0.577}
    ) == pytest.approx(0.023)


def test_a_forecast_the_gate_never_priced_has_no_edge_rather_than_a_zero_one() -> None:
    assert (
        _edge_over_breakeven({"shrunk_probability": None, "breakeven_probability": 0.577}) is None
    )
    assert _edge_over_breakeven({}) is None


# --------------------------------------------------------------------------------------
# An assumption has to announce itself
# --------------------------------------------------------------------------------------
def test_pricing_without_leg_descriptors_says_it_assumed_zero() -> None:
    """Zero is the one correlation value that is definitely wrong for a real slip."""
    result = _price()

    assert result["correlation_source"] == "assumed_zero"
    assert result["correlation_used"] == 0.0
    assert any("assumed zero" in gate for gate in result["gates"])


def test_described_legs_are_priced_off_structure_and_flagged_as_a_prior() -> None:
    result = _price(
        legs=[
            {"prop_type": "points", "side": "over", "player_id": PLAYER_ONE},
            {
                "prop_type": "rebounds",
                "side": "over",
                "player_id": PLAYER_ONE,
            },
        ]
    )

    assert result["correlation_source"] == "prior"
    assert result["correlation_used"] > 0, "one player's two markets share a minutes draw"
    assert any("stated prior" in gate for gate in result["gates"])
    assert result["correlation_pairs"][0]["relation"] == "same_player"


def test_a_hedge_prices_as_a_hedge() -> None:
    player = PLAYER_TWO
    aligned = _price(
        legs=[
            {"prop_type": "points", "side": "over", "player_id": player},
            {"prop_type": "rebounds", "side": "over", "player_id": player},
        ]
    )
    opposed = _price(
        legs=[
            {"prop_type": "points", "side": "over", "player_id": player},
            {"prop_type": "rebounds", "side": "under", "player_id": player},
        ]
    )

    assert aligned["correlation_used"] > 0
    assert opposed["correlation_used"] < 0


def test_a_supplied_correlation_is_respected_and_labelled() -> None:
    result = _price(correlation=0.25)

    assert result["correlation_source"] == "supplied"
    assert result["correlation_used"] == 0.25


# --------------------------------------------------------------------------------------
# The band is priced, and the verdict is taken on its worst end
# --------------------------------------------------------------------------------------
def test_the_entry_is_priced_across_the_band_not_at_the_point() -> None:
    player = PLAYER_THREE
    result = _price(
        legs=[
            {"prop_type": "points", "side": "over", "player_id": player},
            {"prop_type": "rebounds", "side": "over", "player_id": player},
        ]
    )

    assert result["correlation_low"] < result["correlation_used"] < result["correlation_high"]
    assert result["expected_value_low"] <= result["expected_value_high"]
    assert result["worst_case_expected_value"] <= result["expected_value"]


def test_an_unrepresentable_band_is_clipped_rather_than_rejected() -> None:
    """A one-factor model cannot hold negative correlation across three legs. That is a limit of
    the model, not a user error, so the estimate is clipped and the entry still prices."""
    result = _price(
        entry_type="power",
        leg_probabilities=[0.62, 0.61, 0.60],
        legs=[
            {"prop_type": "points", "side": "over", "player_id": PLAYER_ONE},
            {"prop_type": "rebounds", "side": "under", "player_id": PLAYER_TWO},
            {"prop_type": "assists", "side": "over", "player_id": PLAYER_THREE},
        ],
    )

    assert result["correlation_used"] >= 0.0
    assert result["correlation_low"] >= 0.0


def test_a_supplied_negative_correlation_across_three_legs_is_still_rejected() -> None:
    response = client.post(
        "/api/price",
        json={"leg_probabilities": [0.6, 0.6, 0.6], "correlation": -0.2},
    )

    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


def test_leg_descriptors_must_match_the_probabilities_they_describe() -> None:
    response = client.post(
        "/api/price",
        json={
            "leg_probabilities": [0.6, 0.6],
            "legs": [{"prop_type": "points", "side": "over"}],
        },
    )

    assert response.status_code == 422


# --------------------------------------------------------------------------------------
# Entry suggestions inherit every upstream restriction
# --------------------------------------------------------------------------------------
def _board(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"available": True, "forecasts": rows}


def _row(name: str, prop: str, shrunk: float | None, *, game: str = "g1") -> dict[str, Any]:
    return {
        "projection_id": f"{name}-{prop}",
        "full_name": name,
        "prop_type": prop,
        "side": "over",
        "line": 20.5,
        "shrunk_probability": shrunk,
        "probability_lower_bound": None if shrunk is None else shrunk - 0.01,
        "breakeven_probability": 0.577,
        "predicted_probability": 0.7,
        "system_recommendation": "candidate",
        "player_id": name,
        "team": "LVA",
        "game_id": game,
    }


def test_suggestions_only_consider_forecasts_the_gate_called_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declined = _row("Declined", "points", 0.80)
    declined["system_recommendation"] = "declined_no_edge"
    monkeypatch.setattr(
        main,
        "forecasts",
        lambda: _board(
            [
                declined,
                _row("A", "points", 0.70),
                _row("B", "rebounds", 0.69, game="g2"),
            ]
        ),
    )

    response = client.post("/api/entries/suggest", json={})

    assert response.status_code == 200
    assert response.json()["candidates_considered"] == 2


def test_a_forecast_without_a_shrunk_probability_is_skipped_not_backfilled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back to the raw probability would compound selection bias once per leg."""
    monkeypatch.setattr(
        main,
        "forecasts",
        lambda: _board([_row("A", "points", None), _row("B", "rebounds", 0.69, game="g2")]),
    )

    response = client.post("/api/entries/suggest", json={})

    assert response.json()["candidates_considered"] == 1


def test_an_unavailable_board_suggests_nothing_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main, "forecasts", lambda: {"available": False, "reason": "database unreachable"}
    )

    payload = client.post("/api/entries/suggest", json={}).json()

    assert payload["available"] is False
    assert payload["entries"] == []
    assert "database unreachable" in payload["reason"]
