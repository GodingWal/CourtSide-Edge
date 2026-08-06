"""One scoring function, called by both the live board and the replay harness.

This module exists because of a defect that made every published number about this system
untrustworthy: **the model that was backtested was not the model that ran.**

Production pooled five components -- empirical, hierarchical, player_state,
opportunity_conversion, market_prior -- at one set of weights. The walk-forward harness pooled a
different five -- season_average, last_five, minutes_rate, opportunity_conversion, market_prior
-- at another. They shared exactly one component. The harness also ignored injuries, projected
roles, teammate absences and matchup context entirely, so it could not have measured whether
any of those features helped even in principle. The model card then cited that harness's Brier
score as evidence for the production ensemble, and the readiness gates read the same table.

The fix is structural rather than a matter of copying constants across: scoring is a pure
function of point-in-time inputs, defined here, and both callers construct its inputs from
whatever they can see at their own as-of moment. There is no second implementation to drift.

Two subtler bugs died with the rewrite:

* ``projected_minutes = role_minutes or weighted_mean(history)`` treated a legitimate zero-minute
  projection as missing and substituted the player's historical average -- the one case where
  being wrong matters most.
* The simulator and the parametric components disagreed about expected minutes whenever the role
  model had no row, because each had its own fallback and one of them filtered out zero-minute
  games while the other did not. Minutes are now resolved once, in :func:`resolve_adjustments`,
  and every component reads the same number.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from wnba_services.forecasting.calibration import IDENTITY, CalibrationMap
from wnba_services.forecasting.distributions import (
    DEFAULT_DISPERSION,
    LEAGUE_DISPERSION,
    MAX_OUTCOME,
    correlated_sum_moments,
    count_pmf,
    estimate_correlation,
    estimate_dispersion,
    line_probabilities,
    linear_pool,
    log_pool,
    pmf_mean,
    pmf_median,
    pmf_stddev,
)
from wnba_services.forecasting.line_bias import IDENTITY_BIAS, LineBandBias
from wnba_services.forecasting.minutes import (
    MAX_MINUTES,
    GameObservation,
    build_joint_sampler,
    build_minutes_model,
    rest_adjustment,
)
from wnba_services.forecasting.recency import MARKET_HALF_LIFE, weighted_ratio
from wnba_services.forecasting.weights import EnsembleWeights

__all__ = [
    "COMPONENT_NAMES",
    "MINIMUM_HISTORY_GAMES",
    "MINIMUM_HISTORY_WITH_PRIOR",
    "SCORER_VERSION",
    "STAT_GROUPS",
    "SUPPORTED_MARKETS",
    "Adjustments",
    "ComponentForecast",
    "HistoryGame",
    "MarketInputs",
    "MatchupInputs",
    "PriorSeasonRate",
    "RoleInputs",
    "ScoredForecast",
    "ScoringInputs",
    "TeammateInputs",
    "resolve_adjustments",
    "score_prop",
]

SCORER_VERSION = "0.4.0"

COMPONENT_NAMES: tuple[str, ...] = (
    "empirical",
    "hierarchical",
    "player_state",
    "opportunity_conversion",
    "market_prior",
)

# Each market as a tuple of *stat groups*. A group is summed directly (offensive and defensive
# rebounds are one stat); separate groups are correlated random variables whose sum needs its
# covariance propagated rather than assumed away.
STAT_GROUPS: dict[str, tuple[tuple[str, ...], ...]] = {
    "points": (("points",),),
    "rebounds": (("rebounds_offensive", "rebounds_defensive"),),
    "assists": (("assists",),),
    "three_pointers": (("three_pointers_made",),),
    "points_rebounds_assists": (
        ("points",),
        ("rebounds_offensive", "rebounds_defensive"),
        ("assists",),
    ),
    "points_rebounds": (("points",), ("rebounds_offensive", "rebounds_defensive")),
    "points_assists": (("points",), ("assists",)),
    "rebounds_assists": (("rebounds_offensive", "rebounds_defensive"), ("assists",)),
}
SUPPORTED_MARKETS: dict[str, tuple[str, ...]] = {
    market: tuple(column for group in groups for column in group)
    for market, groups in STAT_GROUPS.items()
}

MINIMUM_HISTORY_GAMES = 10
# A prior season is weaker evidence than the current one, but it is far better than nothing --
# and requiring ten current-season games means skipping most of the board every April.
MINIMUM_HISTORY_WITH_PRIOR = 4

_LEAGUE_PRIOR_MINUTES = 300.0
_PRIOR_SEASON_DISCOUNT = 0.40
_MAX_PRIOR_SEASON_MINUTES = 400.0
_DEFAULT_SIMULATIONS = 10_000
_MIN_RATE_MULTIPLIER = 0.75
_MAX_RATE_MULTIPLIER = 1.35
_MIN_MATCHUP_MULTIPLIER = 0.80
_MAX_MATCHUP_MULTIPLIER = 1.25


# --------------------------------------------------------------------------------------
# Inputs -- everything knowable strictly before tipoff
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class HistoryGame:
    """One completed game, reduced to the columns scoring reads."""

    minutes: float
    started: bool
    stats: Mapping[str, float]

    def total(self, columns: Sequence[str]) -> float:
        return math.fsum(float(self.stats.get(column, 0.0)) for column in columns)

    def has(self, column: str) -> bool:
        return column in self.stats


@dataclass(frozen=True)
class RoleInputs:
    """What the role model expects, conditional on the player appearing."""

    expected_minutes: float | None = None
    minutes_std: float | None = None
    start_probability: float | None = None
    availability_probability: float | None = None
    model_version: str | None = None


@dataclass(frozen=True)
class TeammateInputs:
    """Net effect of absent teammates, already combined across them by the feature engine."""

    rate_multiplier: float = 1.0
    minutes_delta: float = 0.0
    effect_count: int = 0
    confidence: float | None = None
    method_version: str | None = None


@dataclass(frozen=True)
class MatchupInputs:
    pace_multiplier: float = 1.0
    defense_multiplier: float = 1.0
    blowout_probability: float = 0.0
    expected_possessions: float | None = None
    expected_margin: float | None = None
    team_rest_days: float | None = None
    opponent_rest_days: float | None = None
    method_version: str | None = None


@dataclass(frozen=True)
class MarketInputs:
    """What the price says, once the operator's margin is removed.

    The old market component read only the line and modelled it as ``Poisson(line + 0.5)``,
    discarding the multipliers entirely and then weighting the result at 5%. On a pick'em board
    where both sides pay the same, that is defensible -- the price genuinely carries no
    information. On a shaded or boosted line it throws away the single most informative number
    available, because the operator has already aggregated everything we are trying to model.
    """

    over_probability: float | None = None
    under_probability: float | None = None

    @property
    def is_informative(self) -> bool:
        return self.over_probability is not None and self.under_probability is not None


@dataclass(frozen=True)
class PriorSeasonRate:
    """Per-minute production in a previous season, used as an intermediate shrinkage target."""

    rate_per_minute: float
    minutes: float

    @property
    def effective_minutes(self) -> float:
        """Discounted so a full prior season cannot outvote the current one."""
        return min(_MAX_PRIOR_SEASON_MINUTES, max(0.0, self.minutes) * _PRIOR_SEASON_DISCOUNT)


@dataclass(frozen=True)
class ScoringInputs:
    """Everything :func:`score_prop` is allowed to see. Nothing here postdates the forecast."""

    prop_type: str
    line: Decimal
    history: tuple[HistoryGame, ...]
    league_rate_per_minute: float
    seed: int = 0
    injury_designation: str = "available"
    role: RoleInputs | None = None
    teammate: TeammateInputs | None = None
    matchup: MatchupInputs | None = None
    market: MarketInputs | None = None
    prior_season: PriorSeasonRate | None = None
    weights: EnsembleWeights = field(default_factory=EnsembleWeights.prior)
    calibration: CalibrationMap = IDENTITY
    line_bias: LineBandBias = IDENTITY_BIAS
    simulations: int = _DEFAULT_SIMULATIONS
    pool: str = "log"

    @property
    def columns(self) -> tuple[str, ...]:
        return SUPPORTED_MARKETS[self.prop_type]

    @property
    def groups(self) -> tuple[tuple[str, ...], ...]:
        return STAT_GROUPS[self.prop_type]

    @property
    def minimum_history(self) -> int:
        return (
            MINIMUM_HISTORY_WITH_PRIOR if self.prior_season is not None else MINIMUM_HISTORY_GAMES
        )

    @property
    def has_sufficient_history(self) -> bool:
        return len(self.history) >= self.minimum_history


# --------------------------------------------------------------------------------------
# Outputs
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ComponentForecast:
    name: str
    version: str
    weight: float
    pmf: tuple[float, ...]
    mean: float
    over: float
    push: float
    under: float


@dataclass(frozen=True)
class Adjustments:
    """The single resolution of minutes and rate that every component reads."""

    expected_minutes: float
    minutes_std: float
    start_probability: float
    rate_multiplier: float
    matchup_multiplier: float
    rest_minutes_multiplier: float
    rest_rate_multiplier: float

    @property
    def combined_rate_multiplier(self) -> float:
        return self.rate_multiplier * self.matchup_multiplier * self.rest_rate_multiplier


@dataclass(frozen=True)
class ScoredForecast:
    """A complete, auditable forecast for one prop at one moment."""

    components: tuple[ComponentForecast, ...]
    pmf: tuple[float, ...]
    mean: float
    median: int
    stddev: float
    over: float
    push: float
    under: float
    calibrated_over: float
    calibrated_under: float
    disagreement: float
    dispersion: float
    adjustments: Adjustments
    data_quality_score: float
    diagnostics: dict[str, Any]

    @property
    def expected_minutes(self) -> float:
        return self.adjustments.expected_minutes


# --------------------------------------------------------------------------------------
# Adjustment resolution -- minutes and rate, decided once
# --------------------------------------------------------------------------------------
def resolve_adjustments(inputs: ScoringInputs) -> Adjustments:
    """Combine role, teammate, matchup and rest into one minutes/rate decision.

    Every clamp here is a statement that a feature may nudge a forecast but never author it. A
    teammate adjustment that wants to move a rate by 60% has found a sample-size artefact, not a
    basketball fact.
    """
    half_life = MARKET_HALF_LIFE.get(inputs.prop_type, 8.0)
    history_minutes = [game.minutes for game in inputs.history]
    started = [game.started for game in inputs.history]

    role = inputs.role
    start_probability = 0.5
    if role is not None and role.start_probability is not None:
        start_probability = max(0.0, min(1.0, role.start_probability))
    elif started:
        start_probability = math.fsum(1.0 for value in started if value) / len(started)

    # `is not None` rather than truthiness: a zero-minute projection is a projection.
    expected_minutes = (
        role.expected_minutes if role is not None and role.expected_minutes is not None else None
    )
    minutes_std = role.minutes_std if role is not None else None

    teammate = inputs.teammate
    rate_multiplier = 1.0
    if teammate is not None:
        rate_multiplier = max(
            _MIN_RATE_MULTIPLIER, min(_MAX_RATE_MULTIPLIER, teammate.rate_multiplier)
        )
        if expected_minutes is not None:
            expected_minutes = max(0.0, min(MAX_MINUTES, expected_minutes + teammate.minutes_delta))

    matchup = inputs.matchup
    matchup_multiplier = 1.0
    # Rest days are deliberately not read here. Measured against settled outcomes (deep dive,
    # Aug 2026), rest and home/away carried no signal, so the identity adjustment is what the
    # evidence supports. `rest_adjustment` stays in minutes.py as a documented hypothesis,
    # not a scored feature.
    rest = rest_adjustment(None, None)
    if matchup is not None:
        matchup_multiplier = max(
            _MIN_MATCHUP_MULTIPLIER,
            min(
                _MAX_MATCHUP_MULTIPLIER,
                matchup.pace_multiplier * matchup.defense_multiplier,
            ),
        )
        # The blowout minutes haircut is likewise gated to zero: blowout_probability is
        # quantized and confounded with pace (r = -0.80), so the haircut mostly re-applied the
        # pace multiplier under another name. Tracked in issue #68; re-enable only behind a
        # refit that separates the two.

    model = build_minutes_model(
        history_minutes,
        started,
        start_probability=start_probability,
        half_life=half_life,
        expected_minutes=expected_minutes,
        minutes_std=minutes_std,
    )
    final_minutes = max(0.0, min(MAX_MINUTES, model.expected_minutes * rest.minutes_multiplier))
    return Adjustments(
        expected_minutes=final_minutes,
        minutes_std=model.stddev,
        start_probability=model.start_probability,
        rate_multiplier=rate_multiplier,
        matchup_multiplier=matchup_multiplier,
        rest_minutes_multiplier=rest.minutes_multiplier,
        rest_rate_multiplier=rest.rate_multiplier,
    )


# --------------------------------------------------------------------------------------
# Dispersion, including the correlated-combo case
# --------------------------------------------------------------------------------------
def resolve_dispersion(inputs: ScoringInputs) -> float:
    """Variance-to-mean ratio for this market, propagating covariance across stat groups.

    For a single-stat market this is the shrunk empirical ratio. For a combination it is the
    ratio implied by summing correlated components -- which is strictly larger than any of them,
    and very much larger than the ``Poisson(summed rate)`` the pipeline used to assume.
    """
    prior = LEAGUE_DISPERSION.get(inputs.prop_type, DEFAULT_DISPERSION)
    played = [game for game in inputs.history if game.minutes > 0.0]
    if len(played) < 3:
        return prior

    groups = inputs.groups
    if len(groups) == 1:
        return estimate_dispersion([game.total(groups[0]) for game in played], prior=prior)

    series = [[game.total(group) for game in played] for group in groups]
    means = [math.fsum(values) / len(values) for values in series]
    variances = [
        math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
        for values, mean in zip(series, means, strict=True)
    ]
    correlations = [
        [1.0 if i == j else estimate_correlation(series[i], series[j]) for j in range(len(series))]
        for i in range(len(series))
    ]
    total_mean, total_variance = correlated_sum_moments(means, variances, correlations)
    if total_mean <= 0.0:
        return prior
    observed = max(1.0, total_variance / total_mean)
    weight = len(played) / (len(played) + 15.0)
    return max(1.0, min(6.0, weight * observed + (1.0 - weight) * prior))


# --------------------------------------------------------------------------------------
# Component expectations
# --------------------------------------------------------------------------------------
def _hierarchical_rate(inputs: ScoringInputs) -> float:
    """Player rate shrunk through the prior season toward the league.

    Two levels rather than one. A player with six games this season and a full prior season is
    not a blank slate, and shrinking them straight to the league mean throws away the most
    relevant evidence that exists about them.
    """
    columns = inputs.columns
    minutes = math.fsum(game.minutes for game in inputs.history)
    total_stat = math.fsum(game.total(columns) for game in inputs.history)

    numerator = total_stat + inputs.league_rate_per_minute * _LEAGUE_PRIOR_MINUTES
    denominator = minutes + _LEAGUE_PRIOR_MINUTES
    prior_season = inputs.prior_season
    if prior_season is not None:
        effective = prior_season.effective_minutes
        numerator += prior_season.rate_per_minute * effective
        denominator += effective
    return numerator / max(1.0, denominator)


def _player_state_rate(inputs: ScoringInputs) -> float:
    """Recency-weighted per-minute rate at this market's fitted half-life."""
    half_life = MARKET_HALF_LIFE.get(inputs.prop_type, 8.0)
    played = [game for game in inputs.history if game.minutes > 0.0]
    if not played:
        return 0.0
    return weighted_ratio(
        [game.total(inputs.columns) for game in played],
        [game.minutes for game in played],
        half_life,
    )


