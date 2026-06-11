"""P0-1 / P0-4 / P1-2 / P1-3 unit tests for the pure validation gates."""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from shared.picks.models import (
    NarrativePayload,
    Pick,
    PickStatus,
    pick_from_message,
)
from shared.picks.reason_codes import ReasonCode
from shared.picks.validation import validate_pick

NOW = datetime(2026, 6, 11, 18, 0, tzinfo=timezone.utc)
BREAKEVEN_3_POWER = 0.5848  # PrizePicks 3-leg power (5x)
THRESHOLD = BREAKEVEN_3_POWER + 0.02


def make_pick(**overrides) -> Pick:
    base = dict(
        pick_id="p1",
        player="Caitlin Clark",
        team="Indiana Fever",
        stat="PTS",
        book="prizepicks",
        entry_type="power",
        legs=3,
        recommendation="Buy",
        projection=21.5,
        std=5.5,
        line=19.5,
        hit_probability=0.65,
        breakeven_probability=BREAKEVEN_3_POWER,
    )
    base.update(overrides)
    return Pick(**base)


def make_payload(**overrides) -> NarrativePayload:
    base = dict(
        player={"name": "Caitlin Clark", "team": "Indiana Fever",
                "position": "G", "career_games": 70, "seasons": 3},
        stat={"category": "PTS", "line": 19.5, "book": "prizepicks"},
        projection={"mean": 21.5, "std": 5.5, "hit_probability": 0.65},
        form={"l5_avg": 22.4, "l10_avg": 21.1, "minutes_l5": 33.0},
        matchup={"opponent": "Chicago Sky", "opp_def_rank_vs_stat": 10,
                 "pace_rank": 3},
        game={"spread": -6.5, "total": 165.5, "win_probability": 0.62,
              "home": True},
        injuries=[],
    )
    base.update(overrides)
    return NarrativePayload(**base)


# ── P0-1: edge-sign consistency ──────────────────────────────────────────────

def test_buy_with_positive_edge_passes():
    result = validate_pick(make_pick(), now=NOW)
    assert result.status == PickStatus.PUBLISHABLE


def test_buy_with_negative_edge_fails():
    result = validate_pick(make_pick(projection=18.9), now=NOW)
    assert result.status == PickStatus.REJECTED
    assert result.reason_codes == (ReasonCode.EDGE_SIGN_MISMATCH.value,)


def test_sell_with_negative_edge_passes():
    result = validate_pick(
        make_pick(recommendation="Sell", projection=17.0, hit_probability=0.66),
        now=NOW,
    )
    assert result.status == PickStatus.PUBLISHABLE


def test_sell_with_positive_edge_fails():
    result = validate_pick(make_pick(recommendation="Sell"), now=NOW)
    assert result.reason_codes == (ReasonCode.EDGE_SIGN_MISMATCH.value,)


def test_zero_edge_is_no_pick():
    result = validate_pick(make_pick(projection=19.5), now=NOW)
    assert result.status == PickStatus.REJECTED
    assert result.reason_codes == (ReasonCode.EDGE_SIGN_MISMATCH.value,)


def test_regression_clark_buy_below_line_must_fail():
    """The 2026-06-11 slate bug: Buy with projection 18.9 under line 19.5."""
    result = validate_pick(
        make_pick(projection=18.9, line=19.5, recommendation="Buy"), now=NOW
    )
    assert result.status == PickStatus.REJECTED
    assert ReasonCode.EDGE_SIGN_MISMATCH.value in result.reason_codes


# ── P0-2: frozen single-source-of-truth numerics ─────────────────────────────

def test_pick_numeric_fields_are_frozen():
    pick = make_pick()
    with pytest.raises(ValidationError):
        pick.projection = 25.0


def test_edge_is_computed_not_supplied():
    pick = make_pick(projection=21.5, line=19.5)
    assert pick.edge == 2.0
    with pytest.raises(ValidationError):
        Pick(**{**make_pick().model_dump(exclude={"edge"}), "edge": 99.0})


