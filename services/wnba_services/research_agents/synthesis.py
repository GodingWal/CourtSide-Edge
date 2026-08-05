"""Combining the model, the research, the market state and policy into one read.

Five analyses beside a forecast is a pile of opinions. What an analyst actually needs is one
read: does the evidence support this decision, contradict it, or fail to reach it at all.

**The synthesizer has no probability of its own, structurally.** It copies the model's number
into the record for context and has nowhere to put a different one -- the table has no column
for one and this module never computes one. That is the boundary the whole research layer rests
on: agents describe, cite, doubt and propose; the number stays the model's. Weighting five agent
confidences into a "research probability" and showing it next to the model's would create a
second, unvalidated forecast whose only quality control is that it sounds thoughtful.

**Agreement is measured, not assumed.** After the adversarial round the agents' confidences are
either close together or not, and the spread is reported. High agreement following a round where
nobody conceded anything is different from high agreement after real revision, so concessions are
counted too.

**Credibility weights the confidences but never the claims.** A role that has been well
calibrated in a domain gets more weight in the agreement statistic. It does not get the power to
promote its claims over another role's -- a cited claim is evidence regardless of who found it,
and a track record is not a reason to stop reading the citation.

Five postures, and the tie-breaks between them are deliberately conservative:

``policy_blocked``    an active rule blocked the decision; nothing else matters
``contested``         live claims contradict each other, or the agents did after arguing
``insufficient_evidence``  the investigation produced too little to support anything
``qualified``         supported, but with concerns a human should read first
``supported``         claims support it, agents agree, and nothing is outstanding
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from wnba_services.research_agents.precedents import PrecedentSet

__all__ = [
    "AgentPosition",
    "Synthesis",
    "SynthesisInputs",
    "record_synthesis",
    "synthesize",
]

# Below this many supporting claims an investigation has not reached a conclusion, it has
# produced paragraphs.
MINIMUM_SUPPORTING_CLAIMS = 2

# Confidence spread across agents above which the panel has not converged. Measured after the
# adversarial round, where disagreement is a finding rather than a starting condition.
DISAGREEMENT_SPREAD = 0.22

# A precedent calibration gap this large is worth surfacing: the reference class says forecasts
# shaped like this one have been stated more confidently than they landed.
PRECEDENT_GAP_CONCERN = 0.08


@dataclass(frozen=True)
class AgentPosition:
    """One agent's final position, after any revision."""

    role: str
    conclusion: str
    confidence: float
    round_number: int
    credibility: float = 0.5
    concessions: int = 0
    unresolved: int = 0
    risk_flags: tuple[str, ...] = ()

    @property
    def revised(self) -> bool:
        return self.round_number > 1


@dataclass(frozen=True)
class SynthesisInputs:
    episode_id: UUID
    model_probability: float
    side: str
    prop_type: str
    positions: tuple[AgentPosition, ...]
    supporting_claim_ids: tuple[UUID, ...]
    contradicting_claim_ids: tuple[UUID, ...]
    policy_blocked: bool
    policy_reasons: tuple[str, ...]
    quote_age_seconds: float | None
    audit_findings: tuple[Mapping[str, Any], ...] = ()
    precedents: PrecedentSet | None = None


@dataclass(frozen=True)
class Synthesis:
    posture: str
    concerns: tuple[dict[str, Any], ...]
    agent_agreement: float | None
    weighted_confidence: float | None
    unresolved_conflicts: int
    narrative: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "posture": self.posture,
            "agent_agreement": (
                None if self.agent_agreement is None else round(self.agent_agreement, 4)
            ),
            "weighted_confidence": (
                None if self.weighted_confidence is None else round(self.weighted_confidence, 4)
            ),
            "unresolved_conflicts": self.unresolved_conflicts,
            "concerns": list(self.concerns),
            "narrative": self.narrative,
            "writes_probability": False,
        }


def _weighted_confidence(positions: Sequence[AgentPosition]) -> float | None:
    if not positions:
        return None
    weights = [max(0.05, position.credibility) for position in positions]
    total = math.fsum(weights)
    return (
        math.fsum(
            weight * position.confidence
            for position, weight in zip(positions, weights, strict=True)
        )
        / total
    )


def _agreement(positions: Sequence[AgentPosition]) -> float | None:
    """1 minus the confidence spread, floored at zero. One agent cannot agree with itself."""
    if len(positions) < 2:
        return None
    confidences = [position.confidence for position in positions]
    mean = math.fsum(confidences) / len(confidences)
    variance = math.fsum((value - mean) ** 2 for value in confidences) / len(confidences)
    return max(0.0, 1.0 - math.sqrt(variance) / 0.5)