def opportunity_conversion_expectation(inputs: ScoringInputs, expected_minutes: float) -> float:
    """Decompose volume from conversion when the box score supports it.

    Attempts per minute is a rotation and role fact that moves slowly; conversion is a shooting
    fact that moves fast and regresses hard. Estimating them at the same decay rate blurs the two
    together, so they get different half-lives -- the one place in the pipeline where that is a
    modelling decision rather than an accident.
    """
    columns = inputs.columns
    played = [game for game in inputs.history if game.minutes > 0.0]
    if not played:
        return 0.0
    minutes = [game.minutes for game in played]
    half_life = MARKET_HALF_LIFE.get(inputs.prop_type, 8.0)

    if columns == ("points",) and all(game.has("field_goals_attempted") for game in played):
        attempts = [game.total(("field_goals_attempted",)) for game in played]
        attempts_per_minute = weighted_ratio(attempts, minutes, half_life)
        points_per_attempt = weighted_ratio(
            [game.total(("points",)) for game in played], attempts, half_life * 2.0
        )
        return expected_minutes * attempts_per_minute * points_per_attempt
    if columns == ("three_pointers_made",) and all(
        game.has("three_pointers_attempted") for game in played
    ):
        attempts = [game.total(("three_pointers_attempted",)) for game in played]
        attempts_per_minute = weighted_ratio(attempts, minutes, half_life)
        conversion = weighted_ratio(
            [game.total(("three_pointers_made",)) for game in played], attempts, half_life * 2.0
        )
        return expected_minutes * attempts_per_minute * conversion
    return expected_minutes * weighted_ratio(
        [game.total(columns) for game in played], minutes, half_life
    )


