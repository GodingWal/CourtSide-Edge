"""Re-judging active rules on what they actually did, and taking back the ones that hurt.

A rule used to be measured exactly once. `run_rule_backtests` scored it against the settled
record available the week it was proposed, a human signed it, and from then on it ran forever on
a verdict that never updated. Meanwhile `rule_firings` recorded every adjustment it made, in
production, against outcomes that later settled -- the best possible evidence about the rule,
sitting unread.

This module reads it. The difference from the backtest matters: a backtest asks *what would this
rule have done*, replayed over history it was fitted near. This asks *what did it do*, on markets
it had never seen, in the live path. That is out-of-sample by construction.

**Suspension is automatic, and only in one direction.** A rule measured as harmful is suspended
without a human, because suspension removes the rule's effect -- it is a de-escalation, and the
system's standing asymmetry is that it may become more cautious on its own and may never become
more confident on its own. `load_rules` selects `active`, `proposed` and `backtested`, so a
suspended rule stops reaching forecasts the moment the status changes; no engine change was
needed. Going back to `active` is unreachable from here and still requires the named-human path
from migration 024.

**The model does not decide.** DeepSeek is asked whether measured harm looks mechanical or looks
like a bad month, and to write the withdrawal rationale a reviewer would want. The suspension is
already decided by then, from the arithmetic. Its answer is recorded next to the numbers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field
from wnba_domain.decision import brier_score, log_loss
from wnba_store.db import connect

from wnba_services.learning_loop.independence import summarise_sample
from wnba_services.research_agents.advisor import Advisor, advisory_summary

__all__ = [
    "HARMFUL_LOG_LOSS_LOSS",
    "MINIMUM_LIVE_MARKETS",
    "RuleReview",
    "RuleReviewBatch",
    "judge_live_rule",
    "review_active_rules",
]

# Independent markets of live firings before the review will reach any verdict but inconclusive.
# Deliberately lower than the 25 a proposal needs, because these are out-of-sample and each one
# is worth more than a replayed row -- but not so low that one bad week withdraws a good rule.
MINIMUM_LIVE_MARKETS = 15

# Mean log-loss the rule must have *added* before it is called harmful. Same magnitude as the
# gain threshold a proposal has to clear, so a rule is not held to a standard to keep that it
# would not have had to meet to be adopted.
HARMFUL_LOG_LOSS_LOSS = 0.002

# A blocked market hitting at or above this rate means the rule is declining winners.
BLOCK_HARM_HIT_RATE = 0.50


@dataclass(frozen=True)
class RuleReview:
    rule_id: str
    action_kind: str
    firings: int
    independent_markets: float
    log_loss_before: float | None
    log_loss_after: float | None
    brier_before: float | None
    brier_after: float | None
    blocked_hit_rate: float | None
    verdict: str
    detail: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "method": "live-firings-v1",
            "action_kind": self.action_kind,
            "firings": self.firings,
            "independent_markets": round(self.independent_markets, 2),
            "log_loss_before": self.log_loss_before,
            "log_loss_after": self.log_loss_after,
            "brier_before": self.brier_before,
            "brier_after": self.brier_after,
            "blocked_hit_rate": self.blocked_hit_rate,
            "verdict": self.verdict,
            "detail": self.detail,
            "minimum_live_markets": MINIMUM_LIVE_MARKETS,
            "reactivation_automatic": False,
        }


@dataclass(frozen=True)
class RuleReviewBatch:
    reviewed: int
    helpful: int
    harmful: int
    inconclusive: int
    suspended: int
    advisories: dict[str, int]


class WithdrawalOpinion(BaseModel):
    """The model's read on a suspension that has already been decided."""

    model_config = ConfigDict(strict=True, extra="forbid")

    looks_mechanical: bool
    withdrawal_rationale: Annotated[str, Field(min_length=20, max_length=1500)]
    alternative_explanations: Annotated[
        list[Annotated[str, Field(min_length=3, max_length=300)]], Field(max_length=5)
    ]


def _mean(values: list[float]) -> float | None:
    return None if not values else round(math.fsum(values) / len(values), 6)


