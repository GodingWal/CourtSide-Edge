"""Model families that are not the champion, scored from exactly the champion's inputs.

The roadmap has said for months that champion/challenger experiments were unimplemented "and
deliberately so: an experiment row requires two model versions to compare and only one exists."
That was the right call while it was true. This module is what makes it stop being true.

The bar for something in here is that it is a *different model*, not a different constant. Two
families clear it:

``hierarchical-bayes``
    A conjugate Gamma-Poisson posterior over the player's per-minute rate, with the likelihood's
    effective sample size discounted by the observed over-dispersion, and with the posterior
    *variance* carried into the predictive distribution. The champion's ``hierarchical`` component
    is a point estimate shrunk at a fixed prior strength of 300 league-minutes; it cannot tell a
    rate of 0.55 measured over 40 minutes from the same 0.55 measured over 700, and it reports
    both with the same width. This one can, which is the whole reason low-sample players and
    rookies are on the list of things the platform handles badly.

``state-space-role``
    A local-level (Kalman) filter on minutes and on per-minute rate, with the signal-to-noise
    ratio estimated per player from the autocovariance of first differences rather than fixed.
    The champion's ``player_state`` component is exponential weighting at a per-market half-life,
    which decays the past at the same speed whether or not anything actually changed. A filter
    whose gain adapts to the observed innovation moves quickly through a genuine role change and
    stays still through noise. Rotation change is the error category the learning loop attributes
    most of its misses to, so a model built to track it is the challenger worth running first.

What is *not* here, on purpose: a gradient-boosted challenger. CatBoost and LightGBM are on the
plan, and a tree model over the feature snapshots is a reasonable third family -- but it needs a
dependency, a training set assembled from the feature store, and a fitting job with its own
leakage controls. Stubbing one that wraps the same five components would populate the experiments
table without creating anything to learn from, which is the failure mode the roadmap already
named. It stays unbuilt until it can be built properly.

Every challenger is a pure function of :class:`ScoringInputs`, the same frozen point-in-time
bundle :func:`score_prop` reads. That is deliberate: a challenger that needed inputs the champion
could not see would be measuring the inputs rather than the model, and no comparison between them
would mean anything.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from wnba_services.forecasting.distributions import (
    MAX_OUTCOME,
    count_pmf,
    line_probabilities,
    pmf_mean,
    pmf_stddev,
)
from wnba_services.forecasting.minutes import MAX_MINUTES
from wnba_services.forecasting.scoring import (
    ScoredForecast,
    ScoringInputs,
    resolve_adjustments,
    resolve_dispersion,
)

__all__ = [
    "CHALLENGERS",
    "ChallengerPrediction",
    "HierarchicalBayesChallenger",
    "StateSpaceRoleChallenger",
    "challenger_by_name",
    "challenger_names",
]

# Prior strength for the Gamma-Poisson rate prior, in league-equivalent minutes. The champion
# uses 300 for the same purpose; keeping the number identical is what makes the comparison a test
# of the posterior treatment rather than a test of who guessed a better constant.
_PRIOR_MINUTES = 300.0
_MIN_HISTORY_FOR_FILTER = 4
# The filter is allowed to trust one game between 2% and 60%. The floor stops a mis-estimated
# signal-to-noise ratio from freezing the level; the ceiling stops one loud game becoming the
# whole forecast, which is the failure mode of any adaptive filter on a 40-game season.
_MIN_GAIN = 0.02
_MAX_GAIN = 0.60
# Minutes uncertainty is integrated over these standard-deviation offsets. Five nodes rather
# than proper Gauss-Hermite quadrature: the minutes distribution is not Gaussian in the tails
# anyway, and five evaluations of a count PMF is a cost that can run on the live board.
_MINUTES_OFFSETS = (-1.6, -0.8, 0.0, 0.8, 1.6)
_MAX_DISPERSION = 8.0


@dataclass(frozen=True)
class ChallengerPrediction:
    """One challenger's answer for one prop, in the shape the champion reports."""

    name: str
    version: str
    pmf: tuple[float, ...]
    mean: float
    stddev: float
    over: float
    push: float
    under: float
    diagnostics: dict[str, Any]

    def probability_for(self, side: str) -> float:
        return self.over if side == "over" else self.under


