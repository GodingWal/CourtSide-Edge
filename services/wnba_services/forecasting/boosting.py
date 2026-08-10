"""Gradient-boosted challenger: a stacked correction over the champion's own components.

The platform plan's ensemble has always named gradient boosting as the third family. What kept
it unbuilt was not the algorithm, it was the training set: a tree model needs point-in-time
features, and rebuilding the champion's inputs for thirty thousand settled episodes would mean
re-deriving history, roles, teammate effects and matchups as of every forecast timestamp.

The shortcut that stays honest is stacking, which the plan itself prescribes ("the final
probability can be stacked"). The booster's features are the champion's five component means
and probabilities, the line, and the champion's own summary statistics -- every one of them
stored per episode at forecast time in ``forecast_components`` and ``decision_episodes``, and
every one reproducible at predict time from the :class:`ScoredForecast` the runner already
holds. The booster learns where the ensemble's blended answer is systematically wrong, not a
private view of the world.

Leakage controls:

* Training reads only rows written at forecast time, never outcomes-adjacent tables.
* The split is walk-forward on ``forecast_timestamp`` -- first 80% train, last 20% validation.
* The artifact records both MAEs; the champion's number is written next to the booster's so a
  fit that does not beat the champion is visible instead of silently worse.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
from wnba_store.db import connect

from wnba_services.forecasting.distributions import count_pmf, line_probabilities
from wnba_services.forecasting.scoring import COMPONENT_NAMES, ScoredForecast, ScoringInputs

if TYPE_CHECKING:
    from wnba_services.forecasting.challengers import ChallengerPrediction

FEATURE_NAMES: tuple[str, ...] = (
    tuple(f"mean_{name}" for name in COMPONENT_NAMES)
    + tuple(f"over_{name}" for name in COMPONENT_NAMES)
    + ("line", "champion_mean", "champion_stddev", "champion_over", "disagreement", "data_quality")
)

NAME = "gradient-boosting"
MINIMUM_TRAIN_ROWS = 400


def _model_dir() -> Path:
    return Path(os.getenv("WNBA_MODEL_DIR", "/var/lib/wnba/models"))


def _feature_vector(
    *,
    component_means: dict[str, float],
    component_overs: dict[str, float],
    line: float,
    champion_mean: float,
    champion_stddev: float,
    champion_over: float,
    disagreement: float,
    data_quality: float,
) -> list[float]:
    """One row in the fixed FEATURE_NAMES order; missing components are zeroed, not guessed."""
    return (
        [component_means.get(name, 0.0) for name in COMPONENT_NAMES]
        + [component_overs.get(name, 0.0) for name in COMPONENT_NAMES]
        + [line, champion_mean, champion_stddev, champion_over, disagreement, data_quality]
    )


def _features_from_champion(inputs: ScoringInputs, champion: ScoredForecast) -> list[float]:
    by_name = {component.name: component for component in champion.components}
    return _feature_vector(
        component_means={name: by_name[name].mean for name in COMPONENT_NAMES if name in by_name},
        component_overs={name: by_name[name].over for name in COMPONENT_NAMES if name in by_name},
        line=float(inputs.line),
        champion_mean=champion.mean,
        champion_stddev=champion.stddev,
        champion_over=champion.over,
        disagreement=champion.disagreement,
        data_quality=champion.data_quality_score,
    )


@dataclass(frozen=True)
class FitReport:
    prop_type: str
    train_rows: int
    validation_rows: int
    champion_mae: float
    booster_mae: float
    artifact: str


def fit_boosted_challengers(
    *, now: datetime | None = None, minimum_rows: int = MINIMUM_TRAIN_ROWS
) -> list[FitReport]:
    """Fit one booster per market on the stored record, walk-forward.

    Writes a LightGBM artifact plus a JSON sidecar per market into ``WNBA_MODEL_DIR``.
    Markets without enough settled history are skipped, not fitted on hope.
    """
    import lightgbm as lgb

    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT d.prop_type,d.line,d.projected_mean,d.model_disagreement,
                      d.data_quality_score,d.forecast_timestamp,
                      f.stddev AS champion_stddev,f.mean AS champion_mean,
                      f.probability_over AS champion_over,
                      o.actual_stat,
                      (SELECT jsonb_object_agg(component_name, mean)
                         FROM wnba.forecast_components fc
                        WHERE fc.projection_id=f.projection_id) AS means,
                      (SELECT jsonb_object_agg(component_name, probability_over)
                         FROM wnba.forecast_components fc
                        WHERE fc.projection_id=f.projection_id) AS overs
               FROM wnba.decision_episodes d
               JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
               JOIN wnba.stat_forecasts f
                 ON f.model_run_id=d.model_run_id AND f.quote_id=d.quote_id
               WHERE NOT o.was_voided AND NOT o.was_push
               ORDER BY d.forecast_timestamp"""
        )
        rows = [dict(row) for row in cur.fetchall()]

    reports: list[FitReport] = []
    model_dir = _model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    for prop_type in sorted({str(row["prop_type"]) for row in rows}):
        market_rows = [row for row in rows if str(row["prop_type"]) == prop_type]
        if len(market_rows) < minimum_rows:
            continue
        split = int(len(market_rows) * 0.8)
        train, validation = market_rows[:split], market_rows[split:]

        def _xy(part: list[dict[str, Any]]) -> tuple[list[list[float]], list[float]]:
            features = [
                _feature_vector(
                    component_means={
                        key: float(str(val)) for key, val in (row["means"] or {}).items()
                    },
                    component_overs={
                        key: float(str(val)) for key, val in (row["overs"] or {}).items()
                    },
                    line=float(str(row["line"])),
                    champion_mean=float(str(row["champion_mean"])),
                    champion_stddev=float(str(row["champion_stddev"])),
                    champion_over=float(str(row["champion_over"])),
                    disagreement=float(str(row["model_disagreement"])),
                    data_quality=float(str(row["data_quality_score"])),
                )
                for row in part
            ]
            return features, [float(str(row["actual_stat"])) for row in part]

        x_train, y_train = _xy(train)
        x_valid, y_valid = _xy(validation)
        booster = lgb.train(
            {
                "objective": "regression",
                "learning_rate": 0.05,
                "num_leaves": 31,
                "min_data_in_leaf": 40,
                "feature_fraction": 0.8,
                "verbose": -1,
                "seed": 0,
            },
            lgb.Dataset(np.asarray(x_train), label=y_train, feature_name=list(FEATURE_NAMES)),
            num_boost_round=300,
            valid_sets=[lgb.Dataset(np.asarray(x_valid), label=y_valid)],
        )
        predicted = booster.predict(x_valid)
        booster_mae = sum(abs(p - y) for p, y in zip(predicted, y_valid, strict=True)) / len(
            y_valid
        )
        champion_mae = sum(
            abs(float(str(row["projected_mean"])) - float(str(row["actual_stat"])))
            for row in validation
        ) / len(validation)

        artifact = model_dir / f"boosted_{prop_type}.txt"
        booster.save_model(str(artifact))
        (model_dir / f"boosted_{prop_type}.json").write_text(
            json.dumps(
                {
                    "name": NAME,
                    "prop_type": prop_type,
                    "trained_at": at.isoformat(),
                    "train_rows": len(train),
                    "validation_rows": len(validation),
                    "champion_mae": round(champion_mae, 4),
                    "booster_mae": round(booster_mae, 4),
                    "feature_names": list(FEATURE_NAMES),
                },
                indent=2,
            )
        )
        reports.append(
            FitReport(
                prop_type=prop_type,
                train_rows=len(train),
                validation_rows=len(validation),
                champion_mae=round(champion_mae, 4),
                booster_mae=round(booster_mae, 4),
                artifact=str(artifact),
            )
        )
    return reports