def _market_mean(inputs: ScoringInputs, dispersion: float, length: int) -> float:
    """The mean whose distribution reproduces the devigged market probability at this line.

    Solved by bisection rather than approximated, because the whole point of reading the price
    is to reproduce what it says. Falls back to a line-centred prior when both sides pay the
    same, which on a flat pick'em board is most of the time and is not a defect: an uninformative
    price should contribute an uninformative prior.
    """
    market = inputs.market
    if market is None or not market.is_informative:
        return max(0.1, float(inputs.line) + 0.5)
    target = market.over_probability
    if target is None:
        return max(0.1, float(inputs.line) + 0.5)
    target = min(0.999, max(0.001, target))

    low, high = 0.05, max(1.0, float(inputs.line) * 4.0 + 10.0)
    for _ in range(60):
        mid = (low + high) / 2.0
        over, _, _ = line_probabilities(count_pmf(mid, dispersion, length), inputs.line)
        if over < target:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _draw_count(rng: random.Random, mean: float, dispersion: float) -> int:
    """One negative-binomial draw, as a Gamma-mixed Poisson.

    Sampling the mixture rather than the count directly is what makes the simulator agree with
    the parametric components: both describe the same distribution, one analytically and one by
    draws, so a disagreement between them is a bug rather than a modelling choice.
    """
    mean = max(1e-6, min(150.0, mean))
    dispersion = max(1.0, min(6.0, dispersion))
    if dispersion - 1.0 > 1e-6:
        shape = mean / (dispersion - 1.0)
        mean = rng.gammavariate(shape, dispersion - 1.0)
    limit = math.exp(-mean) if mean < 700.0 else 0.0
    count, product = 0, 1.0
    while product > limit and count < MAX_OUTCOME:
        count += 1
        product *= rng.random()
    return max(0, count - 1)