class Challenger(Protocol):
    """What the experiment runner needs from a model family."""

    name: str
    version: str

    def specification(self) -> dict[str, Any]:
        """The stored description of this model, for ``wnba.model_versions.specification``."""
        ...

    def predict(
        self, inputs: ScoringInputs, champion: ScoredForecast | None = None
    ) -> ChallengerPrediction:
        """Score one prop. Pure: same inputs, same output, always.

        ``champion`` is the forecast the production ensemble just produced for the same
        prop, supplied so stacked families can learn from it. Families that are not stacks
        ignore it; the runner always passes it.
        """
        ...


# --------------------------------------------------------------------------------------
# Shared machinery
# --------------------------------------------------------------------------------------
def _pmf_length(inputs: ScoringInputs) -> int:
    return min(MAX_OUTCOME + 1, max(60, int(inputs.line) + 50))


def _minutes_nodes(mean: float, stddev: float) -> tuple[tuple[float, float], ...]:
    """A small symmetric quadrature over the minutes distribution.

    The champion integrates minutes uncertainty by Monte Carlo inside one component and ignores
    it in the other four. Both challengers integrate it everywhere, deterministically, so a
    difference between them and the champion is never a difference in random draws.
    """
    spread = max(0.0, stddev)
    if spread <= 0.25 or mean <= 0.0:
        return ((max(0.0, mean), 1.0),)
    offsets = _MINUTES_OFFSETS
    raw = tuple(math.exp(-0.5 * offset * offset) for offset in offsets)
    total = math.fsum(raw)
    return tuple(
        (max(0.0, min(MAX_MINUTES, mean + offset * spread)), weight / total)
        for offset, weight in zip(offsets, raw, strict=True)
    )


def _mix_over_minutes(
    nodes: Sequence[tuple[float, float]],
    rate: float,
    dispersion: float,
    length: int,
) -> tuple[float, ...]:
    """Mixture of count distributions, one per minutes node, weighted by the node's mass."""
    mixed = [0.0] * length
    for minutes, weight in nodes:
        component = count_pmf(max(0.0, minutes * rate), dispersion, length)
        for index, probability in enumerate(component):
            mixed[index] += weight * probability
    total = math.fsum(mixed)
    if total <= 0.0:
        return count_pmf(0.0, dispersion, length)
    return tuple(value / total for value in mixed)


def _observed_dispersion(inputs: ScoringInputs, expected_rate: float) -> float:
    """How much noisier this player's game counts are than a Poisson of the same mean.

    Used to discount the likelihood rather than to widen the forecast: a player whose per-game
    totals scatter at three times Poisson has given us, in information terms, a third of the
    minutes we counted. Treating them as full-strength evidence is exactly how a hot fortnight
    becomes a confident forecast.
    """
    played = [game for game in inputs.history if game.minutes > 0.0]
    if len(played) < 3 or expected_rate <= 0.0:
        return 1.0
    ratios = []
    for game in played:
        expected = game.minutes * expected_rate
        if expected <= 0.0:
            continue
        actual = game.total(inputs.columns)
        ratios.append((actual - expected) ** 2 / expected)
    if not ratios:
        return 1.0
    return max(1.0, min(_MAX_DISPERSION, math.fsum(ratios) / len(ratios)))


def _finish(
    name: str,
    version: str,
    inputs: ScoringInputs,
    pmf: tuple[float, ...],
    diagnostics: dict[str, Any],
) -> ChallengerPrediction:
    over, push, _ = line_probabilities(pmf, inputs.line)
    calibrated_over = inputs.calibration.apply(over)
    return ChallengerPrediction(
        name=name,
        version=version,
        pmf=pmf,
        mean=pmf_mean(pmf),
        stddev=pmf_stddev(pmf),
        over=calibrated_over,
        push=push,
        under=max(0.0, min(1.0, 1.0 - calibrated_over - push)),
        diagnostics={**diagnostics, "raw_over": over},
    )