def judge_live_rule(
    *,
    action_kind: str,
    independent_markets: float,
    log_loss_before: float | None,
    log_loss_after: float | None,
    blocked_hit_rate: float | None,
) -> tuple[str, str]:
    """Three words from the measurements, erring toward ``inconclusive``. Pure and testable."""
    if independent_markets < MINIMUM_LIVE_MARKETS:
        return (
            "inconclusive",
            f"fired on {independent_markets:.1f} independent market(s), below the "
            f"{MINIMUM_LIVE_MARKETS} needed to judge a live rule",
        )

    if action_kind == "block":
        if blocked_hit_rate is None:
            return "inconclusive", "no settled blocked markets to score"
        if blocked_hit_rate >= BLOCK_HARM_HIT_RATE:
            return (
                "harmful",
                f"blocked markets hit {blocked_hit_rate:.1%} of the time, at or above the "
                f"{BLOCK_HARM_HIT_RATE:.0%} that makes a block a declined winner",
            )
        return (
            "helpful",
            f"blocked markets hit only {blocked_hit_rate:.1%} of the time, below the "
            f"{BLOCK_HARM_HIT_RATE:.0%} threshold",
        )

    if action_kind != "shrink_toward_even":
        # flag_for_review and require_evidence move no probability. There is nothing here to
        # measure, and a procedural rule's value is a judgement rather than a number.
        return (
            "inconclusive",
            f"{action_kind} changes no probability; its value is procedural and is not "
            "measurable from firings",
        )

    if log_loss_before is None or log_loss_after is None:
        return "inconclusive", "no scoreable firings"
    gain = log_loss_before - log_loss_after
    if gain < -HARMFUL_LOG_LOSS_LOSS:
        return (
            "harmful",
            f"added {abs(gain):.4f} mean log loss across {independent_markets:.1f} independent "
            f"live market(s); the adjustment is making settled forecasts worse",
        )
    if gain > HARMFUL_LOG_LOSS_LOSS:
        return (
            "helpful",
            f"removed {gain:.4f} mean log loss across {independent_markets:.1f} independent "
            f"live market(s)",
        )
    return "inconclusive", f"changed mean log loss by {gain:+.4f}, inside the noise threshold"