def _empirical_pmf(
    inputs: ScoringInputs, adjustments: Adjustments, dispersion: float, length: int
) -> tuple[float, ...] | None:
    """Simulated PMF from coupled ``(minutes, rate)`` draws, or ``None`` without rate history."""
    half_life = MARKET_HALF_LIFE.get(inputs.prop_type, 8.0)
    observations = [
        GameObservation(minutes=game.minutes, stat=game.total(inputs.columns))
        for game in inputs.history
    ]
    sampler = build_joint_sampler(observations, half_life=half_life)
    if sampler is None:
        return None

    started = [game.started for game in inputs.history]
    model = build_minutes_model(
        [game.minutes for game in inputs.history],
        started,
        start_probability=adjustments.start_probability,
        half_life=half_life,
        expected_minutes=adjustments.expected_minutes,
        minutes_std=adjustments.minutes_std,
    )
    rng = random.Random(inputs.seed)
    counts = [0.0] * length
    multiplier = adjustments.combined_rate_multiplier
    for _ in range(max(1, inputs.simulations)):
        minutes, rate = sampler.sample(rng, model, multiplier)
        counts[min(length - 1, _draw_count(rng, minutes * rate, dispersion))] += 1.0
    total = math.fsum(counts)
    if total <= 0.0:
        return None
    return tuple(count / total for count in counts)


