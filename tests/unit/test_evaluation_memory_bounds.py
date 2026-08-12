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