def review_active_rules(
    *, now: datetime | None = None, advisor: Advisor | None = None
) -> RuleReviewBatch:
    """Score every active rule on its own live firings; suspend the harmful ones."""
    at = now or datetime.now(UTC)
    reviewed = helpful = harmful = inconclusive = suspended = 0

    with connect() as conn, conn.cursor() as cur:
        assistant = advisor or Advisor(cur)
        cur.execute(
            """SELECT rule_id,title,rationale,definition,priority
               FROM wnba.analyst_rules WHERE status='active' ORDER BY rule_id"""
        )
        rules = cur.fetchall()

        for rule in rules:
            reviewed += 1
            rule_id = str(rule["rule_id"])
            definition = rule["definition"] if isinstance(rule["definition"], dict) else {}
            action = definition.get("action", {})
            action_kind = (
                str(action.get("kind", "unknown")) if isinstance(action, dict) else "unknown"
            )

            # Real firings only. A shadow firing measures a rule that was not affecting anything,
            # which is the right evidence for adoption and the wrong evidence for withdrawal.
            cur.execute(
                """SELECT f.probability_before,f.probability_after,o.hit,
                          d.player_id,d.game_id,d.prop_type
                   FROM wnba.rule_firings f
                   JOIN wnba.decision_episodes d ON d.episode_id=f.episode_id
                   JOIN wnba.episode_outcomes o ON o.episode_id=d.episode_id
                   WHERE f.rule_id=%s AND NOT f.shadow
                     AND NOT o.was_voided AND NOT o.was_push""",
                (rule_id,),
            )
            firings = cur.fetchall()
            shape = summarise_sample(firings)

            before_log: list[float] = []
            after_log: list[float] = []
            before_brier: list[float] = []
            after_brier: list[float] = []
            hits: list[float] = []
            for row in firings:
                hit = int(bool(row["hit"]))
                before = float(str(row["probability_before"]))
                after = float(str(row["probability_after"]))
                before_log.append(log_loss(before, hit))
                after_log.append(log_loss(after, hit))
                before_brier.append(brier_score(before, hit))
                after_brier.append(brier_score(after, hit))
                hits.append(float(hit))

            blocked_hit_rate = (
                round(math.fsum(hits) / len(hits), 4) if action_kind == "block" and hits else None
            )
            verdict, detail = judge_live_rule(
                action_kind=action_kind,
                independent_markets=shape.effective,
                log_loss_before=_mean(before_log),
                log_loss_after=_mean(after_log),
                blocked_hit_rate=blocked_hit_rate,
            )
            review = RuleReview(
                rule_id=rule_id,
                action_kind=action_kind,
                firings=len(firings),
                independent_markets=shape.effective,
                log_loss_before=_mean(before_log),
                log_loss_after=_mean(after_log),
                brier_before=_mean(before_brier),
                brier_after=_mean(after_brier),
                blocked_hit_rate=blocked_hit_rate,
                verdict=verdict,
                detail=detail,
            )
            payload = review.to_payload()
            payload["sample_shape"] = shape.to_payload()

            helpful += verdict == "helpful"
            inconclusive += verdict == "inconclusive"

            if verdict != "harmful":
                cur.execute(
                    """UPDATE wnba.analyst_rules SET live_review=%s,last_reviewed_at=%s
                       WHERE rule_id=%s AND status='active'""",
                    (Jsonb(payload), at, rule_id),
                )
                continue

            harmful += 1
            opinion = _withdrawal_opinion(assistant, rule=rule, review=review, at=at)
            reason = (
                f"Suspended automatically on measured live behaviour: {detail}. "
                "Reactivation requires a named human."
            )
            if opinion is not None:
                payload["model_opinion"] = {
                    "looks_mechanical": opinion.looks_mechanical,
                    "withdrawal_rationale": opinion.withdrawal_rationale,
                    "alternative_explanations": opinion.alternative_explanations,
                }
            cur.execute(
                """UPDATE wnba.analyst_rules
                   SET status='suspended',suspended_at=%s,suspension_reason=%s,
                       live_review=%s,last_reviewed_at=%s
                   WHERE rule_id=%s AND status='active'""",
                (at, reason, Jsonb(payload), at, rule_id),
            )
            suspended += cur.rowcount

        summary = advisory_summary(assistant.outcomes)

    return RuleReviewBatch(
        reviewed=reviewed,
        helpful=helpful,
        harmful=harmful,
        inconclusive=inconclusive,
        suspended=suspended,
        advisories=summary,
    )


def _withdrawal_opinion(
    advisor: Advisor, *, rule: Any, review: RuleReview, at: datetime
) -> WithdrawalOpinion | None:
    return advisor.advise(
        task="rule_withdrawal",
        subject=review.rule_id,
        system=(
            "You are reviewing a WNBA forecasting rule that has just been suspended "
            "automatically for measured harm. Return JSON only. The suspension is already "
            "decided by the arithmetic and you cannot reverse it; say whether the harm looks "
            "mechanical -- a genuine flaw in the rule's logic -- or like an artefact of the "
            "sample, and write the withdrawal rationale a reviewer would need. Never output a "
            "probability for a prop, a recommendation, or a stake."
        ),
        question={
            "rule_id": review.rule_id,
            "title": str(rule["title"]),
            "original_rationale": str(rule["rationale"]),
            "definition": rule["definition"] if isinstance(rule["definition"], dict) else {},
            "measured_live_behaviour": {
                "action_kind": review.action_kind,
                "firings": review.firings,
                "independent_markets": round(review.independent_markets, 2),
                "log_loss_before": review.log_loss_before,
                "log_loss_after": review.log_loss_after,
                "brier_before": review.brier_before,
                "brier_after": review.brier_after,
                "blocked_hit_rate": review.blocked_hit_rate,
                "verdict_detail": review.detail,
            },
        },
        schema=WithdrawalOpinion,
        now=at,
    )
