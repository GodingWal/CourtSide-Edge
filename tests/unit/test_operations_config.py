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


def test_pick_screenshot_uses_the_file_picker_instead_of_forcing_a_camera() -> None:
    page = (ROOT / "apps/wnba_apps/api/static/index.html").read_text(encoding="utf-8")
    screenshot_input = page.split('id="pickScreenshot"', 1)[1].split(">", 1)[0]
    assert 'type="file"' in screenshot_input
    assert 'accept="image/png,image/jpeg,image/webp"' in screenshot_input
    assert "capture=" not in screenshot_input


def test_analysis_board_has_responsive_controls_and_resets_filtered_scroll() -> None:
    page = (ROOT / "apps/wnba_apps/api/static/index.html").read_text(encoding="utf-8")
    styles = (ROOT / "apps/wnba_apps/api/static/app.css").read_text(encoding="utf-8")
    enhancements = (ROOT / "apps/wnba_apps/api/static/site_enhancements.js").read_text(
        encoding="utf-8"
    )
    assert 'class="split today-layout"' in page
    assert ".today-layout .filters{display:grid" in styles
    assert "@media(max-width:1450px) and (min-width:1101px)" in styles
    assert ".forecasttable{min-width:940px" in styles
    assert '$(".forecastwrap").scrollTop = 0' in enhancements
    assert "forecastStatusLabel" in enhancements


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


def test_deploy_moves_the_old_deepseek_default_to_flash_without_overwriting_custom_models() -> None:
    deploy = (ROOT / "infrastructure/deploy_courtside.sh").read_text(encoding="utf-8")
    assert "grep -qx 'DEEPSEEK_MODEL=deepseek-v4-pro'" in deploy
    assert "DEEPSEEK_MODEL=deepseek-v4-flash" in deploy


def test_deploy_provisions_writable_screenshot_storage_for_the_web_user() -> None:
    deploy = (ROOT / "infrastructure/deploy_courtside.sh").read_text(encoding="utf-8")
    assert "install -d -o wnba -g wnba -m 0750 /var/lib/wnba/uploads" in deploy


def test_proxy_accepts_the_advertised_screenshot_limit_and_deploys_safely() -> None:
    policy = (ROOT / "infrastructure/nginx/courtside-upload.conf").read_text(encoding="utf-8")
    deploy = (ROOT / "infrastructure/deploy_courtside.sh").read_text(encoding="utf-8")
    app = (ROOT / "apps/wnba_apps/api/static/app.js").read_text(encoding="utf-8")
    assert "client_max_body_size 14m;" in policy
    assert "/etc/nginx/conf.d/courtside-upload.conf" in deploy
    assert "nginx -t" in deploy
    assert "systemctl reload nginx" in deploy
    assert "sites-available" not in deploy
    assert 'type.includes("application/json")' in app
