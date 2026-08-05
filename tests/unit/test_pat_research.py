from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from wnba_rules.dsl import RuleParseError
from wnba_services.research_agents.auditor import audit_projection
from wnba_services.research_agents.coordinator import build_plan, trigger_codes
from wnba_services.research_agents.deepseek import AgentForecastDraft, RuleProposalDraft
from wnba_services.research_agents.organization import synthesize, validate_rule_proposal

NOW = datetime(2026, 8, 4, 20, tzinfo=UTC)


def projection(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "observed_at": NOW - timedelta(minutes=10),
        "locks_at": NOW + timedelta(hours=2),
        "model_disagreement": 0.04,
        "data_quality_score": 0.95,
        "features": {
            "expected_minutes": 31.0,
            "history_games": 12,
            "expected_possessions": 79.0,
            "injury_designation": "available",
            "start_probability": 0.9,
            "teammate_effect_count": 0,
        },
    }
    value.update(overrides)
    return value


def test_coordinator_triggers_only_material_conditions() -> None:
    assert trigger_codes(projection(), now=NOW) == ()
    changed = projection(
        model_disagreement=0.14,
        observed_at=NOW - timedelta(hours=2),
        features={
            "expected_minutes": 30,
            "history_games": 10,
            "expected_possessions": 78,
            "injury_designation": "questionable",
            "start_probability": 0.5,
            "teammate_effect_count": 1,
        },
    )
    assert set(trigger_codes(changed, now=NOW)) == {
        "MATERIAL_INJURY_STATUS",
        "HIGH_MODEL_DISAGREEMENT",
        "STALE_MARKET_QUOTE",
        "UNRESOLVED_STARTER",
        "TEAMMATE_ROLE_CHANGE",
    }
    assert set(build_plan(changed, now=NOW).questions) == {
        "availability",
        "rotation",
        "matchup",
        "market",
        "skeptic",
    }


def test_data_auditor_blocks_stale_or_incomplete_evidence() -> None:
    assert audit_projection(projection(), now=NOW).status == "pass"
    broken = projection(
        observed_at=NOW - timedelta(hours=3),
        data_quality_score=0.4,
        features={},
    )
    report = audit_projection(broken, now=NOW)
    assert report.blocked
    assert {issue.code for issue in report.issues} >= {
        "STALE_QUOTE",
        "MISSING_FEATURE",
        "LOW_DATA_QUALITY",
    }


def forecast(probability: float, *flags: str) -> AgentForecastDraft:
    return AgentForecastDraft(
        advisory_probability=probability,
        rationale="Evidence-grounded advisory assessment for independent review.",
        evidence_ids=[uuid4()],
        risk_flags=list(flags),
    )


def test_synthesizer_never_rewrites_model_probability() -> None:
    result = synthesize(
        model_probability=0.71,
        round_two=[forecast(0.42, "role uncertain"), forecast(0.67)],
        audit_blocked=False,
        data_quality_score=0.9,
    )
    assert result.model_probability == 0.71
    assert result.advisory_probability == pytest.approx(0.545)
    assert result.disposition == "caution"


def proposal(**overrides: object) -> RuleProposalDraft:
    payload: dict[str, object] = {
        "rule_id": "deepseek_minutes_uncertainty",
        "title": "Shrink unresolved minutes",
        "rationale": "Repeated minutes errors justify a conservative confidence reduction.",
        "mechanism": (
            "Minutes multiply every rate and unresolved roles create a bimodal distribution."
        ),
        "confounders": ["late injury changes", "blowouts"],
        "conditions": [{"field": "minutes_std", "operator": "gte", "value": 7.0}],
        "combinator": "all",
        "action": {"kind": "shrink_toward_even", "magnitude": 0.25},
        "evidence_ids": [uuid4()],
        "withdrawal_criteria": (
            "Withdraw when held-out calibration no longer improves for two windows."
        ),
    }
    payload.update(overrides)
    return RuleProposalDraft.model_validate(payload)


def test_deepseek_rule_is_reparsed_through_closed_non_executable_dsl() -> None:
    rule = validate_rule_proposal(proposal())
    assert rule.status.value == "proposed"
    assert rule.proposed_by == "deepseek"
    assert rule.action.apply(0.8) < 0.8

    with pytest.raises((ValueError, RuleParseError), match="unknown rule field"):
        validate_rule_proposal(
            proposal(conditions=[{"field": "python_code", "operator": "eq", "value": 1}])
        )
    with pytest.raises(ValueError):
        validate_rule_proposal(proposal(action={"kind": "increase_probability"}))
