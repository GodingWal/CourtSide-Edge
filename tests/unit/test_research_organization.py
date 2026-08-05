"""The research organization's gates, and the boundary nothing in it may cross.

Two kinds of test here. The first kind checks that each new stage does its job: the auditor
blocks contradictions, precedent retrieval refuses incomparable situations, the synthesizer
reaches the conservative posture. The second kind checks the boundary -- that no schema an agent
can fill in has anywhere to put a probability, and that nothing in the research path writes one.
The second kind is the one that would matter if the first kind all passed and the design were
still wrong.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError
from wnba_services.research_agents.auditor import (
    BLOCKING_QUOTE_AGE,
    MAXIMUM_QUOTE_AGE,
    audit_plan_inputs,
)
from wnba_services.research_agents.credibility import (
    CREDIBILITY_PRIOR,
    expected_calibration,
    pooled_score,
    source_reliability_for,
)
from wnba_services.research_agents.deepseek import (
    AgentAnalysis,
    AnalysisRevision,
    RuleProposalDraft,
)
from wnba_services.research_agents.precedents import (
    MINIMUM_PRECEDENTS,
    find_precedents,
    similarity,
)
from wnba_services.research_agents.synthesis import (
    AgentPosition,
    SynthesisInputs,
    synthesize,
)
from wnba_services.research_agents.workflow import (
    CLAIM_REFRESH,
    ROLE_DOMAIN,
    _snapshot_evidence,
)

NOW = datetime(2026, 8, 5, 18, tzinfo=UTC)
LOCKS = NOW + timedelta(hours=3)


# --------------------------------------------------------------------------------------
# The data auditor
# --------------------------------------------------------------------------------------
def _inputs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "locks_at": LOCKS,
        "quote_observed_at": NOW - timedelta(minutes=5),
        "injury_designation": "available",
        "availability_probability": 0.95,
        "start_probability": 0.9,
        "expected_minutes": 31.0,
        "history_games": 20,
        "data_quality_score": 0.9,
        "contradicting_claims": 0,
    }
    base.update(overrides)
    return base


def test_clean_inputs_clear_the_audit() -> None:
    report = audit_plan_inputs(_inputs(), now=NOW)

    assert report.verdict == "clear"
    assert report.findings == ()
    assert not report.is_blocked


def test_an_out_designation_with_starter_minutes_blocks_the_investigation() -> None:
    """The contradiction a language model would smooth over into a fluent paragraph."""
    report = audit_plan_inputs(_inputs(injury_designation="out", expected_minutes=28.0), now=NOW)

    assert report.is_blocked
    assert "availability_contradicts_role" in {finding.code for finding in report.blocking}


def test_a_certain_starter_with_bench_minutes_blocks() -> None:
    report = audit_plan_inputs(_inputs(start_probability=0.95, expected_minutes=6.0), now=NOW)

    assert report.is_blocked
    assert "start_probability_contradicts_minutes" in {f.code for f in report.blocking}


def test_a_locked_market_blocks_research_that_could_not_inform_anything() -> None:
    report = audit_plan_inputs(_inputs(locks_at=NOW - timedelta(minutes=1)), now=NOW)

    assert report.is_blocked
    assert "market_locked" in {finding.code for finding in report.blocking}


@pytest.mark.parametrize(
    ("age", "expected_verdict", "expected_code"),
    [
        (MAXIMUM_QUOTE_AGE + timedelta(minutes=1), "degraded", "quote_stale"),
        (BLOCKING_QUOTE_AGE + timedelta(minutes=1), "blocked", "quote_archival"),
    ],
)
def test_quote_age_escalates_from_degraded_to_blocking(
    age: timedelta, expected_verdict: str, expected_code: str
) -> None:
    report = audit_plan_inputs(_inputs(quote_observed_at=NOW - age), now=NOW)

    assert report.verdict == expected_verdict
    assert expected_code in {finding.code for finding in report.findings}


def test_degraded_findings_reach_the_agents_and_blocking_ones_do_not() -> None:
    """A blocked plan never reaches an agent, so its findings are not agent context."""
    degraded = audit_plan_inputs(_inputs(history_games=3), now=NOW)
    blocked = audit_plan_inputs(_inputs(injury_designation="out", expected_minutes=30.0), now=NOW)

    assert [finding["code"] for finding in degraded.context_for_agents()] == ["thin_history"]
    assert blocked.context_for_agents() == []


# --------------------------------------------------------------------------------------
# Precedent retrieval
# --------------------------------------------------------------------------------------
def _episode(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "episode_id": uuid4(),
        "game_id": f"game-{uuid4()}",
        "prop_type": "points",
        "side": "over",
        "line": 14.5,
        "predicted_probability": 0.58,
        "projected_minutes": 30.0,
        "model_disagreement": 0.05,
        "start_probability": 0.9,
        "data_quality_score": 0.9,
        "hit": True,
        "full_name": "A Player",
    }
    base.update(overrides)
    return base


def test_a_different_market_is_incomparable_rather_than_distant() -> None:
    assert similarity(_episode(), _episode(prop_type="rebounds")) is None


def test_too_few_shared_dimensions_refuses_to_report_a_distance() -> None:
    """A confident zero computed from one shared field is worse than no answer."""
    sparse = {"prop_type": "points", "line": 14.5}

    assert similarity(sparse, _episode()) is None


def test_precedents_rank_by_situation_and_report_their_own_thinness() -> None:
    subject = _episode()
    near = [_episode(line=14.0, predicted_probability=0.59) for _ in range(4)]
    far = [_episode(line=31.5, predicted_probability=0.95, model_disagreement=0.4)]

    found = find_precedents(subject, near + far)

    assert found.count == 4
    assert not found.is_informative  # four precedents is anecdote, and it says so
    assert found.count < MINIMUM_PRECEDENTS
    assert all(precedent.distance < 0.5 for precedent in found.precedents)


def test_the_subject_is_never_its_own_precedent() -> None:
    subject = _episode()

    found = find_precedents(subject, [subject, _episode(line=14.0)])

    assert all(str(p.episode_id) != str(subject["episode_id"]) for p in found.precedents)


def test_precedent_calibration_gap_reports_overconfidence_in_the_reference_class() -> None:
    subject = _episode()
    stated_high_landed_low = [
        _episode(predicted_probability=0.70, hit=index % 2 == 0) for index in range(20)
    ]

    found = find_precedents(subject, stated_high_landed_low)

    assert found.calibration_gap is not None
    assert found.calibration_gap == pytest.approx(0.20, abs=0.01)


# --------------------------------------------------------------------------------------
# The synthesizer
# --------------------------------------------------------------------------------------
def _position(role: str, confidence: float, **overrides: object) -> AgentPosition:
    return AgentPosition(
        role=role,
        conclusion=f"{role} conclusion",
        confidence=confidence,
        round_number=int(str(overrides.get("round_number", 2))),
        credibility=float(str(overrides.get("credibility", 0.5))),
        concessions=int(str(overrides.get("concessions", 1))),
        unresolved=int(str(overrides.get("unresolved", 0))),
    )


def _synthesis_inputs(**overrides: object) -> SynthesisInputs:
    base: dict[str, object] = {
        "episode_id": uuid4(),
        "model_probability": 0.61,
        "side": "over",
        "prop_type": "points",
        "positions": (_position("availability", 0.7), _position("rotation", 0.68)),
        "supporting_claim_ids": (uuid4(), uuid4()),
        "contradicting_claim_ids": (),
        "policy_blocked": False,
        "policy_reasons": (),
        "quote_age_seconds": 300.0,
    }
    base.update(overrides)
    return SynthesisInputs(**base)  # type: ignore[arg-type]


def test_a_clean_investigation_reaches_supported() -> None:
    result = synthesize(_synthesis_inputs())

    assert result.posture == "supported"
    assert result.concerns == ()


def test_a_policy_block_outranks_everything_else() -> None:
    result = synthesize(
        _synthesis_inputs(
            policy_blocked=True,
            policy_reasons=("block_on_thin_evidence (block): missing features",),
        )
    )

    assert result.posture == "policy_blocked"


def test_contradicting_claims_outrank_thin_support() -> None:
    result = synthesize(
        _synthesis_inputs(supporting_claim_ids=(), contradicting_claim_ids=(uuid4(),))
    )

    assert result.posture == "contested"


def test_too_few_supporting_claims_is_insufficient_evidence_not_support() -> None:
    result = synthesize(_synthesis_inputs(supporting_claim_ids=(uuid4(),)))

    assert result.posture == "insufficient_evidence"


def test_a_panel_that_never_concedes_is_flagged() -> None:
    """Recorded disagreement with zero concessions means the second round did no work."""
    result = synthesize(
        _synthesis_inputs(
            positions=(
                _position("availability", 0.7, concessions=0, unresolved=2),
                _position("rotation", 0.68, concessions=0, unresolved=1),
            )
        )
    )

    assert result.posture == "qualified"
    assert "unyielding_panel" in {concern["kind"] for concern in result.concerns}


def test_credibility_weights_confidence_without_touching_the_model_probability() -> None:
    inputs = _synthesis_inputs(
        positions=(
            _position("availability", 0.9, credibility=0.9),
            _position("rotation", 0.3, credibility=0.1),
        )
    )

    result = synthesize(inputs)

    assert result.weighted_confidence is not None
    # Pulled toward the credible analyst, and nowhere near the unweighted mean of 0.6.
    assert result.weighted_confidence > 0.75
    # And the model's number is untouched by any of it.
    assert inputs.model_probability == 0.61
    assert result.to_payload()["writes_probability"] is False


def test_the_narrative_never_restates_a_probability() -> None:
    result = synthesize(_synthesis_inputs(model_probability=0.61))

    assert "0.61" not in result.narrative
    assert "61%" not in result.narrative


# --------------------------------------------------------------------------------------
# The boundary: agents describe, they do not forecast
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("schema", [AgentAnalysis, AnalysisRevision, RuleProposalDraft])
def test_no_agent_schema_has_anywhere_to_put_a_prop_probability(schema: type[BaseModel]) -> None:
    """The structural version of "DeepSeek cannot alter probabilities".

    Not a prompt instruction and not a review-time check: the schemas forbid unknown fields, so a
    model returning ``probability_over`` fails to parse. There is no path from an agent's output
    to a forecast because there is no field that could carry one.
    """
    fields = set(schema.model_fields)

    assert not fields & {
        "probability_over",
        "probability_under",
        "probability",
        "projected_mean",
        "stake",
        "recommendation",
    }


def test_an_agent_returning_a_probability_field_is_rejected_outright() -> None:
    with pytest.raises(ValidationError):
        AgentAnalysis.model_validate(
            {
                "conclusion": "She plays.",
                "confidence": 0.8,
                "claims": [],
                "risk_flags": [],
                "probability_over": 0.63,
            }
        )


def test_a_revision_cannot_smuggle_a_forecast_through_its_confidence_field() -> None:
    """``revised_confidence`` is confidence in the analyst's own view, and is range-bound."""
    revision = AnalysisRevision.model_validate(
        {
            "conclusion": "Still expect a full workload.",
            "revised_confidence": 0.55,
            "revision_reason": "The market analyst's staleness point is fair.",
            "concessions": ["quote is older than I treated it as"],
            "unresolved_disagreements": [],
            "claims": [],
            "risk_flags": [],
        }
    )

    assert 0.0 <= revision.revised_confidence <= 1.0
    assert not hasattr(revision, "probability_over")


