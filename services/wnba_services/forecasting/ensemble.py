"""Auditable distribution ensemble for countable player props."""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from statistics import pstdev
from typing import Any

MAX_OUTCOME = 200
COMPONENT_WEIGHTS = {
    "empirical": 0.45,
    "hierarchical": 0.30,
    "player_state": 0.20,
    "market_prior": 0.05,
}


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
class EnsembleForecast:
    components: tuple[ComponentForecast, ...]
    pmf: tuple[float, ...]
    mean: float
    median: int
    stddev: float
    over: float
    push: float
    under: float
    disagreement: float


def _poisson_pmf(expectation: float, length: int) -> tuple[float, ...]:
    expectation = max(0.01, min(150.0, expectation))
    values = [math.exp(-expectation)]
    for outcome in range(1, length):
        values.append(values[-1] * expectation / outcome)
    total = math.fsum(values)
    return tuple(value / total for value in values)


def _empirical_pmf(outcomes: list[int], length: int) -> tuple[float, ...]:
    counts = [0.0] * length
    for outcome in outcomes:
        counts[min(length - 1, max(0, outcome))] += 1.0
    total = math.fsum(counts)
    return tuple(count / total for count in counts)


def _line_probabilities(pmf: tuple[float, ...], line: Decimal) -> tuple[float, float, float]:
    over = math.fsum(probability for value, probability in enumerate(pmf) if Decimal(value) > line)
    push = pmf[int(line)] if line == line.to_integral_value() and int(line) < len(pmf) else 0.0
    return over, push, max(0.0, 1.0 - over - push)


def _mean(pmf: tuple[float, ...]) -> float:
    return math.fsum(value * probability for value, probability in enumerate(pmf))


def _component(name: str, pmf: tuple[float, ...], line: Decimal) -> ComponentForecast:
    over, push, under = _line_probabilities(pmf, line)
    return ComponentForecast(
        name=name,
        version="0.1.0",
        weight=COMPONENT_WEIGHTS[name],
        pmf=pmf,
        mean=_mean(pmf),
        over=over,
        push=push,
        under=under,
    )


def build_ensemble(
    *,
    outcomes: list[int],
    history: list[dict[str, Any]],
    columns: tuple[str, ...],
    expected_minutes: float,
    rate_multiplier: float,
    league_rate_per_minute: float,
    line: Decimal,
) -> EnsembleForecast:
    """Blend models at the PMF level so pushes and tails remain coherent."""
    length = min(
        MAX_OUTCOME + 1,
        max(60, max(outcomes, default=0) + 30, int(line) + 50),
    )
    empirical = _empirical_pmf(outcomes, length)
    minutes = sum(float(str(row["minutes"])) for row in history)
    total_stat = sum(sum(float(str(row[column])) for column in columns) for row in history)
    player_rate = total_stat / max(1.0, minutes)
    prior_minutes = 300.0
    shrunk_rate = (player_rate * minutes + league_rate_per_minute * prior_minutes) / (
        minutes + prior_minutes
    )
    hierarchical = _poisson_pmf(expected_minutes * shrunk_rate * rate_multiplier, length)

    state_numerator = state_denominator = 0.0
    alpha = 0.25
    for age, row in enumerate(reversed(history)):
        weight = (1.0 - alpha) ** age
        row_minutes = float(str(row["minutes"]))
        if row_minutes <= 0:
            continue
        state_numerator += weight * sum(float(str(row[column])) for column in columns)
        state_denominator += weight * row_minutes
    state_rate = state_numerator / max(1.0, state_denominator)
    player_state = _poisson_pmf(expected_minutes * state_rate * rate_multiplier, length)
    market_prior = _poisson_pmf(max(0.1, float(line) + 0.5), length)

    components = (
        _component("empirical", empirical, line),
        _component("hierarchical", hierarchical, line),
        _component("player_state", player_state, line),
        _component("market_prior", market_prior, line),
    )
    blended = tuple(
        math.fsum(component.weight * component.pmf[value] for component in components)
        for value in range(length)
    )
    total = math.fsum(blended)
    blended = tuple(value / total for value in blended)
    mean = _mean(blended)
    variance = math.fsum(
        probability * (value - mean) ** 2 for value, probability in enumerate(blended)
    )
    cumulative = 0.0
    median = len(blended) - 1
    for value, probability in enumerate(blended):
        cumulative += probability
        if cumulative >= 0.5:
            median = value
            break
    over, push, under = _line_probabilities(blended, line)
    return EnsembleForecast(
        components=components,
        pmf=blended,
        mean=mean,
        median=median,
        stddev=math.sqrt(variance),
        over=over,
        push=push,
        under=under,
        disagreement=pstdev(component.over for component in components),
    )
