"""The gradient-boosted challenger, and the training pipeline that makes it honest.

This was previously left unbuilt on the grounds that a tree model needs a dependency, a training
set and its own leakage controls, and that a stub wrapping the five existing components would
populate the experiments table without creating anything to learn from. That reasoning was about
*how* to build it, not whether. Here it is built.

WHAT MAKES IT A GENUINELY DIFFERENT FAMILY
------------------------------------------
Every other model here is generative: it forms a rate, multiplies by minutes, and integrates a
count distribution. The probability comes out of a distributional assumption. This one is
discriminative -- it learns P(over | features) directly from settled outcomes, with no count
distribution anywhere in the path. It can represent things the generative family structurally
cannot: an interaction between blowout risk and starter probability that only matters above a
certain line, a market whose price is informative at short lead times and noise at long ones.

It can also learn artefacts of the sample with total confidence, which is why everything below
is arranged around not letting it.

LEAKAGE CONTROL, IN FOUR PLACES
-------------------------------
1. **The features are the replay's own inputs.** Training rows are written by the walk-forward
   replay as it runs, from the `ScoringInputs` it constructed under point-in-time rules. There is
   no second reconstruction of history that could accidentally see further than the replay did.

2. **Folds are chronological, never random.** A random split lets the model train on next
   Tuesday and predict last Tuesday, which inflates every metric and is undetectable in the
   result. Each fold trains on everything before a cut and validates on the window after it.

3. **Markets, not rows, decide the split.** One market appears at five snapshots. Splitting rows
   at random would put the 6h snapshot in train and the 2h snapshot in validation -- the same
   event, resolving the same way, on both sides of the wall.

4. **The market prior is the baseline it must beat.** The devigged price is a feature, so a model
   that has learned nothing but the price will still look excellent against a naive baseline.
   Held-out log loss is reported against the market prior over the identical rows, and a model
   that cannot beat the price it was shown has learned the price.

WHAT HAPPENS WHEN THERE IS NO FITTED MODEL
------------------------------------------
The challenger raises. It does not fall back to the champion's answer, and it does not return
0.5. Both would be a model that looks like it ran; the experiment runner records the raise as a
failure with its reason, and failure rate is one of the things the comparison measures.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb
from wnba_domain.decision import brier_score, log_loss

from wnba_services.forecasting.challenger_types import ChallengerPrediction
from wnba_services.forecasting.distributions import (
    DEFAULT_DISPERSION,
    LEAGUE_DISPERSION,
    MAX_OUTCOME,
    count_pmf,
    line_probabilities,
    pmf_mean,
    pmf_stddev,
)
from wnba_services.forecasting.recency import MARKET_HALF_LIFE, weighted_mean, weighted_ratio
from wnba_services.forecasting.scoring import (
    STAT_GROUPS,
    ScoringInputs,
    resolve_adjustments,
    resolve_dispersion,
)

__all__ = [
    "FEATURE_NAMES",
    "MINIMUM_TRAINING_MARKETS",
    "ArtifactMissing",
    "GradientBoostedChallenger",
    "TrainingResult",
    "features_from_inputs",
    "record_training_example",
    "train_gradient_boosted",
]


class ArtifactMissing(RuntimeError):
    """Raised when the challenger is asked to predict with no fitted model available."""


# Below this many independent markets a tree model is memorising, not learning. Trees are very
# good at fitting three hundred rows exactly and reporting that they have done so.
MINIMUM_TRAINING_MARKETS = 400

# Validation windows for the chronological walk-forward. Each fold trains on everything before
# its cut and validates on the slice after.
_FOLDS = 4

# Deliberately small trees. The training set is thousands of rows, not millions, and depth is
# where a boosted model turns a season's noise into confident structure.
_HYPERPARAMETERS: dict[str, Any] = {
    "objective": "binary",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "max_depth": 4,
    "min_data_in_leaf": 60,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbose": -1,
    "deterministic": True,
    "seed": 20260805,
}
_NUM_ROUNDS = 400
_EARLY_STOPPING = 40

# The ordered feature vocabulary. Order is part of the artifact: a model fitted on one ordering
# and served on another is silently wrong rather than broken, which is the worst kind.
FEATURE_NAMES: tuple[str, ...] = (
    "line",
    "expected_minutes",
    "minutes_std",
    "start_probability",
    "rate_multiplier",
    "matchup_multiplier",
    "rest_rate_multiplier",
    "history_games",
    "played_games",
    "season_mean",
    "last_five_mean",
    "history_std",
    "rate_per_minute",
    "weighted_rate_per_minute",
    "league_rate_per_minute",
    "rate_versus_league",
    "minutes_rate_projection",
    "projection_minus_line",
    "dispersion",
    "market_over_probability",
    "market_is_informative",
    "injury_is_questionable",
    "has_role_projection",
    "teammate_effect_count",
    "prop_group_count",
)


@dataclass(frozen=True)
class TrainingResult:
    artifact_id: Any
    rows: int
    markets: int
    holdout_log_loss: float | None
    holdout_brier: float | None
    baseline_log_loss: float | None
    folds: tuple[dict[str, Any], ...]
    activated: bool
    detail: str

    @property
    def beats_market(self) -> bool:
        if self.holdout_log_loss is None or self.baseline_log_loss is None:
            return False
        return self.holdout_log_loss < self.baseline_log_loss


def features_from_inputs(inputs: ScoringInputs) -> dict[str, float]:
    """The feature vector, derived only from what :func:`score_prop` was allowed to see.

    Taking ``ScoringInputs`` rather than a database row is what keeps the comparison fair and the
    leakage controls intact: the tree model sees exactly the champion's evidence, so a difference
    between them is a difference in modelling. A tree fed richer inputs would be measuring the
    inputs.
    """
    adjustments = resolve_adjustments(inputs)
    columns = inputs.columns
    half_life = MARKET_HALF_LIFE.get(inputs.prop_type, 8.0)

    totals = [game.total(columns) for game in inputs.history]
    played = [game for game in inputs.history if game.minutes > 0.0]
    minutes = [game.minutes for game in played]
    played_totals = [game.total(columns) for game in played]

    season_mean = math.fsum(totals) / len(totals) if totals else 0.0
    recent = totals[-5:]
    last_five = math.fsum(recent) / len(recent) if recent else 0.0
    variance = (
        math.fsum((value - season_mean) ** 2 for value in totals) / (len(totals) - 1)
        if len(totals) > 1
        else 0.0
    )
    total_minutes = math.fsum(minutes)
    rate = math.fsum(played_totals) / total_minutes if total_minutes > 0 else 0.0
    weighted_rate = weighted_ratio(played_totals, minutes, half_life) if played else 0.0
    expected_minutes_history = weighted_mean(minutes[-10:], half_life) if minutes else 0.0
    minutes_rate_projection = expected_minutes_history * weighted_rate

    market = inputs.market
    market_over = 0.5
    market_informative = 0.0
    if market is not None and market.is_informative and market.over_probability is not None:
        market_over = float(market.over_probability)
        market_informative = 1.0

    league = max(1e-9, inputs.league_rate_per_minute)
    return {
        "line": float(inputs.line),
        "expected_minutes": adjustments.expected_minutes,
        "minutes_std": adjustments.minutes_std,
        "start_probability": adjustments.start_probability,
        "rate_multiplier": adjustments.rate_multiplier,
        "matchup_multiplier": adjustments.matchup_multiplier,
        "rest_rate_multiplier": adjustments.rest_rate_multiplier,
        "history_games": float(len(inputs.history)),
        "played_games": float(len(played)),
        "season_mean": season_mean,
        "last_five_mean": last_five,
        "history_std": math.sqrt(max(0.0, variance)),
        "rate_per_minute": rate,
        "weighted_rate_per_minute": weighted_rate,
        "league_rate_per_minute": inputs.league_rate_per_minute,
        "rate_versus_league": rate / league,
        "minutes_rate_projection": minutes_rate_projection,
        "projection_minus_line": minutes_rate_projection - float(inputs.line),
        "dispersion": resolve_dispersion(inputs),
        "market_over_probability": market_over,
        "market_is_informative": market_informative,
        "injury_is_questionable": 1.0 if inputs.injury_designation == "questionable" else 0.0,
        "has_role_projection": 1.0
        if inputs.role is not None and inputs.role.expected_minutes is not None
        else 0.0,
        "teammate_effect_count": float(
            0 if inputs.teammate is None else inputs.teammate.effect_count
        ),
        "prop_group_count": float(len(STAT_GROUPS.get(inputs.prop_type, ((),)))),
    }


def _vector(features: Mapping[str, float]) -> list[float]:
    """Features in the artifact's declared order, with a stated default for anything absent."""
    return [float(features.get(name, 0.0)) for name in FEATURE_NAMES]


