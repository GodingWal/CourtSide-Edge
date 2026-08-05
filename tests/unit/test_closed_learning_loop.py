"""The parts of the closed loop that decide things, tested without a database.

Every function under test here is pure by design, because each one is a place the system either
changes what a forecast sees or retires a claim it made. Those thresholds should be checkable
without standing up Postgres, and a change to one should have to be argued for in a diff.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel
from wnba_services.forecasting.deescalation import (
    BLOCKING_SHRINK,
    WARNING_SHRINK,
    DriftGuard,
    shrink_toward_even,
)
from wnba_services.learning_loop.evaluation import classify_error
from wnba_services.learning_loop.hypothesis_review import (
    MINIMUM_REVIEW_MARKETS,
    verdict_for,
)
from wnba_services.learning_loop.rule_review import MINIMUM_LIVE_MARKETS, judge_live_rule
from wnba_services.research_agents.advisor import Advisor

# ── error attribution ────────────────────────────────────────────────────────


def test_minutes_error_no_longer_swallows_an_efficiency_miss() -> None:
    """The old classifier returned minutes and stopped. Both causes are real here.

    Thirty projected minutes at 0.5 points per minute; the player lost eight minutes *and* shot
    badly in the ones they played. Attributing this to minutes alone is what made every week's
    proposals minutes proposals.
    """
    primary, avoidable, _, secondary = classify_error(
        projected_minutes=30.0,
        actual_minutes=22.0,
        projected_stat=15.0,
        actual_stat=4.0,
        line_value=None,
    )
    assert (primary, avoidable) == ("minutes_projection", True)
    assert secondary == "rate_or_efficiency"


def test_stat_error_fully_explained_by_minutes_is_not_an_efficiency_error() -> None:
    """Minutes are multiplicative, so a proportional stat miss is the same single cause."""
    primary, _, _, secondary = classify_error(
        projected_minutes=30.0,
        actual_minutes=20.0,
        projected_stat=15.0,
        # Exactly the projected per-minute rate over the minutes actually played.
        actual_stat=10.0,
        line_value=None,
    )
    assert primary == "minutes_projection"
    assert secondary is None


def test_confident_and_wrong_with_clean_inputs_is_a_modelling_failure() -> None:
    """This is the case that used to be filed as luck and generate nothing."""
    primary, avoidable, _, _ = classify_error(
        projected_minutes=30.0,
        actual_minutes=30.0,
        projected_stat=15.0,
        actual_stat=16.0,
        line_value=None,
        predicted_probability=0.88,
        hit=False,
    )
    assert (primary, avoidable) == ("modeling", True)


def test_a_forecast_that_was_never_sharp_stays_random_variance() -> None:
    primary, avoidable, _, _ = classify_error(
        projected_minutes=30.0,
        actual_minutes=30.0,
        projected_stat=15.0,
        actual_stat=16.0,
        line_value=None,
        predicted_probability=0.55,
        hit=False,
    )
    assert (primary, avoidable) == ("random_variance", False)


def test_distrusted_inputs_are_named_as_such() -> None:
    primary, avoidable, _, _ = classify_error(
        projected_minutes=30.0,
        actual_minutes=30.0,
        projected_stat=15.0,
        actual_stat=16.0,
        line_value=None,
        data_quality_score=0.55,
    )
    assert (primary, avoidable) == ("data_quality", True)


# ── hypothesis review ────────────────────────────────────────────────────────


def test_a_hypothesis_is_not_judged_on_thin_evidence() -> None:
    status, confidence = verdict_for(supporting=9, contradicting=0, independent_markets=4.0)
    assert status == "inconclusive"
    assert confidence == 0.5


def test_a_mechanism_that_stopped_explaining_failures_is_refuted() -> None:
    status, _ = verdict_for(
        supporting=5, contradicting=45, independent_markets=MINIMUM_REVIEW_MARKETS + 5
    )
    assert status == "refuted"


def test_a_mechanism_still_explaining_failures_is_supported() -> None:
    status, confidence = verdict_for(
        supporting=40, contradicting=10, independent_markets=MINIMUM_REVIEW_MARKETS + 5
    )
    assert status == "supported"
    assert confidence > 0.5


def test_confidence_is_shrunk_toward_even_by_thin_evidence() -> None:
    """Same share, less independent evidence, weaker claim."""
    _, thin = verdict_for(supporting=18, contradicting=2, independent_markets=20.0)
    _, thick = verdict_for(supporting=180, contradicting=20, independent_markets=400.0)
    assert thin < thick


# ── live rule review ─────────────────────────────────────────────────────────


def test_a_rule_is_not_withdrawn_on_one_bad_week() -> None:
    verdict, _ = judge_live_rule(
        action_kind="shrink_toward_even",
        independent_markets=MINIMUM_LIVE_MARKETS - 1,
        log_loss_before=0.60,
        log_loss_after=0.90,
        blocked_hit_rate=None,
    )
    assert verdict == "inconclusive"


def test_a_shrink_rule_that_makes_forecasts_worse_is_harmful() -> None:
    verdict, detail = judge_live_rule(
        action_kind="shrink_toward_even",
        independent_markets=MINIMUM_LIVE_MARKETS + 10,
        log_loss_before=0.60,
        log_loss_after=0.66,
        blocked_hit_rate=None,
    )
    assert verdict == "harmful"
    assert "worse" in detail


def test_a_block_rule_declining_winners_is_harmful() -> None:
    verdict, _ = judge_live_rule(
        action_kind="block",
        independent_markets=MINIMUM_LIVE_MARKETS + 10,
        log_loss_before=None,
        log_loss_after=None,
        blocked_hit_rate=0.62,
    )
    assert verdict == "harmful"


def test_a_procedural_rule_is_never_judged_by_log_loss() -> None:
    verdict, detail = judge_live_rule(
        action_kind="require_evidence",
        independent_markets=MINIMUM_LIVE_MARKETS + 10,
        log_loss_before=0.60,
        log_loss_after=0.90,
        blocked_hit_rate=None,
    )
    assert verdict == "inconclusive"
    assert "procedural" in detail


# ── drift de-escalation ──────────────────────────────────────────────────────


@pytest.mark.parametrize("probability", [0.02, 0.31, 0.5, 0.74, 0.99])
@pytest.mark.parametrize("magnitude", [WARNING_SHRINK, BLOCKING_SHRINK])
def test_de_escalation_can_only_move_a_forecast_toward_even(
    probability: float, magnitude: float
) -> None:
    """The invariant the database also enforces, checked at the source."""
    after = shrink_toward_even(probability, magnitude)
    assert abs(after - 0.5) <= abs(probability - 0.5) + 1e-9


def test_a_blocking_incident_widens_harder_than_a_warning() -> None:
    warning = DriftGuard(None, "reduce_confidence", WARNING_SHRINK, "warning", "ensemble", 0.09)
    blocking = DriftGuard(
        None, "require_manual_review", BLOCKING_SHRINK, "blocking", "ensemble", 0.14
    )
    assert blocking.magnitude > warning.magnitude


def test_no_open_incident_leaves_a_forecast_untouched() -> None:
    inert = DriftGuard(None, None, 0.0, None, None, None)
    assert inert.active is False
    assert inert.apply(0.83) == 0.83


# ── the advisory layer fails closed ──────────────────────────────────────────


class _Trivial(BaseModel):
    value: int = 0


class _RecordingCursor:
    """Enough cursor to catch the audit write."""

    def __init__(self) -> None:
        self.rows: list[tuple[Any, ...]] = []

    def execute(self, query: str, params: tuple[Any, ...] = (), /) -> None:
        self.rows.append(params)


def test_a_missing_key_is_a_recorded_fallback_not_an_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A learning job must complete when the provider is unavailable.

    The whole design rests on this: every caller treats ``None`` as "run the deterministic path",
    so an outage degrades the loop's richness and never its completion.
    """
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    cursor = _RecordingCursor()
    advisor = Advisor(cursor)

    assert advisor.enabled is False
    result = advisor.advise(
        task="hypothesis_review",
        subject="a claim",
        system="system",
        question={"q": 1},
        schema=_Trivial,
    )
    assert result is None
    assert len(cursor.rows) == 1
    assert cursor.rows[0][7] == "fallback"
    assert advisor.outcomes[0].used is False


def test_a_rejected_answer_is_distinguished_from_a_provider_failure() -> None:
    """Different causes need different fixes: one is the vendor, one is the prompt."""
    cursor = _RecordingCursor()
    advisor = Advisor(cursor, enabled=False)
    advisor.reject(task="rule_authoring", subject="minutes_projection", reason="unknown field")
    assert cursor.rows[0][7] == "rejected"