# --------------------------------------------------------------------------------------
# Challenger 1: conjugate hierarchical Bayes over the per-minute rate
# --------------------------------------------------------------------------------------
class HierarchicalBayesChallenger:
    """Three-level Gamma-Poisson pooling: league -> prior season -> current season.

    The posterior over the rate is ``Gamma(a, b)`` with ``a = a0 + sum(stat)/phi`` and
    ``b = b0 + sum(minutes)/phi``, where ``phi`` is the player's observed over-dispersion. Its
    mean is what a shrinkage estimator would report; its variance is what the champion throws
    away. Carrying that variance forward widens the predictive distribution by
    ``minutes^2 * Var[lambda]``, which is small for a 700-minute veteran and large for a rookie
    with two starts -- and it is the rookie the platform currently over-states.
    """

    name = "hierarchical-bayes"
    version = "0.1.0"

    def specification(self) -> dict[str, Any]:
        return {
            "family": "conjugate_gamma_poisson",
            "pooling": ["league", "prior_season", "player"],
            "prior_minutes": _PRIOR_MINUTES,
            "likelihood_discounted_by_overdispersion": True,
            "propagates_parameter_uncertainty": True,
            "integrates_minutes_uncertainty": True,
            "analysis_only": True,
        }

    def predict(
        self, inputs: ScoringInputs, champion: ScoredForecast | None = None
    ) -> ChallengerPrediction:
        adjustments = resolve_adjustments(inputs)
        length = _pmf_length(inputs)
        multiplier = adjustments.combined_rate_multiplier

        league_rate = max(1e-6, inputs.league_rate_per_minute)
        prior_rate = league_rate
        prior_minutes = _PRIOR_MINUTES
        prior_season = inputs.prior_season
        if prior_season is not None:
            # The prior season is itself pooled toward the league before it becomes this
            # season's prior, so a 300-minute prior season does not arrive as certainty.
            effective = prior_season.effective_minutes
            prior_rate = (
                prior_season.rate_per_minute * effective + league_rate * _PRIOR_MINUTES
            ) / max(1.0, effective + _PRIOR_MINUTES)

        minutes_played = math.fsum(game.minutes for game in inputs.history)
        stat_total = math.fsum(game.total(inputs.columns) for game in inputs.history)
        rough_rate = stat_total / minutes_played if minutes_played > 0.0 else prior_rate
        phi = _observed_dispersion(inputs, rough_rate)

        alpha = prior_rate * prior_minutes + stat_total / phi
        beta = prior_minutes + minutes_played / phi
        posterior_mean = alpha / max(1e-6, beta)
        posterior_variance = alpha / max(1e-6, beta * beta)

        rate = posterior_mean * multiplier
        minutes = adjustments.expected_minutes
        nodes = _minutes_nodes(minutes, adjustments.minutes_std)

        # Predictive variance = sampling noise + parameter uncertainty, expressed back as the
        # variance-to-mean ratio the shared count distribution takes.
        sampling_dispersion = resolve_dispersion(inputs)
        expected_count = max(1e-6, minutes * rate)
        parameter_variance = (minutes * multiplier) ** 2 * posterior_variance
        dispersion = min(
            _MAX_DISPERSION,
            max(1.0, sampling_dispersion + parameter_variance / expected_count),
        )
        pmf = _mix_over_minutes(nodes, rate, dispersion, length)

        return _finish(
            self.name,
            self.version,
            inputs,
            pmf,
            {
                "posterior_rate": posterior_mean,
                "posterior_rate_sd": math.sqrt(posterior_variance),
                "overdispersion_phi": phi,
                "effective_minutes": minutes_played / phi,
                "prior_rate": prior_rate,
                "sampling_dispersion": sampling_dispersion,
                "predictive_dispersion": dispersion,
                "expected_minutes": minutes,
            },
        )


