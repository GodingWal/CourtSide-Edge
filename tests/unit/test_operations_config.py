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
    assert "ExecMainStartTimestampMonotonic" in monitor
    for unit in ("wnba-settlement.service", "wnba-trust-fit.service"):
        assert unit in monitor

    trust_unit = (ROOT / "infrastructure/systemd/wnba-trust-fit.service").read_text(
        encoding="utf-8"
    )
    assert "RemainAfterExit=yes" in trust_unit


def test_settlement_releases_memory_between_ordered_phases() -> None:
    service = (ROOT / "infrastructure/systemd/wnba-settlement.service").read_text(encoding="utf-8")
    commands = (
        "learning settle-outcomes",
        "learning evaluate",
        "learning fit",
        "learning settle-picks",
        "learning fit-correlations",
    )
    positions = [service.index(command) for command in commands]
    assert positions == sorted(positions)
    assert "learning settle\n" not in service
    assert service.count("learning settle-outcomes") == 5
    assert service.index("learning void-unsupported") < positions[0]


def test_liveness_uses_the_deployed_virtual_environment() -> None:
    service = (ROOT / "infrastructure/systemd/wnba-liveness.service").read_text(encoding="utf-8")
    assert "ExecStart=/opt/wnba/repo/.venv/bin/wnba monitor liveness" in service
    assert "ExecStartPost=/usr/bin/rm -f /var/lib/wnba/last_liveness_failure" in service
    assert "/root/" not in service


def test_resolved_incidents_are_not_rendered_as_active_errors() -> None:
    app = (ROOT / "apps/wnba_apps/api/static/app.js").read_text(encoding="utf-8")
    styles = (ROOT / "apps/wnba_apps/api/static/app.css").read_text(encoding="utf-8")
    assert "Boolean(a.resolved_at)-Boolean(b.resolved_at)" in app
    assert "x.resolved_at?'ok':x.blocks_recommendations?'bad':'warn'" in app
    assert "resolved-incident" in app
    assert ".listitem.resolved-incident" in styles


def test_deploy_smoke_runs_production_only_learning_sql() -> None:
    deploy = (ROOT / "infrastructure/deploy_courtside.sh").read_text(encoding="utf-8")
    reset = deploy.index("systemctl reset-failed wnba-forecast.service")
    forecast = deploy.index("systemctl restart wnba-forecast.service")
    settlement = deploy.index("systemctl restart wnba-settlement.service")
    trust = deploy.index("systemctl restart wnba-trust-fit.service")
    simulation = deploy.index("systemctl restart wnba-game-simulation.service")
    monitor = deploy.index("infrastructure/monitor_health.sh")
    assert reset < forecast < settlement < trust < simulation < monitor


def test_deploy_reexecutes_once_when_it_updates_itself() -> None:
    deploy = (ROOT / "infrastructure/deploy_courtside.sh").read_text(encoding="utf-8")
    assert "COURTSIDE_DEPLOY_REEXEC:-0" in deploy
    assert "git diff --quiet" in deploy
    assert 'exec /bin/bash "$REPO/infrastructure/deploy_courtside.sh"' in deploy
    assert deploy.index("backup_postgres.sh") < deploy.index("git fetch origin main")
