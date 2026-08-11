"""Deployment configuration protects core forecasts and exposes failed background jobs."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_shadow_simulation_cannot_fail_the_core_forecast_service() -> None:
    forecast = (ROOT / "infrastructure/systemd/wnba-forecast.service").read_text(encoding="utf-8")
    simulation = (ROOT / "infrastructure/systemd/wnba-game-simulation.service").read_text(
        encoding="utf-8"
    )
    assert "forecast run" in forecast
    assert "simulate-games" not in forecast
    assert "simulate-games" in simulation


def test_monitor_checks_last_results_not_only_active_timers() -> None:
    monitor = (ROOT / "infrastructure/monitor_health.sh").read_text(encoding="utf-8")
    assert "wnba-game-simulation.timer" in monitor
    assert 'systemctl show "$service" --property=Result' in monitor
    for unit in ("wnba-settlement.service", "wnba-trust-fit.service"):
        assert unit in monitor


def test_deploy_smoke_runs_production_only_learning_sql() -> None:
    deploy = (ROOT / "infrastructure/deploy_courtside.sh").read_text(encoding="utf-8")
    settlement = deploy.index("systemctl start wnba-settlement.service")
    trust = deploy.index("systemctl start wnba-trust-fit.service")
    simulation = deploy.index("systemctl start wnba-game-simulation.service")
    monitor = deploy.index("infrastructure/monitor_health.sh")
    assert settlement < trust < simulation < monitor
