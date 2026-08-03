"""The video sandbox: identity metrics, registry discipline, and isolation.

These run without footage, without torch, and without a GPU. That is deliberate -- the gate
that decides whether the vision track is worth funding should be testable long before anyone
installs a 2 GB dependency.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

SANDBOX_SRC = Path(__file__).resolve().parents[2] / "services" / "video_intelligence" / "src"
sys.path.insert(0, str(SANDBOX_SRC))

from wnba_video_intelligence import (  # noqa: E402
    Association,
    evaluate_tracking,
    load_registry,
)


def seq(gt_id: str, preds: Sequence[str | None], start: int = 0) -> list[Association]:
    """One ground-truth object observed over consecutive frames."""
    return [Association(frame=start + i, gt_id=gt_id, pred_id=p) for i, p in enumerate(preds)]


# --------------------------------------------------------------------------------------
# Identity switches -- the go/no-go metric
# --------------------------------------------------------------------------------------
def test_a_perfectly_tracked_object_has_no_switches() -> None:
    report = evaluate_tracking(seq("wilson", ["t1"] * 10))
    assert report.id_switches == 0
    assert report.recall == 1.0
    assert report.mostly_tracked == 1
    assert report.verdict().startswith("PASS")


def test_a_single_identity_swap_is_counted() -> None:
    report = evaluate_tracking(seq("wilson", ["t1"] * 5 + ["t2"] * 5))
    assert report.id_switches == 1
    assert report.switches_by_gt == {"wilson": 1}


def test_switch_after_occlusion_is_not_forgiven() -> None:
    """The failure mode broadcast footage actually produces.

    A player is occluded under the basket and re-emerges with a new id. Resetting identity
    memory across the gap would score this as clean, which would flatter the tracker in
    exactly the situation that matters most.
    """
    report = evaluate_tracking(seq("wilson", ["t1", "t1", None, None, "t2", "t2"]))
    assert report.id_switches == 1, "an id change across an occlusion gap is still a switch"
    assert report.fragmentations == 1
    assert report.missed_observations == 2


def test_gap_without_identity_change_is_a_fragmentation_not_a_switch() -> None:
    """Different faults, different fixes: gaps blame the detector, switches the associator."""
    report = evaluate_tracking(seq("wilson", ["t1", "t1", None, "t1", "t1"]))
    assert report.id_switches == 0
    assert report.fragmentations == 1


def test_two_players_swapping_identities_counts_two_switches() -> None:
    """The classic crossing failure: both objects are wrong afterwards, not one."""
    associations = seq("wilson", ["t1", "t1", "t2", "t2"]) + seq(
        "collier", ["t2", "t2", "t1", "t1"]
    )
    report = evaluate_tracking(associations)
    assert report.gt_objects == 2
    assert report.id_switches == 2


def test_switch_rate_is_per_observation_not_per_clip() -> None:
    """Otherwise a long clip looks better than a short one for no real reason."""
    short = evaluate_tracking(seq("a", ["t1"] * 5 + ["t2"] * 5))
    long = evaluate_tracking(seq("a", ["t1"] * 50 + ["t2"] * 50))
    assert short.id_switches == long.id_switches == 1
    assert long.id_switch_rate < short.id_switch_rate


def test_a_thoroughly_confused_track_is_mostly_lost() -> None:
    """Found in every frame, but under six different ids. That is not tracking."""
    report = evaluate_tracking(seq("wilson", ["t1", "t2", "t3", "t4", "t5", "t6"]))
    assert report.recall == 1.0
    assert report.mostly_lost == 1
    assert report.mostly_tracked == 0
    assert report.verdict().startswith("FAIL")


# --------------------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------------------
def test_verdict_fails_on_poor_recall_even_with_stable_identity() -> None:
    report = evaluate_tracking(seq("wilson", ["t1", None, None, None, None]))
    assert report.id_switches == 0
    assert report.recall < 0.9
    assert report.verdict().startswith("FAIL")


def test_verdict_reports_no_data_rather_than_passing_vacuously() -> None:
    """An empty result must never read as success."""
    report = evaluate_tracking(seq("wilson", [None, None, None]))
    assert report.verdict().startswith("NO DATA")


def test_report_summary_is_human_readable() -> None:
    report = evaluate_tracking(seq("wilson", ["t1"] * 8 + ["t2"] * 2))
    assert "idsw=1" in report.summary()


# --------------------------------------------------------------------------------------
# Registry discipline
# --------------------------------------------------------------------------------------
def test_registry_loads_with_thresholds_declared_in_advance() -> None:
    specs = load_registry()
    assert len(specs) >= 8
    for spec in specs:
        assert spec.metric, f"{spec.name} declares no metric"
        assert spec.labels, f"{spec.name} declares no required labels"
        assert spec.is_experimental


def test_registry_refuses_to_become_a_production_dependency(tmp_path: Path) -> None:
    """The isolation guarantee, stated in the data and enforced on load."""
    bad = tmp_path / "feature_registry.yaml"
    bad.write_text(
        "version: 1\nproduction_dependency: true\nfeatures:\n"
        "  - name: x\n    phase: 1\n    labels: [a]\n"
        "    metric: hota\n    promotion_threshold: 0.5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="may never become a hard dependency"):
        load_registry(bad)


# --------------------------------------------------------------------------------------
# Isolation
# --------------------------------------------------------------------------------------
def test_production_code_never_imports_the_video_sandbox() -> None:
    """The sandbox may import the platform's ideas. The platform may not import the sandbox.

    A speculative sensor that production depends on is no longer speculative -- it is an
    outage waiting for a model download to fail.
    """
    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for area in ("packages", "services/wnba_services", "apps"):
        for path in (root / area).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "wnba_video_intelligence" in text:
                offenders.append(str(path.relative_to(root)))
    assert not offenders, f"production code imports the video sandbox: {offenders}"


def test_sandbox_is_excluded_from_the_uv_workspace() -> None:
    """Installing the platform must not drag in torch, ultralytics or OpenCV."""
    pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'exclude = ["services/video_intelligence"]' in pyproject


def test_sandbox_imports_without_the_vision_stack_installed() -> None:
    """Its metrics must be runnable before anyone commits to a 2 GB dependency."""
    result = subprocess.run(
        [sys.executable, "-c", "import wnba_video_intelligence as v; print(v.__version__)"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(SANDBOX_SRC), "PATH": ""},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "0.1.0" in result.stdout
