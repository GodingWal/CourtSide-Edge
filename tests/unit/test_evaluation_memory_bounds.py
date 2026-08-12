"""Full-history learners must bound refresh duplication before rows reach Python."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_error_attribution_backfill_is_incremental_and_lean() -> None:
    source = (ROOT / "services/wnba_services/learning_loop/evaluation.py").read_text(
        encoding="utf-8"
    )
    assert "NOT EXISTS (\n                   SELECT 1 FROM wnba.error_attributions" in source
    assert "LIMIT 2000" in source
    assert "SELECT d.*,o.*" not in source


def test_model_evaluation_deduplicates_refreshes_inside_postgres() -> None:
    source = (ROOT / "services/wnba_services/learning_loop/evaluation.py").read_text(
        encoding="utf-8"
    )
    assert "SELECT DISTINCT ON (d.player_id,d.game_id,d.prop_type)" in source
    assert "fc.component_name,d.player_id,d.game_id,d.prop_type" in source
    assert "SELECT fc.*" not in source


def test_parameter_fitting_deduplicates_before_fetching() -> None:
    source = (ROOT / "services/wnba_services/forecasting/fitting.py").read_text(encoding="utf-8")
    assert source.count("SELECT DISTINCT ON") >= 2


def test_unresolved_outcome_backfill_is_bounded_and_lean() -> None:
    source = (ROOT / "services/wnba_services/learning_loop/settlement.py").read_text(
        encoding="utf-8"
    )
    assert "LIMIT 2000" in source
    assert "SELECT d.*" not in source


def test_correlation_self_join_deduplicates_refreshes_first() -> None:
    source = (ROOT / "services/wnba_services/market_engine/correlation.py").read_text(
        encoding="utf-8"
    )
    start = source.index("def _settled_pair_observations")
    end = source.index("def _cleared", start)
    query = source[start:end]
    assert "WITH settled AS" in query
    assert "SELECT DISTINCT ON (d.player_id,d.game_id,d.prop_type)" in query
    assert "b.episode_id>a.episode_id" not in query