# --------------------------------------------------------------------------------------
# Claim refresh scheduling
# --------------------------------------------------------------------------------------
def test_every_role_has_a_domain_and_every_domain_has_a_refresh_interval() -> None:
    """A role with no refresh interval would produce claims nobody ever re-checks."""
    for role, domain in ROLE_DOMAIN.items():
        assert domain in CLAIM_REFRESH, f"{role} has no refresh interval"


def test_availability_claims_go_stale_faster_than_matchup_claims() -> None:
    """Availability changes latest and matters most; pace does not move after lunch."""
    assert CLAIM_REFRESH["availability"] < CLAIM_REFRESH["matchup"]
    assert CLAIM_REFRESH["market"] < CLAIM_REFRESH["rotation"]


# --------------------------------------------------------------------------------------
# Credibility scoring
# --------------------------------------------------------------------------------------
def test_no_evidence_returns_exactly_the_prior() -> None:
    assert pooled_score(0, 0) == CREDIBILITY_PRIOR


def test_a_lucky_start_cannot_reach_certainty() -> None:
    """Two right answers in week one must not produce a dominant analyst."""
    assert pooled_score(2, 2) < 0.6


def test_pooling_yields_to_volume() -> None:
    assert pooled_score(200, 200) > 0.9


def test_a_perfectly_calibrated_analyst_has_no_gap() -> None:
    pairs = [(0.5, index % 2 == 0) for index in range(100)]

    gap = expected_calibration(pairs)

    assert gap is not None
    assert gap < 0.01