# --------------------------------------------------------------------------------------
# Challenger 2: local-level state space over minutes and rate
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class _FilterResult:
    level: float
    forecast_variance: float
    gain: float
    innovation: float


def _estimate_signal_noise(series: Sequence[float]) -> tuple[float, float]:
    """``(process_variance, observation_variance)`` from the autocovariance of first differences.

    For a local-level model, ``Var(dy) = sigma_w^2 + 2 sigma_v^2`` and
    ``Cov(dy_t, dy_{t-1}) = -sigma_v^2``. Both moments are available from a 20-game season, which
    is the reason this identification is used rather than maximum likelihood: it needs no
    optimiser, it is deterministic, and it degrades to "all noise" rather than to a bad optimum.
    """
    if len(series) < _MIN_HISTORY_FOR_FILTER:
        return 0.0, 1.0
    diffs = [series[i] - series[i - 1] for i in range(1, len(series))]
    if len(diffs) < 2:
        return 0.0, 1.0
    mean_diff = math.fsum(diffs) / len(diffs)
    variance = math.fsum((value - mean_diff) ** 2 for value in diffs) / len(diffs)
    lag_one = math.fsum(
        (diffs[i] - mean_diff) * (diffs[i - 1] - mean_diff) for i in range(1, len(diffs))
    ) / (len(diffs) - 1)
    observation_variance = max(1e-9, -lag_one)
    process_variance = max(0.0, variance - 2.0 * observation_variance)
    return process_variance, observation_variance


def _local_level_filter(
    series: Sequence[float], weights: Sequence[float] | None = None
) -> _FilterResult | None:
    """Run the filter and return the one-step-ahead forecast for the next observation.

    ``weights`` scale the observation variance per point: a nine-minute game says much less about
    a player's per-minute rate than a thirty-four-minute one, and a filter that treats them alike
    will chase garbage-time noise.
    """
    if len(series) < _MIN_HISTORY_FOR_FILTER:
        return None
    process_variance, observation_variance = _estimate_signal_noise(series)
    level = series[0]
    variance = observation_variance + process_variance
    gain = 0.0
    innovation = 0.0
    for index in range(1, len(series)):
        variance += process_variance
        scale = 1.0
        if weights is not None and weights[index] > 0.0:
            mean_weight = math.fsum(weights) / len(weights)
            scale = max(0.25, min(4.0, mean_weight / weights[index]))
        point_variance = observation_variance * scale
        gain = max(_MIN_GAIN, min(_MAX_GAIN, variance / max(1e-9, variance + point_variance)))
        innovation = series[index] - level
        level += gain * innovation
        variance *= 1.0 - gain
    return _FilterResult(
        level=level,
        forecast_variance=variance + process_variance,
        gain=gain,
        innovation=innovation,
    )


