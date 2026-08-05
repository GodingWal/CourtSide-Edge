"""Scoring the analyst, and the two rules that make the score mean anything.

The interesting assertions here are the ones about what is *excluded*: labels written after the
outcome, and overrides on decisions the system had already declined. Both are cases where a naive
implementation produces a flattering number from evidence that does not support one.
"""

from __future__ import annotations

import pytest
from wnba_services.learning_loop.analyst_expertise import (
    FEEDBACK_DOMAINS,
    MINIMUM_DOMAIN_LABELS,
    WEAKEST_ASSUMPTION_DOMAINS,
    domain_for,
    override_verdict,
)
from wnba_services.research_agents.credibility import CREDIBILITY_PRIOR, pooled_score


# --------------------------------------------------------------------------------------
# Override scoring
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("decision", "recommendation", "hit", "expected"),
    [
        # The system recommended it and it landed: agreeing was right, overriding was not.
        ("accepted", "candidate", True, "agreed_and_right"),
        ("override_lower", "candidate", True, "override_hurt"),
        # The system recommended it and it missed: the override earned its keep.
        ("override_lower", "candidate", False, "override_helped"),
        ("accepted", "candidate", False, "agreed_and_wrong"),
        # The system declined and it missed: declining was right.
        ("accepted", "declined", False, "agreed_and_right"),
    ],
)
def test_override_verdicts_score_both_directions(
    decision: str, recommendation: str, hit: bool, expected: str
) -> None:
    verdict, _, _ = override_verdict(
        analyst_decision=decision, system_recommendation=recommendation, hit=hit
    )

    assert verdict == expected


@pytest.mark.parametrize("hit", [True, False])
def test_an_override_on_an_already_declined_decision_is_neutral(hit: bool) -> None:
    """Both parties said no in effect, so the outcome cannot separate them.

    Scoring it either way would measure agreement with a non-event -- and since most of the board
    is declined, counting these is the easiest way to manufacture an impressive override record.
    """
    verdict, _, _ = override_verdict(
        analyst_decision="override_lower", system_recommendation="declined", hit=hit
    )

    assert verdict == "override_neutral"


def test_being_right_means_the_action_would_have_been_correct() -> None:
    _, system_right, analyst_right = override_verdict(
        analyst_decision="override_lower", system_recommendation="candidate", hit=False
    )

    assert system_right is False
    assert analyst_right is True


# --------------------------------------------------------------------------------------
# Domain assignment
# --------------------------------------------------------------------------------------
def test_a_stated_assumption_outranks_the_rejection_reason() -> None:
    """An analyst who names the assumption has told you more than the button they clicked."""
    assert domain_for("rejected_uncertainty", "minutes") == "rotation"
    assert domain_for("rejected_uncertainty", None) == "general"


def test_every_feedback_type_and_assumption_kind_maps_to_a_domain() -> None:
    """An unmapped label would silently vanish from the expertise calculation."""
    assert all(value for value in FEEDBACK_DOMAINS.values())
    assert all(value for value in WEAKEST_ASSUMPTION_DOMAINS.values())


# --------------------------------------------------------------------------------------
# Pooling, shared with agent credibility on purpose
# --------------------------------------------------------------------------------------
def test_an_analyst_with_no_labels_sits_at_the_prior() -> None:
    assert pooled_score(0, 0) == CREDIBILITY_PRIOR


def test_three_good_calls_do_not_make_an_expert() -> None:
    """A human's three opinions are not more evidence than a model's three."""
    assert pooled_score(3, 3) < 0.6


def test_the_provisional_gate_is_a_real_number_of_labels() -> None:
    assert MINIMUM_DOMAIN_LABELS >= 10


# --------------------------------------------------------------------------------------
# The capture path: the loop's input has to be writable
# --------------------------------------------------------------------------------------
def test_the_feedback_model_can_carry_evidence_labels() -> None:
    """The defect this work started from.

    ``evidence_ids_useful`` and ``evidence_ids_misleading`` were columns the retrieval ranking
    read, on a request model that had no such fields and an INSERT that omitted the columns. The
    loop's input was not sparse, it was unreachable, and the ranking looked like it worked.
    """
    from wnba_apps.api.main import FeedbackRequest

    fields = set(FeedbackRequest.model_fields)

    assert {"evidence_ids_useful", "evidence_ids_misleading"} <= fields
    assert {"weakest_assumption", "weakest_assumption_kind", "missing_context"} <= fields
    assert "would_repeat" in fields


def test_the_insert_writes_every_column_the_ranking_reads() -> None:
    """A field on the model that never reaches the INSERT is the same bug one layer down."""
    import inspect

    from wnba_apps.api import main

    source = inspect.getsource(main.submit_feedback)

    for column in (
        "evidence_ids_useful",
        "evidence_ids_misleading",
        "weakest_assumption_kind",
        "labelled_after_outcome",
        "domain",
    ):
        assert column in source, f"{column} is never written by submit_feedback"


def test_evidence_cannot_be_marked_useful_and_misleading_at_once() -> None:
    from uuid import uuid4

    from pydantic import ValidationError
    from wnba_apps.api.main import FeedbackRequest

    shared = uuid4()
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {
                "feedback_type": "accepted",
                "projection_useful": 1.0,
                "evidence_relevant": 0.8,
                "confidence_appropriate": 0.8,
                "would_repeat": True,
                "evidence_ids_useful": [shared],
                "evidence_ids_misleading": [shared],
            }
        )