# --------------------------------------------------------------------------------------
# The scorer
# --------------------------------------------------------------------------------------
def _component(
    name: str, pmf: tuple[float, ...], line: Decimal, weight: float
) -> ComponentForecast:
    over, push, under = line_probabilities(pmf, line)
    return ComponentForecast(
        name=name,
        version=SCORER_VERSION,
        weight=weight,
        pmf=pmf,
        mean=pmf_mean(pmf),
        over=over,
        push=push,
        under=under,
    )


def _data_quality(inputs: ScoringInputs, adjustments: Adjustments) -> float:
    """Evidence completeness in ``[0, 1]``: how much of what we wanted did we actually have?

    Replaces ``0.7 + len(history)/100``, which with a twenty-game window could never exceed 0.9
    and so turned the pipeline's ``quality >= 0.85`` gate into "at least fifteen games" wearing a
    different name. This version is a real checklist, and it can reach 1.0.
    """
    history_score = min(1.0, len(inputs.history) / 20.0)
    feature_score = math.fsum(
        [
            0.30 if inputs.role is not None else 0.0,
            0.25 if inputs.matchup is not None else 0.0,
            0.15 if inputs.teammate is not None else 0.0,
            0.10 if inputs.prior_season is not None else 0.0,
            0.20 if adjustments.expected_minutes > 0.0 else 0.0,
        ]
    )
    return max(0.0, min(1.0, 0.5 * history_score + 0.5 * feature_score))