def record_training_example(
    cur: Any,
    *,
    backtest_run_id: Any,
    historical_quote_id: Any,
    snapshot_label: str,
    as_of: datetime,
    game_id: Any,
    player_id: Any,
    inputs: ScoringInputs,
    actual: float,
) -> None:
    """Persist one training row from the replay's own point-in-time inputs.

    Called from inside the walk-forward replay, which is the only place in the codebase that
    knows how to see the world as it looked at a past moment. Duplicating that knowledge here is
    how the training set would quietly start seeing further than the replay.
    """
    cur.execute(
        """INSERT INTO wnba.training_examples
           (example_id,backtest_run_id,historical_quote_id,snapshot_label,as_of,game_id,
            player_id,prop_type,line,features,label,actual_stat)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (backtest_run_id,historical_quote_id,snapshot_label) DO NOTHING""",
        (
            uuid4(),
            backtest_run_id,
            historical_quote_id,
            snapshot_label,
            as_of,
            game_id,
            player_id,
            inputs.prop_type,
            inputs.line,
            Jsonb(features_from_inputs(inputs)),
            actual > float(inputs.line),
            actual,
        ),
    )


def _market_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(row["player_id"]), str(row["game_id"]), str(row["prop_type"]))


def _chronological_folds(
    rows: Sequence[Mapping[str, Any]], folds: int = _FOLDS
) -> list[tuple[list[int], list[int]]]:
    """Expanding-window folds split on *market* boundaries, in time order.

    Two properties matter and both are easy to lose. The split is chronological, so no fold ever
    trains on the future. And it cuts between markets rather than between rows, so the five
    snapshots of one market -- the same event, resolving the same way -- never straddle the wall.
    """
    order: list[tuple[Any, tuple[str, str, str]]] = []
    seen: dict[tuple[str, str, str], Any] = {}
    for row in rows:
        key = _market_key(row)
        if key not in seen:
            seen[key] = row["as_of"]
            order.append((row["as_of"], key))
    order.sort(key=lambda item: item[0])
    markets = [key for _, key in order]
    if len(markets) < folds * 2:
        return []

    by_market: dict[tuple[str, str, str], list[int]] = {}
    for index, row in enumerate(rows):
        by_market.setdefault(_market_key(row), []).append(index)

    step = len(markets) // (folds + 1)
    result: list[tuple[list[int], list[int]]] = []
    for fold in range(1, folds + 1):
        cut = step * fold
        train_markets = markets[:cut]
        validate_markets = markets[cut : cut + step]
        if not train_markets or not validate_markets:
            continue
        train = [index for key in train_markets for index in by_market.get(key, [])]
        validate = [index for key in validate_markets for index in by_market.get(key, [])]
        if train and validate:
            result.append((train, validate))
    return result