def synthesize(inputs: SynthesisInputs) -> Synthesis:
    """Reach one posture over an investigation. Pure, and reads no probability but the model's."""
    positions = inputs.positions
    concerns: list[dict[str, Any]] = []
    unresolved = sum(position.unresolved for position in positions)
    unresolved += len(inputs.contradicting_claim_ids)

    agreement = _agreement(positions)
    weighted = _weighted_confidence(positions)

    for reason in inputs.policy_reasons:
        concerns.append({"kind": "policy", "detail": reason})
    for finding in inputs.audit_findings:
        concerns.append(
            {
                "kind": "data_audit",
                "detail": str(finding.get("detail") or finding.get("code") or "audit finding"),
                "code": finding.get("code"),
            }
        )
    if inputs.contradicting_claim_ids:
        concerns.append(
            {
                "kind": "contradiction",
                "detail": (
                    f"{len(inputs.contradicting_claim_ids)} live claim(s) contradict the "
                    "position this decision rests on."
                ),
            }
        )
    if agreement is not None and agreement < 1.0 - DISAGREEMENT_SPREAD / 0.5:
        concerns.append(
            {
                "kind": "agent_disagreement",
                "detail": (
                    "The panel did not converge after the adversarial round; the spread in "
                    "analyst confidence is wider than the level at which their agreement means "
                    "anything."
                ),
            }
        )
    unresolved_from_agents = sum(position.unresolved for position in positions)
    if unresolved_from_agents and not any(position.concessions for position in positions):
        concerns.append(
            {
                "kind": "unyielding_panel",
                "detail": (
                    "Analysts recorded disagreements and none of them conceded anything. Either "
                    "the disagreement is substantive or the second round did no work."
                ),
            }
        )
    if inputs.quote_age_seconds is not None and inputs.quote_age_seconds >= 5400:
        concerns.append(
            {
                "kind": "market_state",
                "detail": (
                    f"The quote is {int(inputs.quote_age_seconds // 60)} minutes old; the market "
                    "may have moved on news this research did not see."
                ),
            }
        )

    precedents = inputs.precedents
    if precedents is not None and precedents.is_informative:
        gap = precedents.calibration_gap
        if gap is not None and gap >= PRECEDENT_GAP_CONCERN:
            concerns.append(
                {
                    "kind": "precedent",
                    "detail": (
                        f"Across {precedents.count} comparable settled decisions, forecasts of "
                        f"this shape averaged {precedents.mean_predicted:.0%} stated against "
                        f"{precedents.hit_rate:.0%} observed."
                    ),
                }
            )

    # Ordered, and the order is the point: a policy block is dispositive, contradiction outranks
    # thinness, and "supported" is only reachable when nothing above it applies.
    if inputs.policy_blocked:
        posture = "policy_blocked"
    elif inputs.contradicting_claim_ids:
        posture = "contested"
    elif len(inputs.supporting_claim_ids) < MINIMUM_SUPPORTING_CLAIMS:
        posture = "insufficient_evidence"
    elif concerns:
        posture = "qualified"
    else:
        posture = "supported"

    return Synthesis(
        posture=posture,
        concerns=tuple(concerns),
        agent_agreement=agreement,
        weighted_confidence=weighted,
        unresolved_conflicts=unresolved,
        narrative=_narrate(inputs, posture, concerns, agreement),
    )


def _narrate(
    inputs: SynthesisInputs,
    posture: str,
    concerns: Sequence[Mapping[str, Any]],
    agreement: float | None,
) -> str:
    """A few sentences a human reads first. Describes the research, never restates the forecast."""
    roles = ", ".join(sorted(position.role for position in inputs.positions)) or "no analysts"
    revised = sum(1 for position in inputs.positions if position.revised)
    parts = [
        f"{len(inputs.positions)} analyst(s) ({roles}) investigated this decision; "
        f"{revised} revised after seeing their peers.",
        f"{len(inputs.supporting_claim_ids)} cited claim(s) support the position and "
        f"{len(inputs.contradicting_claim_ids)} contradict it.",
    ]
    if agreement is not None:
        parts.append(f"Panel agreement after the adversarial round: {agreement:.0%}.")
    if posture == "policy_blocked":
        parts.append("An active analyst rule blocked this recommendation; research is advisory.")
    if concerns:
        parts.append(
            "Outstanding concerns: "
            + "; ".join(str(concern.get("kind")) for concern in concerns)
            + "."
        )
    parts.append(
        "This synthesis annotates a frozen forecast. It does not change the model's probability "
        "and no research output can."
    )
    return " ".join(parts)


def record_synthesis(
    cur: Any,
    *,
    research_run_id: UUID,
    inputs: SynthesisInputs,
    synthesis: Synthesis,
    at: datetime,
    synthesized_by: str = "decision_synthesizer",
) -> UUID:
    """Store the synthesis beside the episode it describes."""
    synthesis_id = uuid4()
    precedents = inputs.precedents
    cur.execute(
        """INSERT INTO wnba.decision_syntheses
           (synthesis_id,research_run_id,episode_id,created_at,synthesized_by,posture,
            model_probability,policy_blocked,policy_reasons,supporting_claim_ids,
            contradicting_claim_ids,unresolved_conflicts,agent_agreement,weighted_confidence,
            concerns,precedent_episode_ids,precedent_hit_rate,narrative)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (research_run_id) DO NOTHING""",
        (
            synthesis_id,
            research_run_id,
            inputs.episode_id,
            at,
            synthesized_by,
            synthesis.posture,
            inputs.model_probability,
            inputs.policy_blocked,
            Jsonb(list(inputs.policy_reasons)),
            list(inputs.supporting_claim_ids),
            list(inputs.contradicting_claim_ids),
            synthesis.unresolved_conflicts,
            synthesis.agent_agreement,
            synthesis.weighted_confidence,
            Jsonb(list(synthesis.concerns)),
            [] if precedents is None else [p.episode_id for p in precedents.precedents],
            None if precedents is None or not precedents.is_informative else precedents.hit_rate,
            synthesis.narrative,
        ),
    )
    return synthesis_id