def score_prop(inputs: ScoringInputs) -> ScoredForecast:
    """Score one prop from point-in-time evidence. Pure: same inputs, same output, always."""
    if inputs.prop_type not in SUPPORTED_MARKETS:
        raise ValueError(f"unsupported market {inputs.prop_type!r}")
    if not inputs.has_sufficient_history:
        raise ValueError(
            f"{inputs.prop_type} needs {inputs.minimum_history} games of history, "
            f"got {len(inputs.history)}"
        )

    adjustments = resolve_adjustments(inputs)
    dispersion = resolve_dispersion(inputs)
    length = min(MAX_OUTCOME + 1, max(60, int(inputs.line) + 50))
    multiplier = adjustments.combined_rate_multiplier
    minutes = adjustments.expected_minutes

    hierarchical_mean = minutes * _hierarchical_rate(inputs) * multiplier
    player_state_mean = minutes * _player_state_rate(inputs) * multiplier
    opportunity_mean = opportunity_conversion_expectation(inputs, minutes) * multiplier
    market_mean = _market_mean(inputs, dispersion, length)

    # Measured band bias, applied to the model side only: the shrinkage that protects thin
    # histories systematically pulls high-line (star) projections toward the pack, and settled
    # outcomes show it. The market component is untouched -- the operator already knows the
    # line. The empirical PMF is built from realised counts rather than a mean and carries the
    # same histories the bias was measured against, so it is left alone as well.
    bias = inputs.line_bias.bias_for(float(inputs.line))
    if bias:
        hierarchical_mean = max(0.0, hierarchical_mean + bias)
        player_state_mean = max(0.0, player_state_mean + bias)
        opportunity_mean = max(0.0, opportunity_mean + bias)

    empirical = _empirical_pmf(inputs, adjustments, dispersion, length)
    if empirical is None:
        # No usable rate history: fall back to the hierarchical shape rather than inventing one.
        empirical = count_pmf(hierarchical_mean, dispersion, length)

    pmfs: dict[str, tuple[float, ...]] = {
        "empirical": empirical,
        "hierarchical": count_pmf(hierarchical_mean, dispersion, length),
        "player_state": count_pmf(player_state_mean, dispersion, length),
        "opportunity_conversion": count_pmf(opportunity_mean, dispersion, length),
        "market_prior": count_pmf(market_mean, dispersion, length),
    }
    names = list(COMPONENT_NAMES)
    resolved_weights = inputs.weights.for_components(names)
    components = tuple(
        _component(name, pmfs[name], inputs.line, weight)
        for name, weight in zip(names, resolved_weights, strict=True)
    )

    pool = log_pool if inputs.pool == "log" else linear_pool
    blended = pool([pmfs[name] for name in names], list(resolved_weights), length)
    over, push, under = line_probabilities(blended, inputs.line)

    # Disagreement across components, weighted by the trust each is actually given: a component
    # at 2% weight wandering off is not evidence that the ensemble is confused.
    mean_over = math.fsum(
        weight * component.over
        for component, weight in zip(components, resolved_weights, strict=True)
    )
    disagreement = math.sqrt(
        math.fsum(
            weight * (component.over - mean_over) ** 2
            for component, weight in zip(components, resolved_weights, strict=True)
        )
    )

    calibrated_over = inputs.calibration.apply(over)
    calibrated_under = max(0.0, min(1.0, 1.0 - calibrated_over - push))

    return ScoredForecast(
        components=components,
        pmf=blended,
        mean=pmf_mean(blended),
        median=pmf_median(blended),
        stddev=pmf_stddev(blended),
        over=over,
        push=push,
        under=under,
        calibrated_over=calibrated_over,
        calibrated_under=calibrated_under,
        disagreement=disagreement,
        dispersion=dispersion,
        adjustments=adjustments,
        data_quality_score=_data_quality(inputs, adjustments),
        diagnostics={
            "scorer_version": SCORER_VERSION,
            "pool": inputs.pool,
            "dispersion": dispersion,
            "history_games": len(inputs.history),
            "expected_minutes": adjustments.expected_minutes,
            "minutes_std": adjustments.minutes_std,
            "start_probability": adjustments.start_probability,
            "rate_multiplier": adjustments.rate_multiplier,
            "matchup_multiplier": adjustments.matchup_multiplier,
            "rest_minutes_multiplier": adjustments.rest_minutes_multiplier,
            "rest_rate_multiplier": adjustments.rest_rate_multiplier,
            "component_means": {
                "hierarchical": hierarchical_mean,
                "player_state": player_state_mean,
                "opportunity_conversion": opportunity_mean,
                "market_prior": market_mean,
            },
            "weights": dict(zip(names, resolved_weights, strict=True)),
            "weights_fitted": inputs.weights.is_fitted,
            "calibration_shrinkage": inputs.calibration.shrinkage,
            "calibration_sample": inputs.calibration.sample_size,
            "market_informative": inputs.market is not None and inputs.market.is_informative,
            "prior_season_minutes": (
                None if inputs.prior_season is None else inputs.prior_season.effective_minutes
            ),
        },
    )