def train_gradient_boosted(
    *,
    now: datetime | None = None,
    activate: bool = False,
    semver: str = "0.1.0",
) -> TrainingResult:
    """Fit the tree challenger on replay-derived training rows and store the artifact.

    ``activate`` is False by default. Fitting a model and making it the artifact the challenger
    serves are two decisions, and the second one deserves to be typed on purpose.
    """
    try:
        import lightgbm as lgb
        import numpy as np
    except ImportError as error:  # pragma: no cover - exercised by the optional-extra path
        raise ArtifactMissing(
            "lightgbm is not installed; install the 'models' optional dependency group"
        ) from error

    from wnba_store.db import connect

    at = now or datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT example_id,as_of,player_id,game_id,prop_type,line,features,label
               FROM wnba.training_examples ORDER BY as_of"""
        )
        rows = [dict(row) for row in cur.fetchall()]
        markets = len({_market_key(row) for row in rows})
        if markets < MINIMUM_TRAINING_MARKETS:
            return TrainingResult(
                artifact_id=None,
                rows=len(rows),
                markets=markets,
                holdout_log_loss=None,
                holdout_brier=None,
                baseline_log_loss=None,
                folds=(),
                activated=False,
                detail=(
                    f"{markets} independent training markets is below the "
                    f"{MINIMUM_TRAINING_MARKETS} a boosted model needs before it is learning "
                    "rather than memorising. Run the walk-forward replay to accumulate more."
                ),
            )

        # JSONB arrives typed as `object`. Refusing anything that is not a mapping keeps a
        # driver change from feeding the model a silently empty feature vector, which trees
        # tolerate cheerfully and then report high confidence about.
        stored: list[dict[str, float]] = []
        for row in rows:
            payload = row["features"]
            if not isinstance(payload, dict):
                raise ValueError(f"training example {row['example_id']} has non-object features")
            stored.append({str(key): float(value) for key, value in payload.items()})

        # LightGBM takes arrays, not lists of lists. Building them once here rather than per
        # fold keeps the folds cheap and, more importantly, keeps every fold looking at exactly
        # the same matrix.
        features = np.asarray([_vector(payload) for payload in stored], dtype=np.float64)
        labels = np.asarray([1 if bool(row["label"]) else 0 for row in rows], dtype=np.int32)
        priors = [payload.get("market_over_probability", 0.5) for payload in stored]

        fold_metrics: list[dict[str, Any]] = []
        holdout_logs: list[float] = []
        holdout_briers: list[float] = []
        baseline_logs: list[float] = []
        best_rounds: list[int] = []

        for index, (train_idx, validate_idx) in enumerate(_chronological_folds(rows)):
            train_rows = np.asarray(train_idx, dtype=np.int64)
            validate_rows = np.asarray(validate_idx, dtype=np.int64)
            train_set = lgb.Dataset(
                features[train_rows],
                label=labels[train_rows],
                feature_name=list(FEATURE_NAMES),
            )
            validate_set = lgb.Dataset(
                features[validate_rows],
                label=labels[validate_rows],
                feature_name=list(FEATURE_NAMES),
                reference=train_set,
            )
            booster = lgb.train(
                _HYPERPARAMETERS,
                train_set,
                num_boost_round=_NUM_ROUNDS,
                valid_sets=[validate_set],
                callbacks=[lgb.early_stopping(_EARLY_STOPPING, verbose=False)],
            )
            predictions = booster.predict(
                features[validate_rows], num_iteration=booster.best_iteration
            )
            fold_log = math.fsum(
                log_loss(float(p), labels[i])
                for p, i in zip(predictions, validate_idx, strict=True)
            ) / len(validate_idx)
            fold_brier = math.fsum(
                brier_score(float(p), labels[i])
                for p, i in zip(predictions, validate_idx, strict=True)
            ) / len(validate_idx)
            fold_baseline = math.fsum(log_loss(priors[i], labels[i]) for i in validate_idx) / len(
                validate_idx
            )
            holdout_logs.append(fold_log)
            holdout_briers.append(fold_brier)
            baseline_logs.append(fold_baseline)
            best_rounds.append(int(booster.best_iteration or _NUM_ROUNDS))
            fold_metrics.append(
                {
                    "fold": index + 1,
                    "train_rows": len(train_idx),
                    "validate_rows": len(validate_idx),
                    "log_loss": round(fold_log, 5),
                    "brier": round(fold_brier, 5),
                    "market_prior_log_loss": round(fold_baseline, 5),
                    "beats_market_prior": fold_log < fold_baseline,
                    "best_iteration": int(booster.best_iteration or _NUM_ROUNDS),
                }
            )

        if not fold_metrics:
            return TrainingResult(
                artifact_id=None,
                rows=len(rows),
                markets=markets,
                holdout_log_loss=None,
                holdout_brier=None,
                baseline_log_loss=None,
                folds=(),
                activated=False,
                detail="Too few markets to build a chronological fold structure.",
            )

        # The shipped model is refit on everything, at the average number of rounds the folds
        # found useful. Using a fold's booster would ship a model trained on a strict subset of
        # the evidence; using an unbounded round count would ship one nobody validated.
        rounds = max(20, int(math.fsum(best_rounds) / len(best_rounds)))
        final = lgb.train(
            _HYPERPARAMETERS,
            lgb.Dataset(features, label=labels, feature_name=list(FEATURE_NAMES)),
            num_boost_round=rounds,
        )
        payload = final.model_to_string()
        digest = hashlib.sha256(payload.encode()).hexdigest()

        holdout_log = math.fsum(holdout_logs) / len(holdout_logs)
        holdout_brier = math.fsum(holdout_briers) / len(holdout_briers)
        baseline_log = math.fsum(baseline_logs) / len(baseline_logs)
        beats = holdout_log < baseline_log

        artifact_id = uuid4()
        if activate and beats:
            cur.execute(
                "UPDATE wnba.model_artifacts SET is_active=false WHERE name=%s AND is_active",
                ("gradient-boosted",),
            )
        cur.execute(
            """INSERT INTO wnba.model_artifacts
               (artifact_id,name,semver,fitted_at,framework,payload,payload_sha256,feature_names,
                training_rows,training_markets,trained_through,hyperparameters,fold_metrics,
                holdout_log_loss,holdout_brier,baseline_log_loss,is_active,notes)
               VALUES (%s,'gradient-boosted',%s,%s,'lightgbm',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                       %s,%s)""",
            (
                artifact_id,
                semver,
                at,
                payload,
                digest,
                list(FEATURE_NAMES),
                len(rows),
                markets,
                rows[-1]["as_of"],
                Jsonb({**_HYPERPARAMETERS, "num_boost_round": rounds}),
                Jsonb(fold_metrics),
                holdout_log,
                holdout_brier,
                baseline_log,
                activate and beats,
                (
                    "Chronological market-level folds; market prior is the baseline it must beat."
                    if beats
                    else "Does not beat the market prior it was shown; not activated."
                ),
            ),
        )

    return TrainingResult(
        artifact_id=artifact_id,
        rows=len(rows),
        markets=markets,
        holdout_log_loss=holdout_log,
        holdout_brier=holdout_brier,
        baseline_log_loss=baseline_log,
        folds=tuple(fold_metrics),
        activated=activate and beats,
        detail=(
            f"Held-out log loss {holdout_log:.5f} against market prior {baseline_log:.5f} "
            f"over {len(fold_metrics)} chronological folds."
            + ("" if beats else " Did not beat the price it was shown, so it learned the price.")
        ),
    )


class GradientBoostedChallenger:
    """Discriminative challenger: P(over) straight from features, no count distribution.

    The artifact is loaded lazily and cached. When none exists the challenger raises rather than
    guessing -- a fallback to 0.5 or to the champion's answer would be a model that appears to
    have run, and the experiment framework measures failure rate precisely so that a family which
    cannot answer is not quietly credited with the answers it skipped.
    """

    name = "gradient-boosted"
    version = "0.1.0"

    def __init__(self) -> None:
        self._booster: Any | None = None
        self._feature_names: tuple[str, ...] = FEATURE_NAMES
        self._loaded = False

    def specification(self) -> dict[str, Any]:
        return {
            "family": "gradient_boosted_trees",
            "framework": "lightgbm",
            "discriminative": True,
            "features": list(FEATURE_NAMES),
            "training_set": "walk_forward_replay_point_in_time_inputs",
            "folds": "chronological, split on market boundaries",
            "baseline_that_must_be_beaten": "devigged market prior",
            "serialisation": "plain text booster, never a pickle",
            "analysis_only": True,
        }

    def load(self, cur: Any) -> bool:
        """Load the active artifact. Returns whether one was found."""
        try:
            import lightgbm as lgb
        except ImportError as error:  # pragma: no cover - optional-extra path
            raise ArtifactMissing(
                "lightgbm is not installed; install the 'models' optional dependency group"
            ) from error

        cur.execute(
            """SELECT payload,feature_names FROM wnba.model_artifacts
               WHERE name=%s AND is_active ORDER BY fitted_at DESC LIMIT 1""",
            (self.name,),
        )
        row = cur.fetchone()
        self._loaded = True
        if row is None:
            self._booster = None
            return False
        self._booster = lgb.Booster(model_str=str(row["payload"]))
        stored = row["feature_names"]
        # Serving a model against a different feature order than it was fitted on is silently
        # wrong rather than broken, so the order travels with the artifact and is checked.
        self._feature_names = (
            tuple(str(name) for name in stored) if isinstance(stored, list) else FEATURE_NAMES
        )
        return True

    def predict(self, inputs: ScoringInputs) -> ChallengerPrediction:
        if self._booster is None:
            raise ArtifactMissing(
                "no active gradient-boosted artifact; run `wnba forecast train-gbm --activate` "
                "after the walk-forward replay has accumulated training rows"
            )
        import numpy as np

        features = features_from_inputs(inputs)
        vector = np.asarray(
            [[float(features.get(name, 0.0)) for name in self._feature_names]], dtype=np.float64
        )
        raw_over = float(self._booster.predict(vector)[0])
        over = max(1e-6, min(1.0 - 1e-6, raw_over))
        calibrated_over = inputs.calibration.apply(over)

        # A discriminative model produces a probability, not a distribution. The reported PMF is
        # reconstructed so the challenger fills the same shape as its peers -- and it is derived
        # *from* the probability rather than being the source of it, which is why the mean is
        # labelled as implied.
        adjustments = resolve_adjustments(inputs)
        dispersion = LEAGUE_DISPERSION.get(inputs.prop_type, DEFAULT_DISPERSION)
        length = min(MAX_OUTCOME + 1, max(60, int(inputs.line) + 50))
        implied_mean = _mean_matching_probability(over, inputs, dispersion, length)
        pmf = count_pmf(implied_mean, dispersion, length)
        _, push, _ = line_probabilities(pmf, inputs.line)

        return ChallengerPrediction(
            name=self.name,
            version=self.version,
            pmf=pmf,
            mean=pmf_mean(pmf),
            stddev=pmf_stddev(pmf),
            over=calibrated_over,
            push=push,
            under=max(0.0, min(1.0, 1.0 - calibrated_over - push)),
            diagnostics={
                "raw_over": over,
                "implied_mean": implied_mean,
                "expected_minutes": adjustments.expected_minutes,
                "market_over_probability": features["market_over_probability"],
                "feature_count": len(self._feature_names),
                "discriminative": True,
            },
        )


def _mean_matching_probability(
    over: float, inputs: ScoringInputs, dispersion: float, length: int
) -> float:
    """The count mean whose distribution reproduces this probability at this line.

    Bisection rather than an approximation, for the same reason the market component solves for
    its mean rather than guessing: the reported distribution should actually correspond to the
    number the model produced.
    """
    low, high = 0.05, 80.0
    for _ in range(40):
        middle = (low + high) / 2
        candidate, _, _ = line_probabilities(count_pmf(middle, dispersion, length), inputs.line)
        if candidate < over:
            low = middle
        else:
            high = middle
    return (low + high) / 2
