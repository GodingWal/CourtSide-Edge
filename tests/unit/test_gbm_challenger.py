"""The tree challenger's leakage controls, which are the only reason to trust it at all.

A boosted model will fit whatever it is given and report high confidence about it. Every test
here is an attempt to catch the specific ways this one could be given something it should not
have, or could be credited with an answer it did not produce.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from wnba_services.forecasting.challengers import challenger_names
from wnba_services.forecasting.gbm import (
    FEATURE_NAMES,
    MINIMUM_TRAINING_MARKETS,
    ArtifactMissing,
    GradientBoostedChallenger,
    _chronological_folds,
    features_from_inputs,
)
from wnba_services.forecasting.scoring import HistoryGame, ScoringInputs

BASE = datetime(2026, 5, 1, tzinfo=UTC)


def _game(minutes: float, points: float) -> HistoryGame:
    return HistoryGame(
        minutes=minutes,
        started=True,
        stats={
            "points": points,
            "field_goals_attempted": points * 0.9,
            "rebounds_offensive": 1.0,
            "rebounds_defensive": 3.0,
            "assists": 2.0,
            "three_pointers_made": 1.0,
            "three_pointers_attempted": 3.0,
        },
    )


def _inputs(**overrides: object) -> ScoringInputs:
    defaults: dict[str, object] = {
        "prop_type": "points",
        "line": Decimal("14.5"),
        "history": tuple(_game(30.0, 15.0) for _ in range(18)),
        "league_rate_per_minute": 0.45,
    }
    defaults.update(overrides)
    return ScoringInputs(**defaults)  # type: ignore[arg-type]


def _rows(markets: int, snapshots: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market in range(markets):
        for _ in range(snapshots):
            rows.append(
                {
                    "as_of": BASE + timedelta(days=market),
                    "player_id": f"p{market % 12}",
                    "game_id": f"g{market}",
                    "prop_type": "points",
                }
            )
    return rows


# --------------------------------------------------------------------------------------
# Leakage control 1: features are the champion's own evidence
# --------------------------------------------------------------------------------------
def test_the_feature_vector_matches_the_declared_vocabulary_exactly() -> None:
    """A feature produced but undeclared is silently dropped at serving time."""
    produced = set(features_from_inputs(_inputs()))

    assert produced == set(FEATURE_NAMES)


def test_features_come_only_from_scoring_inputs() -> None:
    """The tree sees the champion's evidence, so a difference is a difference in modelling.

    Enforced by the signature: ``features_from_inputs`` takes ``ScoringInputs`` and no cursor, so
    there is no path by which it could read a row the champion could not.
    """
    import inspect

    signature = inspect.signature(features_from_inputs)

    assert list(signature.parameters) == ["inputs"]
    # `from __future__ import annotations` makes the annotation a string, so compare the name.
    assert signature.parameters["inputs"].annotation == "ScoringInputs"


# --------------------------------------------------------------------------------------
# Leakage control 2 and 3: chronological folds, split on markets
# --------------------------------------------------------------------------------------
def test_no_fold_trains_on_the_future() -> None:
    rows = _rows(60)

    for train, validate in _chronological_folds(rows):
        latest_train = max(rows[index]["as_of"] for index in train)
        earliest_validation = min(rows[index]["as_of"] for index in validate)
        assert latest_train <= earliest_validation


def test_a_market_never_straddles_the_fold_wall() -> None:
    """Five snapshots of one market are one event resolving one way. Splitting them leaks it."""
    rows = _rows(60)

    for train, validate in _chronological_folds(rows):
        train_markets = {(rows[i]["player_id"], rows[i]["game_id"]) for i in train}
        validate_markets = {(rows[i]["player_id"], rows[i]["game_id"]) for i in validate}
        assert not (train_markets & validate_markets)


def test_too_few_markets_produces_no_folds_rather_than_a_degenerate_one() -> None:
    assert _chronological_folds(_rows(3)) == []


# --------------------------------------------------------------------------------------
# Leakage control 4: the market prior is a feature, so it is also the baseline
# --------------------------------------------------------------------------------------
def test_the_market_price_is_a_feature() -> None:
    """Which is exactly why held-out loss must be reported against the price, not a naive mean."""
    assert "market_over_probability" in FEATURE_NAMES
    assert "market_is_informative" in FEATURE_NAMES


def test_an_uninformative_market_is_flagged_rather_than_imputed() -> None:
    """A flat pick'em board carries no price information and must not look like a 50% signal."""
    features = features_from_inputs(_inputs())

    assert features["market_is_informative"] == 0.0


# --------------------------------------------------------------------------------------
# Failing loudly
# --------------------------------------------------------------------------------------
def test_an_unfitted_challenger_raises_rather_than_guessing() -> None:
    """Returning 0.5 or the champion's answer would be a model that appears to have run."""
    with pytest.raises(ArtifactMissing, match="no active gradient-boosted artifact"):
        GradientBoostedChallenger().predict(_inputs())


def test_the_family_is_registered_even_before_it_is_fitted() -> None:
    """A family that vanished from the registry would make 'never compared' look like 'lost'."""
    assert "gradient-boosted" in challenger_names()


def test_the_training_gate_is_in_markets_not_rows() -> None:
    assert MINIMUM_TRAINING_MARKETS >= 400


def test_the_specification_records_what_it_had_to_beat() -> None:
    specification = GradientBoostedChallenger().specification()

    assert specification["baseline_that_must_be_beaten"] == "devigged market prior"
    assert specification["serialisation"] == "plain text booster, never a pickle"
    assert specification["analysis_only"] is True
