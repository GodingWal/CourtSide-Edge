"""Deterministic synthesis of a research run into one auditable verdict.

Five agents producing five paragraphs is not research; it is five paragraphs. Something has to
say what the disagreement *amounted to*, and that something must be code rather than a sixth
model call, because a summary written by the provider cannot be replayed, cannot be audited, and
would quietly become the place where a probability sneaks back in.

So the verdict is computed here, from the stored claims and citations alone. It reports how much
of the analysts' cited work the skeptic contradicted and how loudly, and nothing else. It has no
access to the forecast, the line, or the market price -- there is no field it could put a prop
probability in, and no input it could derive one from. Its output is a caution level for a human
reading the evidence file, never a term in the model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from wnba_services.research_agents.deepseek import AgentAnalysis

__all__ = ["ResearchVerdict", "synthesize"]

# Below this share of uncontested claims the file is treated as unsettled regardless of tone.
AGREEMENT_FLOOR = 0.5
# A skeptic this sure of its objection escalates a contested file on its own.
CONFIDENT_SKEPTIC = 0.7


@dataclass(frozen=True)
class ResearchVerdict:
    """What the run established, and how much of it survived the skeptic."""

    caution: str
    agreement: float
    total_claims: int
    contested_claims: int
    contested_predicates: tuple[str, ...]
    risk_flags: tuple[str, ...]
    skeptic_confidence: float
    rationale: str


def _unique(values: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for value in values:
        seen.setdefault(value, None)
    return tuple(seen)


def synthesize(
    *,
    analyses: Mapping[str, AgentAnalysis],
    skeptic: AgentAnalysis,
    peer_evidence: Mapping[str, UUID],
) -> ResearchVerdict:
    """Reduce one run to a verdict.

    ``analyses`` are the stage-one analysts by role, ``skeptic`` is the stage-two review, and
    ``peer_evidence`` maps each analyst's role to the evidence id under which its conclusion was
    handed to the skeptic. A claim counts as contested when the skeptic contradicted evidence the
    claim rests on, or contradicted the conclusion of the analyst that made it -- the two ways an
    objection can actually land.
    """
    contradicted = {
        evidence_id for claim in skeptic.claims for evidence_id in claim.contradicting_evidence_ids
    }
    total_claims = 0
    contested_claims = 0
    contested_predicates: list[str] = []
    for role, analysis in sorted(analyses.items()):
        peer_id = peer_evidence.get(role)
        role_disputed = peer_id is not None and peer_id in contradicted
        for claim in analysis.claims:
            total_claims += 1
            if role_disputed or contradicted.intersection(claim.evidence_ids):
                contested_claims += 1
                contested_predicates.append(f"{role}:{claim.predicate}")

    agreement = 1.0 - contested_claims / total_claims if total_claims else 0.0
    risk_flags = _unique(
        [flag for _, analysis in sorted(analyses.items()) for flag in analysis.risk_flags]
        + list(skeptic.risk_flags)
    )

    if total_claims == 0:
        caution = "high"
        rationale = "No analyst produced a claim the supplied evidence could support."
    elif agreement < AGREEMENT_FLOOR:
        caution = "high"
        rationale = (
            f"The skeptic contested {contested_claims} of {total_claims} cited claims, "
            "leaving most of the file disputed."
        )
    elif contested_claims and skeptic.confidence >= CONFIDENT_SKEPTIC:
        caution = "high"
        rationale = (
            f"The skeptic contested {contested_claims} of {total_claims} cited claims and holds "
            f"that objection at {skeptic.confidence:.0%} confidence."
        )
    elif contested_claims:
        caution = "elevated"
        rationale = (
            f"The skeptic contested {contested_claims} of {total_claims} cited claims but is "
            f"only {skeptic.confidence:.0%} confident in the objection."
        )
    elif risk_flags:
        caution = "elevated"
        rationale = (
            f"No cited claim was contested, but {len(risk_flags)} unresolved risk flag(s) remain."
        )
    else:
        caution = "low"
        rationale = f"All {total_claims} cited claims went uncontested with no risk flags raised."

    return ResearchVerdict(
        caution=caution,
        agreement=agreement,
        total_claims=total_claims,
        contested_claims=contested_claims,
        contested_predicates=tuple(contested_predicates),
        risk_flags=risk_flags,
        skeptic_confidence=skeptic.confidence,
        rationale=rationale,
    )
