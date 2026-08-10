"""The stacked gradient-boosting challenger (platform plan section 7)."""

from __future__ import annotations

import math
from decimal import Decimal
from types import SimpleNamespace

import pytest
from wnba_services.forecasting.boosting import (
    FEATURE_NAMES,
    GradientBoostingChallenger,
    _feature_vector,
    fit_boosted_challengers,
)
from wnba_services.forecasting.scoring import COMPONENT_NAMES


def test_feature_vector_has_fixed_order_and_length() -> None:
    vector = _feature_vector(
        component_means={"empirical": 20.1},
        component_overs={"market_prior": 0.55},
        line=19.5,
        champion_mean=20.4,
        champion_stddev=5.1,
        champion_over=0.57,
        disagreement=0.03,
        data_quality=0.95,
    )
    assert len(vector) == len(FEATURE_NAMES)
    assert vector[0] == 20.1  # empirical mean first
    assert vector[len(COMPONENT_NAMES)] == 0.0  # empirical over absent, zeroed
    assert vector[-1] == 0.95  # data quality last


def _inputs() -> SimpleNamespace:
    return SimpleNamespace(prop_type="points", line=Decimal("19.5"))


def _champion() -> SimpleNamespace:
    components = tuple(
        SimpleNamespace(name=name, mean=20.0, over=0.55)
        for name in COMPONENT_NAMES
    )
    return SimpleNamespace(
        components=components,
        mean=20.2,
        stddev=5.0,
        over=0.56,
        disagreement=0.02,
        data_quality_score=0.95,
        dispersion=1.1,
    )


def test_predict_requires_the_champion_forecast() -> None:
    challenger = GradientBoostingChallenger()
    with pytest.raises(ValueError, match="stacks on the champion"):
        challenger.predict(_inputs())


def test_predict_without_an_artifact_fails_loudly(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WNBA_MODEL_DIR", str(tmp_path))
    challenger = GradientBoostingChallenger()
    with pytest.raises(ValueError, match="not fitted"):
        challenger.predict(_inputs(), _champion())


class _Rows:
    """The one-query connection fit_boosted_challengers reads its training set from."""

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def __enter__(self) -> _Rows:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def cursor(self) -> _Rows:
        return self

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        pass

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


def _training_rows(count: int) -> list[dict[str, object]]:
    import random

    rng = random.Random(7)
    rows: list[dict[str, object]] = []
    for index in range(count):
        champion_mean = 20.0 + rng.gauss(0, 1)
        # The booster has something to learn: outcomes run 5% above the champion's mean.
        actual = champion_mean * 1.05 + rng.gauss(0, 3)
        rows.append(
            {
                "prop_type": "points",
                "line": 19.5,
                "projected_mean": champion_mean,
                "model_disagreement": 0.02,
                "data_quality_score": 0.95,
                "forecast_timestamp": (
                    f"2026-07-{(index % 28) + 1:02d}T{index % 24:02d}:00:00+00:00"
                ),
                "champion_stddev": 5.0,
                "champion_mean": champion_mean,
                "champion_over": 0.56,
                "actual_stat": actual,
                "means": dict.fromkeys(COMPONENT_NAMES, champion_mean),
                "overs": dict.fromkeys(COMPONENT_NAMES, 0.55),
            }
        )
    return rows


def test_fit_then_predict_is_pure_and_learns_the_bias(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("lightgbm")
    monkeypatch.setenv("WNBA_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(
        "wnba_services.forecasting.boosting.connect",
        lambda: _Rows(_training_rows(500)),
    )
    reports = fit_boosted_challengers(minimum_rows=100)
    assert [report.prop_type for report in reports] == ["points"]
    assert reports[0].booster_mae < reports[0].champion_mae

    challenger = GradientBoostingChallenger()
    first = challenger.predict(_inputs(), _champion())
    second = challenger.predict(_inputs(), _champion())
    assert first.over == second.over
    assert math.isclose(math.fsum(first.pmf), 1.0, abs_tol=1e-9)
    assert 0.0 <= first.over <= 1.0
