"""Tests that enforce the platform invariants at the type level.

These are not unit tests of convenience. Each one guards a specific way this kind of system
fools itself, and each is expected to fail the build rather than emit a warning nobody reads.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, get_args, get_origin
from uuid import uuid4

import pytest
import wnba_domain
from pydantic import BaseModel, ValidationError
from wnba_domain import (
    ActionType,
    AgentRole,
    ClaimStatus,
    DecisionEpisode,
    FeedbackType,
    InjuryDesignation,
    InjuryStatusRecord,
    ModelPromotion,
    ModelStage,
    OntologyAction,
    PlayerGameLine,
    ProjectedRole,
    PropType,
    RecommendationStatus,
    ResearchClaim,
    Side,
    SourceName,
    SourceRef,
    TemporalError,
)

T0 = datetime(2026, 8, 2, 14, 30, tzinfo=UTC)


def _all_domain_models() -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []
    for name in wnba_domain.__all__:
        obj = getattr(wnba_domain, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            models.append(obj)
    return models


def _field_is_bool(annotation: Any) -> bool:
    if annotation is bool:
        return True
    # Unwrap Optional[bool] / bool | None
    return get_origin(annotation) is not None and bool in get_args(annotation)


# --------------------------------------------------------------------------------------
# Invariant 3: starter status is a probability, never a boolean
# --------------------------------------------------------------------------------------
#
# The WNBA does not require pre-tip lineup submission, so "confirmed starter" is not a fact
# that exists to be ingested. The only legal booleans are *observed outcomes*, recorded after
# the game, which may never be read as an input for the game they describe.
_OUTCOME_ONLY_BOOLEANS = {
    ("PlayerGameLine", "started"),
    ("EpisodeOutcome", "did_start"),
    ("EpisodeOutcome", "did_play"),
}

# Scoped to starter/lineup vocabulary specifically. Deliberately excludes "available", which
# legitimately describes a *market* being offered (`PropQuote.is_available`) rather than a
# player being able to play -- player availability is `availability_probability`, a float.
_STARTER_PATTERN = re.compile(r"start|lineup", re.IGNORECASE)


def test_no_boolean_starter_fields_outside_observed_outcomes() -> None:
    offenders: list[str] = []
    for model in _all_domain_models():
        for field_name, field in model.model_fields.items():
            if not _STARTER_PATTERN.search(field_name):
                continue
            if not _field_is_bool(field.annotation):
                continue
            if (model.__name__, field_name) in _OUTCOME_ONLY_BOOLEANS:
                continue
            offenders.append(f"{model.__name__}.{field_name}")

    assert not offenders, (
        "Starter/availability status must be modelled as a probability, never a boolean. "
        f"Offending fields: {offenders}. If one of these is a genuine post-game observation, "
        "add it to _OUTCOME_ONLY_BOOLEANS with a justification."
    )


def test_projected_role_carries_probabilities() -> None:
    role = ProjectedRole(
        player_id=uuid4(),
        game_id=uuid4(),
        availability_probability=0.9,
        start_probability=0.75,
        closing_lineup_probability=0.6,
        expected_minutes=28.5,
        minutes_std=5.0,
        valid_from=T0,
        system_from=T0,
    )
    assert 0.0 <= role.start_probability <= 1.0
    assert not hasattr(role, "is_starter")


def test_unavailable_player_cannot_have_projected_minutes() -> None:
    with pytest.raises(ValidationError, match="expected_minutes must be 0"):
        ProjectedRole(
            player_id=uuid4(),
            game_id=uuid4(),
            availability_probability=0.0,
            start_probability=0.0,
            closing_lineup_probability=0.0,
            expected_minutes=25.0,
            minutes_std=4.0,
            valid_from=T0,
            system_from=T0,
        )


# --------------------------------------------------------------------------------------
# Invariant 1: bitemporality
# --------------------------------------------------------------------------------------
def test_every_bitemporal_record_requires_both_time_axes() -> None:
    for model in _all_domain_models():
        if not issubclass(model, wnba_domain.BitemporalRecord):
            continue
        fields = set(model.model_fields)
        missing = {"valid_from", "valid_to", "system_from", "system_to"} - fields
        assert not missing, f"{model.__name__} is missing temporal fields: {missing}"


def test_inverted_temporal_envelope_is_rejected() -> None:
    ref = SourceRef(source=SourceName.ESPN, source_entity_id="x", retrieved_at=T0)
    with pytest.raises(ValidationError) as exc:
        InjuryStatusRecord(
            player_id=uuid4(),
            designation=InjuryDesignation.OUT,
            source_ref=ref,
            valid_from=T0,
            valid_to=T0 - timedelta(hours=1),
            system_from=T0,
        )
    assert isinstance(exc.value.errors()[0]["ctx"]["error"], TemporalError)


# --------------------------------------------------------------------------------------
# Invariant 4: strictness
# --------------------------------------------------------------------------------------
def test_extra_fields_are_forbidden() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceRef(
            source=SourceName.ESPN,
            source_entity_id="x",
            retrieved_at=T0,
            plausible_looking_typo="oops",  # type: ignore[call-arg]
        )


def test_naive_datetimes_are_rejected() -> None:
    """A naive timestamp in a bitemporal system is an unexploded bug."""
    with pytest.raises(ValidationError):
        SourceRef(
            source=SourceName.ESPN,
            source_entity_id="x",
            retrieved_at=datetime(2026, 8, 2, 14, 30),  # noqa: DTZ001
        )


def test_audit_records_are_immutable() -> None:
    ref = SourceRef(source=SourceName.ESPN, source_entity_id="x", retrieved_at=T0)
    with pytest.raises(ValidationError, match="frozen"):
        ref.source_entity_id = "rewritten"  # type: ignore[misc]


def test_box_score_must_be_internally_consistent() -> None:
    def line(**overrides: object) -> PlayerGameLine:
        base: dict[str, object] = {
            "player_id": uuid4(),
            "game_id": uuid4(),
            "team_id": uuid4(),
            "minutes": 30.0,
            "started": True,
            "points": 13,
            "rebounds_offensive": 1,
            "rebounds_defensive": 4,
            "assists": 3,
            "steals": 1,
            "blocks": 0,
            "turnovers": 2,
            "personal_fouls": 3,
            "field_goals_made": 5,
            "field_goals_attempted": 11,
            "three_pointers_made": 1,
            "three_pointers_attempted": 4,
            "free_throws_made": 2,
            "free_throws_attempted": 2,
        }
        base.update(overrides)
        return PlayerGameLine(**base)  # type: ignore[arg-type]

    good = line()
    assert good.rebounds == 5
    assert good.points_rebounds_assists == 21

    with pytest.raises(ValidationError, match="disagree with shot breakdown"):
        line(points=99)
    with pytest.raises(ValidationError, match=r"made .* exceeds attempted"):
        line(free_throws_made=5, points=16)
    with pytest.raises(ValidationError, match="three pointers made cannot exceed"):
        line(three_pointers_made=6, three_pointers_attempted=6, field_goals_made=5)


# --------------------------------------------------------------------------------------
# Invariant 6: asymmetric autonomy
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "action_type",
    [ActionType.PROMOTE_MODEL, ActionType.APPROVE_CANDIDATE, ActionType.OVERRIDE_MINUTES],
)
def test_automation_cannot_expand_exposure(action_type: ActionType) -> None:
    with pytest.raises(ValidationError, match="cannot be taken automatically"):
        OntologyAction(
            action_id=uuid4(),
            action_type=action_type,
            actor="scheduler",
            occurred_at=T0,
            subject_id=uuid4(),
            previous_state="challenger",
            new_state="champion",
            reason="backtest looked wonderful",
            is_automated=True,
        )


@pytest.mark.parametrize(
    "action_type",
    [ActionType.DISABLE_SOURCE, ActionType.FREEZE_GAME, ActionType.MARK_SOURCE_STALE],
)
def test_automation_may_restrict_freely(action_type: ActionType) -> None:
    action = OntologyAction(
        action_id=uuid4(),
        action_type=action_type,
        actor="drift-monitor",
        occurred_at=T0,
        subject_id=uuid4(),
        previous_state="enabled",
        new_state="disabled",
        reason="calibration drift beyond threshold",
        is_automated=True,
    )
    assert action.is_automated


def test_promotion_to_champion_requires_an_experiment() -> None:
    with pytest.raises(ValidationError, match="requires the experiment"):
        ModelPromotion(
            promotion_id=uuid4(),
            model_version_id=uuid4(),
            from_stage=ModelStage.CHALLENGER,
            to_stage=ModelStage.CHAMPION,
            approved_by="analyst",
            occurred_at=T0,
            reason="felt right",
        )


# --------------------------------------------------------------------------------------
# Research: uncited claims are not claims
# --------------------------------------------------------------------------------------
def test_claim_without_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ResearchClaim(
            claim_id=uuid4(),
            subject_id=uuid4(),
            predicate="expected_minutes",
            value="32",
            confidence=0.8,
            evidence_ids=(),
            valid_from=T0,
            expires_at=T0 + timedelta(hours=6),
            generated_by=AgentRole.ROTATION,
        )


def test_evidence_cannot_both_support_and_contradict() -> None:
    shared = uuid4()
    with pytest.raises(ValidationError, match="both support and contradict"):
        ResearchClaim(
            claim_id=uuid4(),
            subject_id=uuid4(),
            predicate="expected_minutes",
            value="32",
            confidence=0.8,
            evidence_ids=(shared,),
            contradicting_evidence_ids=(shared,),
            valid_from=T0,
            expires_at=T0 + timedelta(hours=6),
            generated_by=AgentRole.ROTATION,
            status=ClaimStatus.PROPOSED,
        )


def test_claims_expire() -> None:
    claim = ResearchClaim(
        claim_id=uuid4(),
        subject_id=uuid4(),
        predicate="injury_designation",
        value="questionable",
        confidence=0.7,
        evidence_ids=(uuid4(),),
        valid_from=T0,
        expires_at=T0 + timedelta(hours=3),
        generated_by=AgentRole.AVAILABILITY,
    )
    assert claim.is_live_at(T0 + timedelta(hours=1))
    assert not claim.is_live_at(T0 + timedelta(hours=4))


# --------------------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------------------
def test_analyst_override_must_state_a_reason() -> None:
    kwargs: dict[str, object] = {
        "episode_id": uuid4(),
        "forecast_timestamp": T0,
        "player_id": uuid4(),
        "game_id": uuid4(),
        "prop_type": PropType.POINTS,
        "side": Side.OVER,
        "line": Decimal("21.5"),
        "source": SourceName.PRIZEPICKS,
        "quote_id": uuid4(),
        "projected_mean": 23.4,
        "projected_median": 23.0,
        "predicted_probability": 0.59,
        "projected_minutes": 31.0,
        "confidence": 0.71,
        "data_quality_score": 0.94,
        "model_disagreement": 0.04,
        "model_version_ids": (uuid4(),),
        "model_run_id": uuid4(),
        "feature_snapshot_id": uuid4(),
        "system_recommendation": RecommendationStatus.RECOMMENDED,
        "analyst_decision": FeedbackType.OVERRIDE_LOWER,
    }
    with pytest.raises(ValidationError, match="must record its reason"):
        DecisionEpisode(**kwargs)  # type: ignore[arg-type]

    kwargs["analyst_override_reason"] = "beat reporter says minutes restriction still in effect"
    episode = DecisionEpisode(**kwargs)  # type: ignore[arg-type]
    assert episode.is_paper, "episodes must default to paper until the ten criteria are met"