class GradientBoostingChallenger:
    """The stacked tree model, scoring from the champion's components at run time."""

    name = NAME
    version = "boosted-v1"

    def __init__(self) -> None:
        self._boosters: dict[str, Any] = {}

    def specification(self) -> dict[str, Any]:
        return {
            "family": NAME,
            "kind": "stacked LightGBM regression over champion components",
            "features": list(FEATURE_NAMES),
            "leakage_controls": ["forecast-time features only", "walk-forward validation"],
        }

    def _booster_for(self, prop_type: str) -> Any:
        import lightgbm as lgb

        if prop_type not in self._boosters:
            artifact = _model_dir() / f"boosted_{prop_type}.txt"
            if not artifact.exists():
                raise ValueError(f"{NAME} challenger is not fitted for {prop_type!r}")
            self._boosters[prop_type] = lgb.Booster(model_file=str(artifact))
        return self._boosters[prop_type]

    def predict(
        self, inputs: ScoringInputs, champion: ScoredForecast | None = None
    ) -> ChallengerPrediction:
        """Score one prop. Needs the champion's forecast: stacking is the design."""
        # Imported here, not at module top: challengers.py registers this class, so a
        # top-level import in both directions is a cycle.
        from wnba_services.forecasting.challengers import ChallengerPrediction, _pmf_length

        if champion is None:
            raise ValueError(f"{NAME} stacks on the champion forecast; none was supplied")
        booster = self._booster_for(inputs.prop_type)
        mean = max(0.0, float(booster.predict([_features_from_champion(inputs, champion)])[0]))
        length = _pmf_length(inputs)
        dispersion = champion.dispersion if math.isfinite(champion.dispersion) else 1.0
        pmf = count_pmf(mean, max(0.05, dispersion), length)
        over, push, under = line_probabilities(pmf, inputs.line)
        stddev = math.sqrt(sum(((i - mean) ** 2) * p for i, p in enumerate(pmf)))
        return ChallengerPrediction(
            name=self.name,
            version=self.version,
            pmf=pmf,
            mean=mean,
            stddev=stddev,
            over=over,
            push=push,
            under=under,
            diagnostics={"stacked_on": "production_ensemble", "features": len(FEATURE_NAMES)},
        )
