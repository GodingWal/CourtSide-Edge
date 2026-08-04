"""Applying rules, and recording exactly what they did.

Two properties matter more than the mechanics:

* **Only ACTIVE rules affect output.** Proposed and backtested rules are evaluated in shadow
  so their real-world effect is measurable before anyone trusts them, which is the same
  champion/challenger discipline the models get.
* **Every firing is recorded, including shadow firings.** A silent adjustment is
  indistinguishable from a bug. When a forecast comes out at 0.55 instead of 0.68, the
  system must be able to name the rule that did it and the analyst who approved that rule.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from wnba_rules.dsl import ActionKind, Rule, RuleStatus

__all__ = ["Firing", "RuleOutcome", "apply_rules"]


@dataclass(frozen=True)
class Firing:
    rule_id: str
    kind: ActionKind
    shadow: bool
    probability_before: float
    probability_after: float
    note: str = ""

    @property
    def delta(self) -> float:
        return self.probability_after - self.probability_before


@dataclass
class RuleOutcome:
    probability: float
    blocked: bool = False
    needs_review: bool = False
    needs_evidence: bool = False
    firings: list[Firing] = field(default_factory=list)
    block_reasons: list[str] = field(default_factory=list)

    @property
    def was_adjusted(self) -> bool:
        return any(f.delta != 0.0 for f in self.firings if not f.shadow)

    def explain(self) -> str:
        if not self.firings:
            return "no rules fired"
        parts = [
            f"{f.rule_id}{' (shadow)' if f.shadow else ''}: {f.kind.value}"
            + (f" {f.probability_before:.3f}->{f.probability_after:.3f}" if f.delta else "")
            for f in self.firings
        ]
        return "; ".join(parts)


def apply_rules(
    rules: list[Rule],
    facts: Mapping[str, object],
    probability: float,
    *,
    include_shadow: bool = True,
) -> RuleOutcome:
    """Run every matching rule against one candidate decision.

    Rules are applied in priority order and their effects compose, so two overlapping
    shrink rules both bite rather than the last one winning. Because every action moves
    toward 0.5 and never past it, composition converges instead of oscillating -- which is
    why the ordering is a readability concern rather than a correctness one.
    """
    outcome = RuleOutcome(probability=probability)

    for rule in sorted(rules, key=lambda r: (-r.priority, r.rule_id)):
        if rule.status is RuleStatus.ACTIVE:
            shadow = False
        elif include_shadow and rule.status in (RuleStatus.PROPOSED, RuleStatus.BACKTESTED):
            shadow = True
        else:
            continue  # rejected and retired rules never run, in shadow or otherwise

        if not rule.matches(facts):
            continue

        before = outcome.probability
        after = before

        if rule.action.kind is ActionKind.SHRINK_TOWARD_EVEN and not shadow:
            after = rule.action.apply(before)
            outcome.probability = after
        elif rule.action.kind is ActionKind.SHRINK_TOWARD_EVEN:
            # Shadow firings are scored, not applied.
            after = rule.action.apply(before)

        if not shadow:
            if rule.action.kind is ActionKind.BLOCK:
                outcome.blocked = True
                outcome.block_reasons.append(f"{rule.rule_id}: {rule.title}")
            elif rule.action.kind is ActionKind.FLAG_FOR_REVIEW:
                outcome.needs_review = True
            elif rule.action.kind is ActionKind.REQUIRE_EVIDENCE:
                outcome.needs_evidence = True

        outcome.firings.append(
            Firing(
                rule_id=rule.rule_id,
                kind=rule.action.kind,
                shadow=shadow,
                probability_before=before,
                probability_after=after,
                note=rule.action.note,
            )
        )

    return outcome
