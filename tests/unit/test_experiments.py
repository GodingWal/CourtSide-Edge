"""The experiment lifecycle, and the locks on its last step.

The comparison logic is tested for the three ways a champion/challenger result can lie: an
unpaired sample, a sample that is large only because the board was re-scored, and an aggregate
win covering a subgroup loss. The promotion path is tested the way the rule approval path is --
by trying to get past it.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from wnba_services.forecasting.challenger_types import ArtifactBacked, ChallengerPrediction
from wnba_services.forecasting.challengers import CHALLENGERS
from wnba_services.forecasting.gbm import ArtifactMissing
from wnba_services.forecasting.scoring import HistoryGame, ScoringInputs
from wnba_services.learning_loop.experiments import (
    MATERIAL_LOG_LOSS_GAIN,
    MINIMUM_SUBGROUP_SAMPLE,
    _paired_log_loss_gain,
    _score,
    _subgroup_rows,
    _verdict,
    promote_challenger_with_cursor,
    record_shadow_predictions,
)

TIP = datetime(2026, 8, 5, 23, tzinfo=UTC)


class RecordingCursor:
    """Captures statements and answers ``fetchone`` from a scripted row."""

    def __init__(self, row: dict[str, Any] | None = None, rowcount: int = 1) -> None:
        self.row = row
        self.rowcount = rowcount
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def execute(self, query: str, params: tuple[Any, ...] = ()) -> None:
        self.calls.append((" ".join(query.split()), params))

    def fetchone(self) -> dict[str, Any] | None:
        return None if self.row is None else dict(self.row)

    def fetchall(self) -> list[dict[str, Any]]:
        return [] if self.row is None else [dict(self.row)]


def _paired_row(
    *,
    champion: float,
    challenger: float,
    hit: bool,
    prop_type: str = "points",
    line: float = 14.5,
    player: str = "player-1",
    game: str = "game-1",
    minutes_before_tip: float = 120.0,
) -> dict[str, Any]:
    return {
        "player_id": player,
        "game_id": game,
        "prop_type": prop_type,
        "side": "over",
        "line": Decimal(str(line)),
        "predicted_probability": champion,
        "challenger_probability": challenger,
        "projected_mean": 15.0,
        "challenger_mean": 15.2,
        "actual_stat": 16.0,
        "closing_line": Decimal(str(line + 0.5)),
        "hit": hit,
        "forecast_timestamp": TIP - timedelta(minutes=minutes_before_tip),
        "scheduled_tipoff": TIP,
    }


def _pairs(count: int, *, edge: float, seed: int = 11, **overrides: Any) -> list[dict[str, Any]]:
    """Paired forecasts with per-row noise, so the paired variance is not degenerate.

    ``edge`` is how far the challenger leans toward the truth relative to the champion. A
    positive edge is a genuinely sharper model; a negative one is a confidently wrong model.
    Both need row-to-row variation, because a series of identical differences has zero variance
    and no statistic worth computing -- which is itself behaviour worth keeping.
    """
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        hit = rng.random() < 0.5
        direction = 1.0 if hit else -1.0
        champion = 0.5 + direction * 0.05 + rng.uniform(-0.03, 0.03)
        challenger = min(0.95, max(0.05, champion + direction * edge + rng.uniform(-0.03, 0.03)))
        rows.append(
            _paired_row(
                champion=champion,
                challenger=challenger,
                hit=hit,
                player=f"player-{index}",
                game=f"game-{index // 4}",
                **overrides,
            )
        )
    return rows


def _confident_pairs(count: int, **overrides: Any) -> list[dict[str, Any]]:
    """A challenger that is right and confident where the champion hedges."""
    return _pairs(count, edge=0.2, **overrides)


def test_the_comparison_is_paired_not_two_separate_averages() -> None:
    """Paired differences are what make a real gain detectable on a small sample."""
    rows = _confident_pairs(60)

    gain, statistic = _paired_log_loss_gain(rows)

    assert gain > MATERIAL_LOG_LOSS_GAIN
    assert statistic is not None
    assert statistic > 2.0
    champion = _score(rows, "predicted_probability")
    challenger = _score(rows, "challenger_probability")
    assert challenger.log_loss is not None and champion.log_loss is not None
    assert challenger.log_loss < champion.log_loss


def test_a_challenger_that_is_confidently_wrong_loses() -> None:
    rows = _pairs(60, edge=-0.25)

    gain, statistic = _paired_log_loss_gain(rows)

    assert gain < 0
    assert (
        _verdict(
            independent=500,
            minimum_sample=200,
            gain=gain,
            statistic=statistic,
            subgroups=[],
        )
        == "challenger_worse"
    )


def test_an_aggregate_win_hiding_a_subgroup_loss_does_not_reach_challenger_better() -> None:
    """The check that stops a promotion built on one market carrying the rest."""
    good = _confident_pairs(120, prop_type="points")
    bad = _pairs(MINIMUM_SUBGROUP_SAMPLE + 10, edge=-0.3, seed=23, prop_type="rebounds")
    rows = good + bad

    subgroups = _subgroup_rows(rows)
    rebounds = next(
        group
        for group in subgroups
        if group["dimension"] == "market" and group["value"] == "rebounds"
    )
    gain, statistic = _paired_log_loss_gain(rows)

    assert rebounds["degraded"] is True
    assert (
        _verdict(
            independent=500,
            minimum_sample=200,
            gain=gain,
            statistic=statistic,
            subgroups=subgroups,
        )
        == "subgroup_degradation"
    )


def test_a_thin_subgroup_cannot_block_on_one_bad_night() -> None:
    rows = _confident_pairs(120) + _pairs(5, edge=-0.35, seed=31, prop_type="assists")

    assists = next(
        group
        for group in _subgroup_rows(rows)
        if group["dimension"] == "market" and group["value"] == "assists"
    )

    assert assists["conclusive"] is False
    assert assists["degraded"] is False


def test_a_large_but_dependent_sample_is_still_insufficient() -> None:
    """Re-scoring one night's board a hundred times is one night, not a track record."""
    assert (
        _verdict(independent=64.0, minimum_sample=200, gain=0.4, statistic=9.0, subgroups=[])
        == "insufficient_sample"
    )


