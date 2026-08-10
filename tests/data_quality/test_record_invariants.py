"""The invariants that decide whether the record can be believed at all.

These are not tests of features. They are tests of the properties every conclusion downstream
quietly assumes: that a void is not a loss, that a push is not an outcome, that a settled result
is compared against the line it was actually forecast at, and that the schema will refuse a row
which violates any of it.

The reason they are worth their own suite is that every one of these failures is silent. A void
scored as a loss produces a slightly worse Brier score and no error. A line stored at 15.25
settles against a comparison that can never push. Nothing crashes; the numbers are simply wrong,
and they stay wrong through calibration, drift detection and the readiness gates.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from wnba_services.learning_loop.settlement import STAT_COLUMNS, actual_stat, resolve_outcome

MIGRATIONS = Path(__file__).resolve().parents[2] / "infrastructure" / "migrations"


def _migration_text() -> str:
    return "\n".join(path.read_text() for path in sorted(MIGRATIONS.glob("*.sql")))


# --------------------------------------------------------------------------------------
# Void, push, hit -- the three-way distinction everything downstream depends on
# --------------------------------------------------------------------------------------
def test_a_player_who_never_appeared_voids_rather_than_losing() -> None:
    """Counting a scratch as a loss blames the model for a rotation decision."""
    resolution = resolve_outcome(side="over", actual=0.0, forecast_line=15.5, minutes=0.0)
    assert resolution.voided
    assert not resolution.hit
    assert not resolution.is_scoreable


def test_a_missing_box_score_row_voids_rather_than_settling_at_zero() -> None:
    resolution = resolve_outcome(side="over", actual=0.0, forecast_line=15.5, minutes=None)
    assert resolution.voided
    assert not resolution.is_scoreable


def test_landing_exactly_on_the_line_is_a_push_and_carries_no_information() -> None:
    resolution = resolve_outcome(side="over", actual=16.0, forecast_line=16.0, minutes=30.0)
    assert resolution.pushed
    assert not resolution.voided
    assert not resolution.is_scoreable


def test_only_a_resolved_win_or_loss_is_scoreable() -> None:
    for side, actual, expected in (("over", 22.0, True), ("over", 9.0, False)):
        resolution = resolve_outcome(side=side, actual=actual, forecast_line=15.5, minutes=30.0)
        assert resolution.is_scoreable
        assert resolution.hit is expected


def test_the_under_side_resolves_as_the_mirror_of_the_over() -> None:
    """A sign error here would corrupt every conclusion while looking entirely plausible."""
    for actual in (9.0, 22.0):
        over = resolve_outcome(side="over", actual=actual, forecast_line=15.5, minutes=30.0)
        under = resolve_outcome(side="under", actual=actual, forecast_line=15.5, minutes=30.0)
        assert over.hit is not under.hit


def test_a_scratch_voids_whichever_side_was_taken() -> None:
    for side in ("over", "under"):
        assert resolve_outcome(side=side, actual=0.0, forecast_line=15.5, minutes=0.0).voided


# --------------------------------------------------------------------------------------
# Market definitions
# --------------------------------------------------------------------------------------
def test_every_settleable_market_sums_real_box_score_columns() -> None:
    for market, columns in STAT_COLUMNS.items():
        assert columns, market
        assert len(set(columns)) == len(columns), f"{market} double-counts a column"


def test_rebounds_are_always_the_sum_of_both_kinds() -> None:
    """Settling rebounds off one column would silently halve every rebound market."""
    for market, columns in STAT_COLUMNS.items():
        if "rebounds_offensive" in columns:
            assert "rebounds_defensive" in columns, market
        if "rebounds_defensive" in columns:
            assert "rebounds_offensive" in columns, market


def test_a_combination_market_contains_each_of_its_named_parts() -> None:
    assert set(STAT_COLUMNS["points_rebounds_assists"]) == {
        "points",
        "rebounds_offensive",
        "rebounds_defensive",
        "assists",
    }
    assert set(STAT_COLUMNS["points_assists"]) == {"points", "assists"}


def test_an_unsupported_market_is_refused_rather_than_settled_as_zero() -> None:
    with pytest.raises(ValueError, match="unsupported settlement market"):
        actual_stat({"points": 10}, "double_doubles")


def test_the_settled_stat_is_the_sum_of_its_columns() -> None:
    line = {"points": 18, "rebounds_offensive": 2, "rebounds_defensive": 6, "assists": 4}
    assert actual_stat(line, "points_rebounds_assists") == pytest.approx(30.0)
    assert actual_stat(line, "rebounds") == pytest.approx(8.0)


# --------------------------------------------------------------------------------------
# Schema guarantees -- enforced by the database, checked here so a migration cannot drop them
# --------------------------------------------------------------------------------------
def test_lines_are_constrained_to_half_point_increments() -> None:
    """A line stored at 15.25 settles against a comparison that can never push."""
    assert "line_is_half_point" in _migration_text()


def test_bitemporal_ranges_cannot_close_before_they_open() -> None:
    text = _migration_text()
    assert "quote_valid_range" in text
    assert "quote_system_range" in text


def test_a_blocking_incident_must_actually_block() -> None:
    assert "blocking_incidents_must_block" in _migration_text()


def test_a_rule_cannot_go_active_without_a_human_and_a_backtest() -> None:
    """The asymmetry the whole rules layer rests on, enforced in the database as well."""
    text = _migration_text()
    assert "active_rules_require_a_human" in text
    assert "active_rules_require_a_backtest" in text


def test_a_rule_firing_can_never_increase_confidence() -> None:
    assert "firings_never_increase_confidence" in _migration_text()


def test_a_team_cannot_play_itself() -> None:
    assert "a_team_cannot_play_itself" in _migration_text()


def test_an_override_must_record_its_reason() -> None:
    assert "override_requires_reason" in _migration_text()


def test_fitted_parameters_are_superseded_rather_than_updated() -> None:
    """A forecast must stay explicable with the parameters in force when it was made."""
    text = _migration_text()
    assert "fitted_parameters_superseded_after_fitting" in text
    assert "fitted_parameters_live_idx" in text


def test_dispersion_can_never_be_recorded_below_poisson() -> None:
    assert re.search(r"dispersion IS NULL OR dispersion >= 1\.0", _migration_text())


def test_paper_is_the_default_rather_than_something_to_remember() -> None:
    """Analysis-only must not depend on every insert site getting a flag right."""
    text = _migration_text()
    assert re.search(r"is_paper\s+BOOLEAN NOT NULL DEFAULT TRUE", text)


def test_a_research_verdict_has_nowhere_to_put_a_prop_probability() -> None:
    """Research may raise caution about a forecast. It may never contribute one.

    Enforced by the shape of the table rather than by reviewer attention: there is no column a
    predicted probability, edge or side could be written into, so the boundary cannot be crossed
    by an insert that merely looks reasonable.
    """
    text = _migration_text()
    table = re.search(
        r"CREATE TABLE IF NOT EXISTS wnba\.research_verdicts \((.*?)\n\);", text, re.DOTALL
    )
    assert table is not None
    names = re.findall(r"^\s{4}(\w+)\s", table.group(1), re.MULTILINE)
    assert "caution" in names, "the verdict table was not parsed as expected"
    for forbidden in ("probability", "predicted", "edge", "side", "stake", "recommend"):
        assert not [name for name in names if forbidden in name], forbidden


def test_a_research_run_cannot_report_fewer_provider_calls_than_agents() -> None:
    """Retries only ever add calls; the reverse means the accounting was written by hand."""
    assert "research_run_counts_are_not_negative" in _migration_text()


def test_every_migration_is_a_single_transaction() -> None:
    """A half-applied migration leaves a schema no code was written against.

    Migrations 033 and 034 reached production before this invariant was added. Their checksums
    are immutable, so they are documented exceptions rather than silently rewritten history.
    """
    legacy_non_transactional = {"033_minutes_overrides.sql", "034_error_causal_chains.sql"}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        if path.name in legacy_non_transactional:
            continue
        body = path.read_text()
        assert body.count("BEGIN;") == 1, path.name
        assert body.count("COMMIT;") == 1, path.name


def test_migrations_are_numbered_without_gaps_or_collisions() -> None:
    """Two migrations sharing a number apply in an order nobody chose."""
    numbers = sorted(int(path.name.split("_")[0]) for path in MIGRATIONS.glob("*.sql"))
    assert numbers == list(range(1, len(numbers) + 1))
