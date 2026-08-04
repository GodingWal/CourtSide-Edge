"""What a proposed rule would have done, measured before anyone is asked to approve it.

The rules package enforced that no rule may go active without a named human approver, and the
schema enforced that no rule may go active without a backtest. Nothing could produce a backtest,
so nothing could ever legitimately go active -- and the live database held zero rules and zero
firings. The governance was complete and the pipeline that would feed it did not exist.

This is that pipeline's evidence stage. Given a candidate rule and the settled record, it answers
the three questions an approver actually has:

* **How often would this have fired?** A rule that matches four episodes is untestable, however
  sensible it sounds. A rule that matches ninety per cent of them is not a rule, it is a
  recalibration wearing a disguise.
* **Would it have helped?** Measured as mean log loss on the episodes it fired on, before its
  adjustment against after. A shrink rule that fires on forecasts which were *right* makes the
  record worse, and the fact that its reasoning sounds wise does not change that.
* **What did it block?** For a blocking rule the question is whether the recommendations it would
  have refused actually lost. Blocking winners is expensive and invisible.

**Firing counts are counted in independent markets, not rows.** A rule that fires on forty
snapshots of the same three markets has three markets of evidence behind it.

**Nothing here promotes anything.** The verdict is written to the rule's ``backtest`` column and
the status advances at most from ``proposed`` to ``backtested``. Moving to ``active`` needs a human
name, which is the one thing this module structurally cannot supply.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from wnba_domain.decision import brier_score, log_loss
from wnba_rules.dsl import ActionKind, Rule

from wnba_services.learning_loop.independence import summarise_sample

__all__ = [
    "MAXIMUM_COVERAGE",
    "MINIMUM_FIRED_MARKETS",
    "RuleBacktest",
    "SettledEpisode",
    "backtest_rule",
]

# Below this many independent markets a rule has not been tested, it has been anecdote-checked.
MINIMUM_FIRED_MARKETS = 25

# A rule firing on nearly everything is not encoding a judgement about a situation; it is a global
# adjustment, and global adjustments belong in the calibration map where they are fitted rather
# than argued for.
MAXIMUM_COVERAGE = 0.60

_MINIMUM_LOG_LOSS_GAIN = 0.002


@dataclass(frozen=True)
class SettledEpisode:
    """One settled decision, reduced to what a rule can see and what actually happened."""

    player_id: str
    game_id: str
    prop_type: str
    facts: Mapping[str, object]
    probability: float
    """Predicted probability of the side that was actually selected."""

    hit: bool
    forecast_timestamp: Any = None

    def as_row(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "game_id": self.game_id,
            "prop_type": self.prop_type,
            "forecast_timestamp": self.forecast_timestamp,
        }


@dataclass(frozen=True)
class RuleBacktest:
    """Everything an approver needs to decide, and nothing that decides for them."""

    rule_id: str
    action_kind: str
    episodes: int
    independent_markets: int
    fired: int
    fired_markets: int
    coverage: float
    log_loss_before: float | None
    log_loss_after: float | None
    brier_before: float | None
    brier_after: float | None
    blocked_hit_rate: float | None
    breakeven: float | None
    verdict: str
    detail: str

    @property
    def log_loss_gain(self) -> float:
        if self.log_loss_before is None or self.log_loss_after is None:
            return 0.0
        return self.log_loss_before - self.log_loss_after

    @property
    def is_promotable(self) -> bool:
        """Whether a human may now be *asked*. Never whether the rule should be activated."""
        return self.verdict == "helpful"

    def to_payload(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "action_kind": self.action_kind,
            "episodes": self.episodes,
            "independent_markets": self.independent_markets,
            "fired": self.fired,
            "fired_markets": self.fired_markets,
            "coverage": round(self.coverage, 4),
            "log_loss_before": self.log_loss_before,
            "log_loss_after": self.log_loss_after,
            "log_loss_gain": round(self.log_loss_gain, 5),
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "blocked_hit_rate": self.blocked_hit_rate,
            "breakeven": self.breakeven,
            "verdict": self.verdict,
            "detail": self.detail,
            "minimum_fired_markets": MINIMUM_FIRED_MARKETS,
            "maximum_coverage": MAXIMUM_COVERAGE,
            "promotion_requires_a_human": True,
        }


def _mean(values: Sequence[float]) -> float | None:
    return math.fsum(values) / len(values) if values else None


def backtest_rule(
    rule: Rule,
    episodes: Sequence[SettledEpisode],
    *,
    breakeven: float | None = None,
    minimum_fired_markets: int = MINIMUM_FIRED_MARKETS,
) -> RuleBacktest:
    """Replay one rule over settled episodes and report what it would have done.

    The rule's action is applied directly rather than by running the engine, because the engine
    correctly refuses to *apply* a rule that is not yet active -- which is precisely the state
    every rule is in when it needs a backtest. Reading ``matches`` and ``action.apply`` uses the
    same public surface without having to forge an approval to get an answer.
    """
    all_rows = [episode.as_row() for episode in episodes]
    shape = summarise_sample(all_rows)

    fired: list[SettledEpisode] = []
    adjusted: list[float] = []
    for episode in episodes:
        if not rule.matches(episode.facts):
            continue
        fired.append(episode)
        adjusted.append(rule.action.apply(episode.probability))

    fired_shape = summarise_sample([episode.as_row() for episode in fired])
    coverage = fired_shape.markets / shape.markets if shape.markets else 0.0

    before_log = [log_loss(e.probability, int(e.hit)) for e in fired]
    after_log = [
        log_loss(probability, int(episode.hit))
        for episode, probability in zip(fired, adjusted, strict=True)
    ]
    before_brier = [brier_score(e.probability, int(e.hit)) for e in fired]
    after_brier = [
        brier_score(probability, int(episode.hit))
        for episode, probability in zip(fired, adjusted, strict=True)
    ]

    blocked_hit_rate = None
    if rule.action.kind is ActionKind.BLOCK and fired:
        blocked_hit_rate = math.fsum(float(e.hit) for e in fired) / len(fired)

    verdict, detail = _judge(
        rule=rule,
        fired_markets=fired_shape.markets,
        coverage=coverage,
        before=_mean(before_log),
        after=_mean(after_log),
        blocked_hit_rate=blocked_hit_rate,
        breakeven=breakeven,
        minimum_fired_markets=minimum_fired_markets,
    )

    return RuleBacktest(
        rule_id=rule.rule_id,
        action_kind=rule.action.kind.value,
        episodes=len(episodes),
        independent_markets=shape.markets,
        fired=len(fired),
        fired_markets=fired_shape.markets,
        coverage=coverage,
        log_loss_before=_mean(before_log),
        log_loss_after=_mean(after_log),
        brier_before=_mean(before_brier),
        brier_after=_mean(after_brier),
        blocked_hit_rate=blocked_hit_rate,
        breakeven=breakeven,
        verdict=verdict,
        detail=detail,
    )


def _judge(
    *,
    rule: Rule,
    fired_markets: int,
    coverage: float,
    before: float | None,
    after: float | None,
    blocked_hit_rate: float | None,
    breakeven: float | None,
    minimum_fired_markets: int,
) -> tuple[str, str]:
    """Turn the measurements into one of three words, erring toward ``inconclusive``."""
    if fired_markets == 0:
        return "inconclusive", "the rule never fired on the settled record"
    if fired_markets < minimum_fired_markets:
        return (
            "inconclusive",
            f"fired on {fired_markets} independent market(s), below the {minimum_fired_markets} "
            "needed to distinguish an effect from noise",
        )
    if coverage > MAXIMUM_COVERAGE:
        return (
            "harmful",
            f"fired on {coverage:.0%} of markets, which is a global recalibration rather than a "
            "judgement about a situation; fit it as a calibration map instead",
        )

    if rule.action.kind is ActionKind.BLOCK:
        if blocked_hit_rate is None:
            return "inconclusive", "no blocked episodes to score"
        threshold = breakeven if breakeven is not None else 0.5
        if blocked_hit_rate < threshold:
            return (
                "helpful",
                f"would have blocked {fired_markets} market(s) that hit only "
                f"{blocked_hit_rate:.1%} of the time, below the {threshold:.1%} needed",
            )
        return (
            "harmful",
            f"would have blocked {fired_markets} market(s) that hit {blocked_hit_rate:.1%} of the "
            f"time, at or above the {threshold:.1%} needed -- these were winners",
        )

    if rule.action.kind is not ActionKind.SHRINK_TOWARD_EVEN:
        # Flag and require-evidence actions change no probability, so there is nothing to score.
        return (
            "inconclusive",
            f"{rule.action.kind.value} changes no probability; its value is procedural and has "
            "to be judged by a human rather than measured here",
        )

    if before is None or after is None:
        return "inconclusive", "no scoreable episodes"
    gain = before - after
    if gain > _MINIMUM_LOG_LOSS_GAIN:
        return (
            "helpful",
            f"reduced mean log loss from {before:.4f} to {after:.4f} across {fired_markets} "
            "independent market(s)",
        )
    if gain < -_MINIMUM_LOG_LOSS_GAIN:
        return (
            "harmful",
            f"raised mean log loss from {before:.4f} to {after:.4f}; it fired on forecasts that "
            "were already right",
        )
    return (
        "inconclusive",
        f"changed mean log loss by {gain:+.5f}, which is indistinguishable from noise",
    )
