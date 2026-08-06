"""What the adversarial review adds up to.

The verdict is the only part of a research run a human reads before the rest. If it can be
computed two ways, or if it drifts with the model's mood rather than with the citations, it is
worse than nothing: it launders five paragraphs of prose into a number that looks measured.
So it is pure, deterministic, and derived from the citation graph alone.
"""

from __future__ import annotations

from dataclasses import fields
from uuid import UUID, uuid4

from wnba_services.research_agents.deepseek import AgentAnalysis
from wnba_services.research_agents.synthesis import ResearchVerdict, synthesize

AVAILABILITY_EVIDENCE = uuid4()
MATCHUP_EVIDENCE = uuid4()
PEER = {"availability": uuid4(), "matchup": uuid4()}


def analysis(
    *,
    confidence: float = 0.6,
    claims: list[dict[str, object]] | None = None,
    risk_flags: list[str] | None = None,
) -> AgentAnalysis:
    return AgentAnalysis.model_validate(
        {
            "conclusion": "A conclusion.",
            "confidence": confidence,
            "claims": claims or [],
            "risk_flags": risk_flags or [],
        }
    )


def claim(
    predicate: str,
    *,
    supports: list[UUID],
    contradicts: list[UUID] | None = None,
) -> dict[str, object]:
    return {
        "predicate": predicate,
        "value": "a value",
        "confidence": 0.7,
        "evidence_ids": supports,
        "contradicting_evidence_ids": contradicts or [],
    }


def two_analysts() -> dict[str, AgentAnalysis]:
    return {
        "availability": analysis(claims=[claim("status", supports=[AVAILABILITY_EVIDENCE])]),
        "matchup": analysis(claims=[claim("pace", supports=[MATCHUP_EVIDENCE])]),
    }


def verdict_for(skeptic: AgentAnalysis) -> ResearchVerdict:
    return synthesize(analyses=two_analysts(), skeptic=skeptic, peer_evidence=PEER)


# --------------------------------------------------------------------------------------
# Caution levels
# --------------------------------------------------------------------------------------
def test_an_uncontested_file_with_no_flags_is_low_caution() -> None:
    result = verdict_for(analysis())
    assert result.caution == "low"
    assert result.agreement == 1.0
    assert (result.total_claims, result.contested_claims) == (2, 0)


def test_unresolved_risk_flags_alone_raise_caution() -> None:
    result = verdict_for(analysis(risk_flags=["status could change at shootaround"]))
    assert result.caution == "elevated"
    assert result.risk_flags == ("status could change at shootaround",)


def test_contradicting_the_evidence_under_a_claim_contests_it() -> None:
    skeptic = analysis(
        confidence=0.4,
        claims=[claim("stale", supports=[MATCHUP_EVIDENCE], contradicts=[AVAILABILITY_EVIDENCE])],
    )
    result = verdict_for(skeptic)
    assert result.contested_claims == 1
    assert result.contested_predicates == ("availability:status",)
    assert result.agreement == 0.5
    assert result.caution == "elevated"


def test_contradicting_an_analyst_conclusion_contests_everything_it_claimed() -> None:
    """The objection lands on the analyst, not on one line of its evidence."""
    skeptic = analysis(
        confidence=0.4,
        claims=[claim("overstated", supports=[MATCHUP_EVIDENCE], contradicts=[PEER["matchup"]])],
    )
    result = verdict_for(skeptic)
    assert result.contested_predicates == ("matchup:pace",)


def test_a_confident_skeptic_escalates_a_contested_file() -> None:
    skeptic = analysis(
        confidence=0.85,
        claims=[claim("stale", supports=[MATCHUP_EVIDENCE], contradicts=[AVAILABILITY_EVIDENCE])],
    )
    result = verdict_for(skeptic)
    assert result.caution == "high"
    assert "85%" in result.rationale


def test_a_mostly_disputed_file_is_high_caution_however_softly_it_is_put() -> None:
    skeptic = analysis(
        confidence=0.1,
        claims=[
            claim(
                "unsupported",
                supports=[MATCHUP_EVIDENCE],
                contradicts=[AVAILABILITY_EVIDENCE, PEER["matchup"]],
            )
        ],
    )
    result = verdict_for(skeptic)
    assert (result.contested_claims, result.agreement) == (2, 0.0)
    assert result.caution == "high"


def test_a_file_where_nothing_could_be_claimed_is_high_caution() -> None:
    """Silence is not agreement. Nobody found evidence for anything."""
    result = synthesize(
        analyses={"availability": analysis(), "matchup": analysis()},
        skeptic=analysis(),
        peer_evidence=PEER,
    )
    assert result.caution == "high"
    assert result.total_claims == 0
    assert result.agreement == 0.0


# --------------------------------------------------------------------------------------
# Invariants
# --------------------------------------------------------------------------------------
def test_the_verdict_cannot_carry_a_prop_probability() -> None:
    """Invariant 7 structurally: there is no field for one, and no input to derive one from."""
    names = {field.name for field in fields(ResearchVerdict)}
    assert not names & {"probability", "predicted_probability", "edge", "recommendation", "side"}


def test_risk_flags_are_deduplicated_across_agents_in_a_stable_order() -> None:
    analyses = {
        "availability": analysis(risk_flags=["thin sample", "status unclear"]),
        "matchup": analysis(risk_flags=["thin sample"]),
    }
    result = synthesize(
        analyses=analyses,
        skeptic=analysis(risk_flags=["status unclear", "line moved late"]),
        peer_evidence=PEER,
    )
    assert result.risk_flags == ("thin sample", "status unclear", "line moved late")


def test_synthesis_is_deterministic_under_agent_ordering() -> None:
    skeptic = analysis(
        confidence=0.4,
        claims=[claim("stale", supports=[MATCHUP_EVIDENCE], contradicts=[AVAILABILITY_EVIDENCE])],
    )
    forward = two_analysts()
    reversed_order = dict(reversed(list(forward.items())))
    assert synthesize(analyses=forward, skeptic=skeptic, peer_evidence=PEER) == synthesize(
        analyses=reversed_order, skeptic=skeptic, peer_evidence=PEER
    )
