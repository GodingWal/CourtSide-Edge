"""P2-2 / P2-3 unit tests: line snapshots, adverse-move revalidation, and the
calibration / CLV pick log."""
import fakeredis
import pytest

from shared import db as shared_db
from shared.picks.calibration import (
    brier_score,
    clv,
    log_pick,
    record_result,
    reliability_buckets,
    render_markdown,
    run_weekly_report,
    weekly_report,
)
from shared.picks.line_tracking import (
    LineHistory,
    is_adverse_move,
    revalidate_at_publish,
)
from shared.picks.models import PickStatus, Recommendation
from shared.picks.test_validation import make_payload, make_pick

# ── P2-2: line snapshots + adverse movement ──────────────────────────────────

def test_adverse_move_direction_and_threshold():
    assert is_adverse_move(Recommendation.BUY, 19.5, 20.5, 0.5)       # line up on a Buy
    assert not is_adverse_move(Recommendation.BUY, 19.5, 19.0, 0.5)   # favorable
    assert is_adverse_move(Recommendation.SELL, 19.5, 18.5, 0.5)      # line down on a Sell
    assert not is_adverse_move(Recommendation.SELL, 19.5, 19.8, 0.5)
    assert not is_adverse_move(Recommendation.BUY, 19.5, 19.9, 0.5)   # under threshold


def test_buy_revalidated_against_moved_line_and_demoted():
    """Spec acceptance: 19.5 -> 20.5 on a Buy between capture and publish is
    re-validated against 20.5; here the new line drops the hit probability
    below threshold, so the pick demotes to LEAN."""
    pick = make_pick()  # projection 21.5, std 5.5, line 19.5, hp 0.65
    result = revalidate_at_publish(pick, 20.5)
    assert result["adverse"] and result["demoted"]
    assert result["status"] == PickStatus.LEAN
    assert result["pick"].line == 20.5
    assert result["pick"].hit_probability < pick.hit_probability
    assert "ADVERSE_LINE_MOVE" in result["pick"].flags
    assert result["capture_line"] == 19.5


def test_strong_pick_survives_adverse_move():
    pick = make_pick(projection=26.0, hit_probability=0.80)
    result = revalidate_at_publish(pick, 20.5)
    assert result["adverse"] and not result["demoted"]
    assert result["status"] == PickStatus.PUBLISHABLE
    assert "ADVERSE_LINE_MOVE" in result["pick"].flags


def test_favorable_move_passes_through_unchanged():
    pick = make_pick()
    result = revalidate_at_publish(pick, 19.0)
    assert not result["adverse"]
    assert result["pick"] is pick


def test_line_history_snapshots_are_timestamped_and_age_gated():
    client = fakeredis.FakeRedis(decode_responses=True)
    history = LineHistory(client)
    history.record("prizepicks", "Caitlin Clark", "PTS", 19.5, ts=1000.0)
    history.record("prizepicks", "Caitlin Clark", "PTS", 20.0, ts=1600.0)

    latest = history.latest("prizepicks", "Caitlin Clark", "PTS")
    assert latest["line"] == 20.0 and latest["ts"] == 1600.0
    # Within the 15-minute window: usable. Beyond it: treated as missing.
    assert history.latest("prizepicks", "Caitlin Clark", "PTS",
                          max_age_minutes=15, now=1600.0 + 14 * 60) is not None
    assert history.latest("prizepicks", "Caitlin Clark", "PTS",
                          max_age_minutes=15, now=1600.0 + 16 * 60) is None


# ── P2-3: calibration + CLV logging ──────────────────────────────────────────

def test_clv_signed_by_pick_direction():
    assert clv("Buy", 19.5, 20.5) == pytest.approx(1.0)    # market moved toward the over
    assert clv("Buy", 19.5, 18.5) == pytest.approx(-1.0)
    assert clv("Sell", 19.5, 18.5) == pytest.approx(1.0)   # market moved toward the under
    assert clv("Sell", 19.5, 20.5) == pytest.approx(-1.0)


def test_pick_log_is_scored_against_actuals(tmp_path):
    db_path = str(tmp_path / "picks.db")
    pick = make_pick()
    with shared_db.transaction(db_path) as conn:
        log_pick(conn, pick, "PUBLISHED", payload=make_payload())
        log_pick(conn, make_pick(pick_id="p2", hit_probability=0.61), "LEAN",
                 reason_codes=("BELOW_THRESHOLD",))
        record_result(conn, "p1", actual_value=24.0, line_close=20.5)

        row = conn.execute(
            "SELECT hit, clv, actual_value, line_close, payload_hash, model_version "
            "FROM pick_log WHERE pick_id = 'p1'").fetchone()
    hit, clv_value, actual, close, digest, version = row
    assert hit == 1 and actual == 24.0 and close == 20.5
    assert clv_value == pytest.approx(1.0)
    assert digest and len(digest) == 64  # full payload snapshot hash
    assert version == "unversioned"     # model version stamped on every pick


def test_brier_and_reliability():
    rows = [(0.65, 1), (0.65, 1), (0.65, 0), (0.55, 0), (0.55, 1), (0.85, 1)]
    assert brier_score(rows) == pytest.approx(
        sum((p - h) ** 2 for p, h in rows) / len(rows))
    buckets = reliability_buckets(rows)
    by_name = {b["bucket"]: b for b in buckets}
    assert by_name["0.6-0.7"]["n"] == 3
    assert by_name["0.6-0.7"]["hit_rate"] == pytest.approx(2 / 3, abs=1e-4)


def test_weekly_report_and_markdown(tmp_path):
    db_path = str(tmp_path / "picks.db")
    with shared_db.transaction(db_path) as conn:
        for i, (hp, actual) in enumerate([(0.65, 24.0), (0.62, 15.0), (0.70, 22.0)]):
            log_pick(conn, make_pick(pick_id=f"p{i}", hit_probability=hp), "PUBLISHED")
            record_result(conn, f"p{i}", actual_value=actual, line_close=20.0)
        log_pick(conn, make_pick(pick_id="r1"), "REJECTED",
                 reason_codes=("EDGE_SIGN_MISMATCH",))
        log_pick(conn, make_pick(pick_id="r2"), "LEAN",
                 reason_codes=("BELOW_THRESHOLD",))

        report = weekly_report(conn)
    assert report["n_scored"] == 3
    assert report["brier"] is not None
    assert report["avg_clv"] == pytest.approx(0.5)  # Buys: closes at 20.0 vs 19.5
    assert report["rejection_counts"] == {"EDGE_SIGN_MISMATCH": 1,
                                          "BELOW_THRESHOLD": 1}

    out_path = str(tmp_path / "report.md")
    run_weekly_report(db_path, out_path)
    with open(out_path, encoding="utf-8") as fh:
        markdown = fh.read()
    assert "EDGE_SIGN_MISMATCH | 1" in markdown
    assert "Brier" in render_markdown(report)