def test_confident_and_wrong_produces_a_large_gap() -> None:
    gap = expected_calibration([(0.95, False)] * 40)

    assert gap is not None
    assert gap > 0.9


# --------------------------------------------------------------------------------------
# The evidence feedback loop actually closes
# --------------------------------------------------------------------------------------
def test_evidence_is_ordered_by_learned_usefulness() -> None:
    """Ordering is the only thing analyst labels change, and it has to change something."""
    projection = {
        "features": {"injury_designation": "available", "expected_minutes": 30.0},
        "source": "underdog",
        "line": 14.5,
        "observed_at": NOW,
        "locks_at": LOCKS,
        "prop_type": "points",
        "mean": 15.0,
        "median": 15,
        "stddev": 6.0,
        "model_disagreement": 0.04,
        "data_quality_score": 0.9,
    }
    ranking = {"matchup": 0.9, "availability": 0.1, "market": 0.5}

    _, evidence = _snapshot_evidence(projection, audit_context=[], precedents=None, ranking=ranking)
    kinds = [kind for kind, _ in evidence.values()]

    assert kinds.index("matchup") < kinds.index("market") < kinds.index("availability")


def test_a_disliked_evidence_kind_is_demoted_but_never_withheld() -> None:
    """A feedback loop that could hide evidence would quietly narrow what analysts can see."""
    projection = {
        "features": {},
        "source": "underdog",
        "line": 14.5,
        "observed_at": NOW,
        "locks_at": LOCKS,
        "prop_type": "points",
        "mean": 15.0,
        "median": 15,
        "stddev": 6.0,
        "model_disagreement": 0.04,
        "data_quality_score": 0.9,
    }

    _, evidence = _snapshot_evidence(
        projection, audit_context=[], precedents=None, ranking={"matchup": 0.0}
    )
    kinds = [kind for kind, _ in evidence.values()]

    assert "matchup" in kinds
    assert kinds[-1] == "matchup"


