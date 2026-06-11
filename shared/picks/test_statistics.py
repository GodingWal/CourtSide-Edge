"""P1-1 / P1-2 / P1-4 / P3-1 unit tests: distributions, breakeven math,
correlation handling, and grading."""
import math

import pytest
from pydantic import ValidationError

from shared.picks.breakeven import (
    breakeven_probability,
    entry_ev,
    payout_map,
    publish_threshold,
)
from shared.picks.correlation import (
    check_entry_ev,
    joint_hit_probability,
    latent_rho,
    mc_standard_error,
)
from shared.picks.distributions import (
    POISSON,
    fit_dispersion,
    normal_p_over,
    p_over,
    project,
    std_from_dispersion,
)
from shared.picks.grading import allowed_in_entry, factor_alignment, grade_pick
from shared.picks.minutes_risk import apply_haircut, blowout_flags, minutes_haircut
from shared.picks.models import ProjectionInfo
from shared.picks.test_validation import make_payload, make_pick

# ── P1-2: payout-implied breakeven (verified against the spec table) ─────────

@pytest.mark.parametrize(
    "book,entry_type,legs,expected",
    [
        ("prizepicks", "power", 2, 0.5774),   # 3x
        ("prizepicks", "power", 3, 0.5848),   # 5x
        ("underdog", "power", 3, 0.5503),     # 6x
        ("prizepicks", "power", 6, 0.5466),   # 37.5x
    ],
)
def test_power_breakeven_matches_closed_form(book, entry_type, legs, expected):
    be = breakeven_probability(book, entry_type, legs)
    assert be == pytest.approx(expected, abs=1e-3)
    # closed form for all-or-nothing payouts
    multiplier = payout_map(book, entry_type, legs)[str(legs)]
    assert be == pytest.approx((1 / multiplier) ** (1 / legs), abs=1e-6)


def test_flex_breakeven_solves_ev_equals_one():
    for book, legs in (("prizepicks", 3), ("prizepicks", 5), ("underdog", 4)):
        be = breakeven_probability(book, "flex", legs)
        assert entry_ev(be, legs, payout_map(book, "flex", legs)) == pytest.approx(1.0, abs=1e-6)
        assert 0.5 < be < 0.7


def test_publish_threshold_adds_margin_and_escalation():
    be = breakeven_probability("prizepicks", "power", 3)
    assert publish_threshold("prizepicks", "power", 3) == pytest.approx(be + 0.02)
    assert publish_threshold("prizepicks", "power", 3, escalation_pp=3.0) == pytest.approx(be + 0.05)


def test_unknown_payout_combination_raises():
    with pytest.raises(KeyError):
        payout_map("prizepicks", "power", 9)


# ── P1-1: distribution outputs ───────────────────────────────────────────────

def test_mean_only_projection_is_schema_violation():
    with pytest.raises(ValidationError):
        ProjectionInfo(mean=21.5)  # std and hit_probability are required


def test_dispersion_fit_overdispersed_sample():
    # mean 18, variance well above mean: clearly overdispersed scoring log.
    samples = [8, 12, 31, 22, 9, 27, 14, 25, 11, 30, 16, 24, 6, 21, 19, 23]
    r = fit_dispersion(samples)
    assert math.isfinite(r) and r > 0
    mean = sum(samples) / len(samples)
    var = sum((x - mean) ** 2 for x in samples) / len(samples)
    assert r == pytest.approx(mean**2 / (var - mean))
    assert std_from_dispersion(mean, r) == pytest.approx(math.sqrt(var), rel=1e-6)


def test_dispersion_underdispersed_falls_back_to_poisson():
    samples = [18, 19, 18, 18, 19, 18, 19, 18, 18, 19, 18, 19, 18, 18, 19]
    assert fit_dispersion(samples) == POISSON


def test_dispersion_small_sample_uses_position_fallback():
    r = fit_dispersion([20.0] * 10, position="G", stat="PTS")
    assert r == 9.0  # config fallback, not fit from 10 games


def test_p_over_decreases_with_line_and_matches_distribution():
    assert p_over(20.0, 14.5, r=9.0) > p_over(20.0, 19.5, r=9.0) > p_over(20.0, 24.5, r=9.0)
    assert 0.0 < p_over(20.0, 19.5, r=9.0) < 1.0
    # Overdispersion fattens the tail vs Poisson.
    assert p_over(20.0, 29.5, r=6.0) > p_over(20.0, 29.5, r=POISSON)


def test_project_returns_full_distribution():
    samples = [8, 12, 31, 22, 9, 27, 14, 25, 11, 30, 16, 24, 6, 21, 19, 23]
    out = project(samples, line=19.5)
    assert set(out) == {"mean", "std", "p_over"}
    assert out["std"] > math.sqrt(out["mean"])  # overdispersed
    assert 0.0 < out["p_over"] < 1.0


def test_normal_p_over_symmetry():
    assert normal_p_over(20.0, 5.0, 20.0) == pytest.approx(0.5)
    assert normal_p_over(21.5, 5.5, 19.5) > 0.5 > normal_p_over(18.9, 5.5, 19.5)


# ── P1-3: minutes haircut ────────────────────────────────────────────────────