def test_pick_round_trips_through_transport():
    pick = make_pick()
    assert pick_from_message(pick.model_dump()) == pick


# ── P1-2: breakeven threshold gate ───────────────────────────────────────────

def test_53_percent_in_3_leg_power_context_does_not_publish():
    result = validate_pick(make_pick(hit_probability=0.53), now=NOW)
    assert result.status == PickStatus.LEAN
    assert ReasonCode.BELOW_THRESHOLD.value in result.reason_codes


def test_below_coinflip_is_rejected_outright():
    result = validate_pick(make_pick(hit_probability=0.49), now=NOW)
    assert result.status == PickStatus.REJECTED
    assert ReasonCode.BELOW_THRESHOLD.value in result.reason_codes


def test_just_above_threshold_publishes():
    result = validate_pick(make_pick(hit_probability=THRESHOLD + 0.001), now=NOW)
    assert result.status == PickStatus.PUBLISHABLE


# ── P0-4: injury staleness + roster ──────────────────────────────────────────

def _injury(hours_old: float, **overrides) -> dict:
    record = {
        "player": "Courtney Vandersloot",
        "team": "Chicago Sky",
        "status": "OUT",
        "last_updated": (NOW - timedelta(hours=hours_old)).isoformat(),
    }
    record.update(overrides)
    return record


def test_injury_record_25h_old_rejects():
    payload = make_payload(injuries=[_injury(25)])
    result = validate_pick(make_pick(), payload, now=NOW)
    assert result.status == PickStatus.REJECTED
    assert ReasonCode.STALE_INJURY_DATA.value in result.reason_codes


def test_injury_record_23h_old_passes():
    payload = make_payload(injuries=[_injury(23)])
    result = validate_pick(make_pick(), payload, now=NOW)
    assert result.status == PickStatus.PUBLISHABLE


def test_roster_mismatch_rejects():
    roster = {"caitlin clark": "Chicago Sky"}
    result = validate_pick(make_pick(), make_payload(), now=NOW, roster=roster)
    assert result.status == PickStatus.REJECTED
    assert ReasonCode.ROSTER_MISMATCH.value in result.reason_codes


def test_roster_match_passes():
    roster = {"caitlin clark": "Indiana Fever"}
    result = validate_pick(make_pick(), make_payload(), now=NOW, roster=roster)
    assert result.status == PickStatus.PUBLISHABLE


# ── P1-3: blowout escalation ─────────────────────────────────────────────────

def _blowout_payload() -> NarrativePayload:
    return make_payload(game={"spread": -12.0, "total": 165.5,
                              "win_probability": 0.80, "home": True})


def test_blowout_buy_at_threshold_plus_1pp_is_blocked():
    pick = make_pick(hit_probability=round(THRESHOLD + 0.01, 4))
    result = validate_pick(pick, _blowout_payload(), now=NOW)
    assert result.status == PickStatus.LEAN
    assert ReasonCode.BELOW_THRESHOLD.value in result.reason_codes
    assert ReasonCode.BLOWOUT_RISK.value in result.reason_codes
    assert ReasonCode.BLOWOUT_RISK.value in result.flags


def test_blowout_buy_clearing_escalated_threshold_publishes_with_flag():
    pick = make_pick(hit_probability=round(THRESHOLD + 0.035, 4))
    result = validate_pick(pick, _blowout_payload(), now=NOW)
    assert result.status == PickStatus.PUBLISHABLE
    assert ReasonCode.BLOWOUT_RISK.value in result.flags


def test_same_probability_without_blowout_publishes():
    pick = make_pick(hit_probability=round(THRESHOLD + 0.01, 4))
    result = validate_pick(pick, make_payload(), now=NOW)
    assert result.status == PickStatus.PUBLISHABLE
    assert not result.flags


def test_blowout_sell_is_not_escalated():
    pick = make_pick(recommendation="Sell", projection=17.0,
                     hit_probability=round(THRESHOLD + 0.01, 4))
    result = validate_pick(pick, _blowout_payload(), now=NOW)
    assert result.status == PickStatus.PUBLISHABLE