def test_evidence_kinds_recur_across_props_so_feedback_can_accumulate() -> None:
    """The defect the kind ranking exists to fix: evidence ids are content-hashed per prop.

    Two different props produce disjoint evidence *ids* and identical evidence *kinds*. Ranking
    by id could therefore never accumulate a sample; ranking by kind can.
    """
    base = {
        "features": {},
        "source": "underdog",
        "observed_at": NOW,
        "locks_at": LOCKS,
        "prop_type": "points",
        "mean": 15.0,
        "median": 15,
        "stddev": 6.0,
        "model_disagreement": 0.04,
        "data_quality_score": 0.9,
    }
    _, first = _snapshot_evidence(
        {**base, "line": 14.5}, audit_context=[], precedents=None, ranking={}
    )
    _, second = _snapshot_evidence(
        {**base, "line": 22.5}, audit_context=[], precedents=None, ranking={}
    )

    assert set(first).isdisjoint(second)
    assert {kind for kind, _ in first.values()} == {kind for kind, _ in second.values()}


def test_source_reliability_never_flatters_a_thin_snapshot() -> None:
    """Reliability is the weaker of snapshot completeness and the source's track record."""

    class _Cursor:
        def __init__(self, row: dict[str, object] | None) -> None:
            self.row = row

        def execute(self, query: str, params: object = ()) -> None:
            return None

        def fetchone(self) -> dict[str, object] | None:
            return self.row

    untracked = source_reliability_for(_Cursor(None), "derived", default=0.9)
    contradicted = source_reliability_for(
        _Cursor({"reliability": 0.3, "sample_size": 40}), "derived", default=0.9
    )
    unmeasured = source_reliability_for(
        _Cursor({"reliability": 0.99, "sample_size": 0}), "derived", default=0.6
    )

    assert untracked == 0.9
    assert contradicted == 0.3
    assert unmeasured == 0.6
