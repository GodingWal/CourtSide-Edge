"""Liveness checks: the monitor that catches silent idleness.

The failures these exist for all looked like success. So the tests here are less about SQL
and more about the properties that decide whether the monitor is trustworthy: does it stay
quiet when quiet is correct, does it name the right severity, and does it survive its own
queries breaking.
"""

from __future__ import annotations

import re

from wnba_domain.enums import Severity, ValidationLevel
from wnba_services.monitoring.liveness import (
    CHECKS,
    Finding,
    LivenessCheck,
    LivenessReport,
    run_liveness_checks,
)


def check(code: str) -> LivenessCheck:
    return next(c for c in CHECKS if c.code == code)


# --------------------------------------------------------------------------------------
# The three real failures each have a check
# --------------------------------------------------------------------------------------
def test_every_observed_silent_failure_has_a_check() -> None:
    """Each of these was found by a human noticing an empty screen. Never again by that route."""
    codes = {c.code for c in CHECKS}
    assert "GAME_PAST_TIPOFF_NOT_FINAL" in codes, "the ingest-ledger failure"
    assert "QUOTED_PLAYERS_WITHOUT_ROLES" in codes, "role projection skipping everything"
    assert "QUOTED_GAME_WITHOUT_FORECASTS" in codes, "the forecaster skipping everything"
    assert "MARKET_ARCHIVE_STALE" in codes, "the archive is the one thing that cannot be rebuilt"
    assert "SETTLED_GAME_WITH_UNSCORED_EPISODES" in codes, "settlement stalling invisibly"


def test_checks_that_stop_recommendations_are_marked_blocking() -> None:
    """A game we priced but never forecast must not quietly leave a stale board recommendable."""
    assert check("QUOTED_GAME_WITHOUT_FORECASTS").blocks_recommendations
    assert check("GAME_PAST_TIPOFF_NOT_FINAL").blocks_recommendations


def test_blocking_severity_matches_blocking_behaviour() -> None:
    """The domain refuses a BLOCKING incident that does not block, so these must agree."""
    for c in CHECKS:
        if c.severity is Severity.BLOCKING:
            assert c.blocks_recommendations, (
                f"{c.code} is BLOCKING severity but does not block; DataQualityIncident "
                "would reject it at construction"
            )


# --------------------------------------------------------------------------------------
# Not crying wolf
# --------------------------------------------------------------------------------------
def test_every_check_is_scoped_so_a_quiet_night_is_not_an_outage() -> None:
    """A monitor that alerts when nothing is scheduled gets muted, and a muted monitor is
    worse than none because it still implies coverage."""
    for c in CHECKS:
        sql = c.sql.upper()
        guarded = "HAVING" in sql or "GROUP BY" in sql
        assert guarded, (
            f"{c.code} has no HAVING/GROUP BY guard, so it may return a row even when there "
            "is nothing wrong"
        )


def test_upcoming_game_checks_allow_a_grace_period() -> None:
    """A board that appeared two minutes ago has not had time to be forecast."""
    for code in ("QUOTED_GAME_WITHOUT_FORECASTS", "QUOTED_PLAYERS_WITHOUT_ROLES"):
        sql = check(code).sql
        assert re.search(r"system_from\s*<\s*now\(\)\s*-\s*interval", sql), (
            f"{code} must require the quotes to be old enough that forecasting should have run"
        )


def test_checks_only_consider_games_that_have_not_started() -> None:
    for code in ("QUOTED_GAME_WITHOUT_FORECASTS", "QUOTED_PLAYERS_WITHOUT_ROLES"):
        assert "scheduled_tipoff > now()" in check(code).sql


# --------------------------------------------------------------------------------------
# Structural
# --------------------------------------------------------------------------------------
def test_every_check_states_a_question_in_plain_language() -> None:
    """An alert nobody understands is an alert nobody acts on."""
    for c in CHECKS:
        assert c.question.endswith("?"), f"{c.code} does not state a question"
        assert len(c.question) > 20


def test_check_codes_are_unique_and_stable_identifiers() -> None:
    codes = [c.code for c in CHECKS]
    assert len(set(codes)) == len(codes)
    for code in codes:
        assert re.fullmatch(r"[A-Z][A-Z_]+", code), f"{code} is not a stable screaming-snake id"


def test_checks_select_a_detail_column() -> None:
    """run_liveness_checks reads row['detail']; a check that forgets it would report noise."""
    for c in CHECKS:
        assert "AS detail" in c.sql, f"{c.code} does not project a detail column"


def test_all_checks_are_temporal_level() -> None:
    for c in CHECKS:
        assert c.level is ValidationLevel.TEMPORAL


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------
def test_an_empty_report_is_healthy_and_says_so() -> None:
    report = LivenessReport(
        checked_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        checks_run=len(CHECKS),
    )
    assert report.healthy
    assert "OK" in report.summary()
    assert report.blocking == []


def test_a_failing_report_names_the_check_in_its_summary() -> None:
    report = LivenessReport(
        checked_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        checks_run=len(CHECKS),
    )
    report.findings.append(
        Finding(
            check=check("QUOTED_GAME_WITHOUT_FORECASTS"), detail="9 players quoted, 0 forecasts"
        )
    )
    assert not report.healthy
    assert "QUOTED_GAME_WITHOUT_FORECASTS" in report.summary()
    assert len(report.blocking) == 1


def test_run_liveness_checks_is_importable_without_a_database() -> None:
    """The monitor must not require the thing it monitors in order to be loaded and tested."""
    assert callable(run_liveness_checks)


def test_identity_sensitive_checks_resolve_through_player_merges() -> None:
    """Regression: on its first live run QUOTED_PLAYERS_WITHOUT_ROLES reported all nine
    athletes as role-less because it read the raw quote id, while the role projector writes
    against the merged canonical id. Every check that reasons about a player must resolve
    identity the same way the pipeline does, or it reports a permanent false alarm -- and a
    monitor that cries wolf gets muted, which is worse than no monitor at all.
    """
    for code in ("QUOTED_PLAYERS_WITHOUT_ROLES", "UNMERGED_QUOTED_ATHLETES"):
        sql = check(code).sql
        assert "player_merges" in sql, f"{code} ignores merges and will false-positive"
        assert "coalesce(" in sql, f"{code} does not fall back to the raw id when unmerged"