def test_haircut_schedule():
    assert minutes_haircut(0.60) == 0.0
    assert minutes_haircut(0.70) == pytest.approx(0.08)
    assert minutes_haircut(0.775) == pytest.approx(0.115)
    assert minutes_haircut(0.85) == pytest.approx(0.15)
    assert minutes_haircut(0.95) == pytest.approx(0.15)


def test_haircut_scales_mean_and_spread():
    mean, std = apply_haircut(20.0, 6.0, 0.85)
    assert mean == pytest.approx(17.0)
    assert std == pytest.approx(5.1)


def test_blowout_flags_underdog_side_marked_for_review():
    flags = blowout_flags(spread=16.0, team_win_probability=0.25)
    assert "BLOWOUT_RISK" in flags
    assert "UNDERDOG_GARBAGE_TIME_REVIEW" in flags


# ── P1-4: same-game correlation ──────────────────────────────────────────────

def _leg(player, team, opponent, stat, direction, p):
    return {"player": player, "team": team, "opponent": opponent, "stat": stat,
            "direction": direction, "hit_probability": p, "game_id": f"{team}@{opponent}"}


CLARK_OVER = _leg("Caitlin Clark", "IND", "CHI", "PTS", "over", 0.60)
BOSTON_UNDER = _leg("Aliyah Boston", "IND", "CHI", "PTS", "under", 0.60)


def test_clark_over_boston_under_joint_differs_from_product():
    """Spec acceptance test: same-game legs are not independent."""
    joint = joint_hit_probability([CLARK_OVER, BOSTON_UNDER])
    product = 0.60 * 0.60
    assert abs(joint - product) > 3 * mc_standard_error(product)
    # Shared team-total factor + opposite directions => negatively correlated hits.
    assert joint < product


def test_same_direction_same_team_legs_are_positively_correlated():
    other_over = dict(BOSTON_UNDER, direction="over")
    joint = joint_hit_probability([CLARK_OVER, other_over])
    assert joint > 0.36 + 3 * mc_standard_error(0.36)


def test_cross_game_legs_stay_independent():
    away_leg = _leg("A'ja Wilson", "LVA", "SEA", "PTS", "over", 0.60)
    joint = joint_hit_probability([CLARK_OVER, away_leg])
    assert joint == pytest.approx(0.36, abs=3 * mc_standard_error(0.36))


def test_relationship_classification():
    assert latent_rho(CLARK_OVER, BOSTON_UNDER) == 0.25       # same team, same stat
    assert latent_rho(CLARK_OVER, dict(BOSTON_UNDER, stat="AST")) == 0.20
    opponent = _leg("Angel Reese", "CHI", "IND", "PTS", "over", 0.6)
    opponent["game_id"] = CLARK_OVER["game_id"]
    assert latent_rho(CLARK_OVER, opponent) == 0.15
    away = _leg("A'ja Wilson", "LVA", "SEA", "PTS", "over", 0.6)
    assert latent_rho(CLARK_OVER, away) == 0.0


def test_correlation_can_fail_entry_ev():
    """Two 59% legs clear a 2-leg power marginally when independent, but the
    same-game negative hit correlation drags EV below breakeven."""
    legs = [dict(CLARK_OVER, hit_probability=0.59),
            dict(BOSTON_UNDER, hit_probability=0.59)]
    pp_2_power = payout_map("prizepicks", "power", 2)
    result = check_entry_ev(legs, pp_2_power)
    assert not result["approved"]
    assert result["reason_code"] == "CORRELATION_EV_FAIL"
    # Sanity: independent marginals would have cleared it.
    assert entry_ev(0.59, 2, pp_2_power) > 1.0


# ── P3-1: grading ────────────────────────────────────────────────────────────

THRESHOLD = publish_threshold("prizepicks", "power", 3)


def test_grade_a_strong_edge_and_alignment():
    pick = make_pick(hit_probability=round(THRESHOLD + 0.06, 4))
    payload = make_payload(injuries=[{
        "player": "Kelsey Mitchell", "team": "Indiana Fever", "status": "OUT",
        "last_updated": "2026-06-11T12:00:00+00:00"}])
    assert grade_pick(pick, payload) == "A"
    assert sum(factor_alignment(pick, payload).values()) >= 3


def test_grade_b_moderate_edge_two_factors():
    pick = make_pick(hit_probability=round(THRESHOLD + 0.03, 4))
    payload = make_payload(matchup={"opponent": "Chicago Sky",
                                    "opp_def_rank_vs_stat": 10, "pace_rank": 12})
    assert grade_pick(pick, payload) == "B"


def test_grade_c_threshold_with_mixed_factors():
    pick = make_pick(hit_probability=round(THRESHOLD + 0.001, 4))
    payload = make_payload(
        form={"l5_avg": 17.0, "l10_avg": 18.0, "minutes_l5": 33.0},
        matchup={"opponent": "Chicago Sky", "opp_def_rank_vs_stat": 2, "pace_rank": 12},
    )
    assert grade_pick(pick, payload) == "C"


def test_grade_d_below_threshold():
    pick = make_pick(hit_probability=round(THRESHOLD - 0.01, 4))
    assert grade_pick(pick, make_payload()) == "D"


def test_entry_builder_enforcement():
    assert allowed_in_entry("A", "power") and allowed_in_entry("B", "power")
    assert not allowed_in_entry("C", "power")
    assert allowed_in_entry("C", "flex")
    assert not allowed_in_entry("D", "power") and not allowed_in_entry("D", "flex")