def test_a_tiny_gain_is_reported_as_no_difference() -> None:
    assert (
        _verdict(independent=500, minimum_sample=200, gain=0.001, statistic=8.0, subgroups=[])
        == "no_difference"
    )


# --------------------------------------------------------------------------------------
# Promotion: the locked step
# --------------------------------------------------------------------------------------
def _evaluated_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "status": "evaluated",
        "verdict": "challenger_better",
        "promoted": False,
        "independent_sample_size": 260,
        "minimum_sample": 200,
        "subgroups": [{"dimension": "market", "value": "points", "degraded": False}],
        "challenger_name": "state-space-role",
        "champion_model_version_id": uuid4(),
        "challenger_model_version_id": uuid4(),
    }
    row.update(overrides)
    return row


def test_promotion_records_a_named_human_and_swaps_the_stages() -> None:
    cursor = RecordingCursor(_evaluated_row())
    experiment_id = uuid4()
    at = datetime(2026, 8, 6, 15, tzinfo=UTC)

    result = promote_challenger_with_cursor(
        cursor,
        experiment_id,
        approved_by="Goding Wal",
        reason="Beats the champion out of sample with no subgroup degradation.",
        now=at,
    )

    assert result.status == "promoted"
    assert result.actor == "Goding Wal"
    statements = [call[0] for call in cursor.calls]
    assert any("SET promoted=true" in statement for statement in statements)
    assert any("stage='champion'" in statement for statement in statements)
    assert any("stage='deprecated'" in statement for statement in statements)


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (None, "unknown experiment"),
        (_evaluated_row(promoted=True), "already been promoted"),
        (_evaluated_row(status="running"), "must be evaluated"),
        (_evaluated_row(verdict="no_difference"), "not 'challenger_better'"),
        (_evaluated_row(verdict="insufficient_sample"), "not 'challenger_better'"),
        (_evaluated_row(independent_sample_size=90), "below its gate"),
        (
            _evaluated_row(
                subgroups=[{"dimension": "market", "value": "rebounds", "degraded": True}]
            ),
            "subgroup degradation",
        ),
    ],
)
def test_promotion_fails_closed(row: dict[str, Any] | None, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        promote_challenger_with_cursor(
            RecordingCursor(row),
            uuid4(),
            approved_by="Goding Wal",
            reason="A reason long enough to satisfy the audit requirement.",
        )


@pytest.mark.parametrize(
    ("approver", "reason", "message"),
    [
        ("", "A reason long enough to satisfy the audit requirement.", "named human"),
        ("G", "A reason long enough to satisfy the audit requirement.", "named human"),
        ("Goding Wal", "too short", "audit reason"),
    ],
)
def test_promotion_requires_an_identity_and_a_reason(
    approver: str, reason: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        promote_challenger_with_cursor(
            RecordingCursor(_evaluated_row()), uuid4(), approved_by=approver, reason=reason
        )


# --------------------------------------------------------------------------------------
# Shadow execution
# --------------------------------------------------------------------------------------
def _scoring_inputs() -> ScoringInputs:
    history = tuple(
        HistoryGame(
            minutes=30.0,
            started=True,
            stats={
                "points": 15.0,
                "rebounds_offensive": 1.0,
                "rebounds_defensive": 3.0,
                "assists": 2.0,
                "three_pointers_made": 1.0,
            },
        )
        for _ in range(18)
    )
    return ScoringInputs(
        prop_type="points",
        line=Decimal("14.5"),
        history=history,
        league_rate_per_minute=0.45,
    )


def test_shadow_predictions_are_written_for_each_running_experiment() -> None:
    cursor = RecordingCursor(rowcount=1)
    experiments = [
        {
            "experiment_id": uuid4(),
            "challenger_name": "hierarchical-bayes",
            "challenger_model_version_id": uuid4(),
        },
        {
            "experiment_id": uuid4(),
            "challenger_name": "state-space-role",
            "challenger_model_version_id": uuid4(),
        },
    ]

    written = record_shadow_predictions(
        cursor,
        experiments,
        episode_id=uuid4(),
        inputs=_scoring_inputs(),
        side="over",
        at=TIP,
    )

    assert written == 2
    for statement, params in cursor.calls:
        assert "INSERT INTO wnba.challenger_predictions" in statement
        assert params[12] is False  # failed
        assert params[11] >= 0.0  # latency_ms


def test_an_artifact_backed_challenger_is_prepared_before_it_is_asked_to_predict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shadow path owns the loading, or the family never loads at all.

    A family whose weights live in the database is registered, asked to predict, and -- with no
    caller for its loader -- raises on every episode forever. The experiment record then shows a
    100% failure rate, which reads as "this model is broken" rather than "nobody wired it up".
    """
    prepared: list[Any] = []

    class Fake:
        name = "fake-artifact-backed"

        def ensure_loaded(self, cur: Any) -> bool:
            prepared.append(cur)
            return True

        def predict(self, inputs: ScoringInputs) -> ChallengerPrediction:
            if not prepared:
                raise ArtifactMissing("no active artifact")
            return ChallengerPrediction(
                name=self.name,
                version="0.0.1",
                mean=15.0,
                stddev=5.0,
                over=0.55,
                push=0.0,
                under=0.45,
                pmf=(0.45, 0.55),
                diagnostics={},
            )

    monkeypatch.setitem(CHALLENGERS, "fake-artifact-backed", cast(Any, Fake()))
    cursor = RecordingCursor(rowcount=1)

    written = record_shadow_predictions(
        cursor,
        [
            {
                "experiment_id": uuid4(),
                "challenger_name": "fake-artifact-backed",
                "challenger_model_version_id": uuid4(),
            }
        ],
        episode_id=uuid4(),
        inputs=_scoring_inputs(),
        side="over",
        at=TIP,
    )

    assert written == 1
    assert prepared == [cursor]
    _, params = cursor.calls[-1]
    assert params[12] is False  # failed
    assert params[9] == pytest.approx(0.55)


def test_every_artifact_backed_family_in_the_registry_is_recognised_as_one() -> None:
    """``isinstance`` against the protocol is what the wiring depends on.

    ``GradientBoostedChallenger`` is the family this exists for; if its loader were renamed or
    its signature drifted, the runtime check would quietly stop matching and the family would go
    back to raising on every episode with nothing failing in CI.
    """
    assert isinstance(CHALLENGERS["gradient-boosted"], ArtifactBacked)


def test_a_challenger_that_raises_is_recorded_as_a_failure_not_dropped() -> None:
    """Silently skipping the props a model cannot score is how a failure rate disappears."""
    cursor = RecordingCursor(rowcount=1)
    inputs = ScoringInputs(
        prop_type="points",
        line=Decimal("14.5"),
        history=(),  # too little history for `resolve_dispersion` to have anything to read
        league_rate_per_minute=0.45,
    )
    experiments = [
        {
            "experiment_id": uuid4(),
            "challenger_name": "unregistered-family",
            "challenger_model_version_id": uuid4(),
        }
    ]

    written = record_shadow_predictions(
        cursor, experiments, episode_id=uuid4(), inputs=inputs, side="over", at=TIP
    )

    assert written == 1
    _, params = cursor.calls[-1]
    assert params[12] is True  # failed
    assert "unknown challenger" in str(params[13])
    assert params[9] is None and params[10] is None  # no probabilities on a failure