class StateSpaceRoleChallenger:
    """Adaptive-gain tracking of a role that is allowed to change.

    Two filters run: one on game minutes, one on per-minute production with the observation
    variance scaled by minutes played. Where the champion's exponential weighting applies the
    same half-life to a player whose role is stable and a player who moved into the starting five
    three games ago, the Kalman gain here is derived from how much of the observed variation is
    persistent -- so it is near the floor for the first player and near the ceiling for the
    second, without anybody choosing which is which.

    A projected-minutes row still wins when one exists. The filter's minutes estimate is a
    fallback and a diagnostic, not a competitor to a rotation projection built from injuries and
    confirmed lineups.
    """

    name = "state-space-role"
    version = "0.1.0"

    def specification(self) -> dict[str, Any]:
        return {
            "family": "local_level_state_space",
            "states": ["minutes", "rate_per_minute"],
            "identification": "autocovariance_of_first_differences",
            "gain_bounds": [_MIN_GAIN, _MAX_GAIN],
            "minutes_weighted_observation_variance": True,
            "analysis_only": True,
            "fallback": "champion_adjustments_when_history_is_short",
        }

    def predict(
        self, inputs: ScoringInputs, champion: ScoredForecast | None = None
    ) -> ChallengerPrediction:
        adjustments = resolve_adjustments(inputs)
        length = _pmf_length(inputs)
        multiplier = adjustments.combined_rate_multiplier
        sampling_dispersion = resolve_dispersion(inputs)

        played = [game for game in inputs.history if game.minutes > 0.0]
        rates = [game.total(inputs.columns) / game.minutes for game in played]
        minutes_series = [game.minutes for game in played]
        rate_filter = _local_level_filter(rates, minutes_series)
        minutes_filter = _local_level_filter([game.minutes for game in inputs.history])

        if rate_filter is None:
            # Too little history to identify a state. Report the champion's resolved rate rather
            # than an unidentified filter: a challenger that invents numbers where it has none is
            # not a challenger, it is noise wearing a model version.
            fallback_rate = (
                math.fsum(game.total(inputs.columns) for game in played) / math.fsum(minutes_series)
                if played and math.fsum(minutes_series) > 0.0
                else inputs.league_rate_per_minute
            )
            rate_level = fallback_rate
            rate_variance = 0.0
            gain = 0.0
        else:
            rate_level = max(0.0, rate_filter.level)
            rate_variance = max(0.0, rate_filter.forecast_variance)
            gain = rate_filter.gain

        role = inputs.role
        has_projection = role is not None and role.expected_minutes is not None
        if has_projection or minutes_filter is None:
            minutes = adjustments.expected_minutes
            minutes_std = adjustments.minutes_std
        else:
            rest = adjustments.rest_minutes_multiplier
            minutes = max(0.0, min(MAX_MINUTES, minutes_filter.level * rest))
            minutes_std = max(
                adjustments.minutes_std * 0.5, math.sqrt(minutes_filter.forecast_variance)
            )

        rate = rate_level * multiplier
        nodes = _minutes_nodes(minutes, minutes_std)
        expected_count = max(1e-6, minutes * rate)
        # State uncertainty in the rate is a forecast-variance contribution exactly as the
        # posterior variance is for the Bayesian challenger.
        state_variance = (minutes * multiplier) ** 2 * rate_variance
        dispersion = min(
            _MAX_DISPERSION,
            max(1.0, sampling_dispersion + state_variance / expected_count),
        )
        pmf = _mix_over_minutes(nodes, rate, dispersion, length)

        return _finish(
            self.name,
            self.version,
            inputs,
            pmf,
            {
                "filtered_rate": rate_level,
                "rate_forecast_sd": math.sqrt(rate_variance),
                "kalman_gain": gain,
                "filtered_minutes": None if minutes_filter is None else minutes_filter.level,
                "used_role_projection": has_projection,
                "expected_minutes": minutes,
                "minutes_std": minutes_std,
                "sampling_dispersion": sampling_dispersion,
                "predictive_dispersion": dispersion,
                "history_games": len(inputs.history),
            },
        )


def _build_challengers() -> Mapping[str, Challenger]:
    # Imported here rather than at module top: the boosted family reads this module's
    # ChallengerPrediction, so a top-level import in both directions would be a cycle.
    from wnba_services.forecasting.boosting import GradientBoostingChallenger

    return {
        challenger.name: challenger
        for challenger in (
            HierarchicalBayesChallenger(),
            StateSpaceRoleChallenger(),
            GradientBoostingChallenger(),
        )
    }


CHALLENGERS: Mapping[str, Challenger] = _build_challengers()


def challenger_names() -> tuple[str, ...]:
    return tuple(sorted(CHALLENGERS))


def challenger_by_name(name: str) -> Challenger:
    try:
        return CHALLENGERS[name]
    except KeyError:
        raise ValueError(
            f"unknown challenger {name!r}; known: {', '.join(challenger_names())}"
        ) from None
