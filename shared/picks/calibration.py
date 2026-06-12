"""Calibration + CLV logging (P2-3).

Every pick — published, LEAN, or rejected — is appended to the `pick_log`
table with full lineage: model version, payload hash + snapshot,
{mean, std, hit_probability}, capture line, closing line, recommendation,
entry context, and (once scored) the actual result. CLV is the primary KPI;
raw hit rate over small samples is noise.
"""
import hashlib
import json
from datetime import datetime, timezone

from shared import db as shared_db
from shared.picks.models import NarrativePayload, Pick, Recommendation

PICK_LOG_TABLE = f"""
CREATE TABLE IF NOT EXISTS pick_log (
    id {shared_db.AUTO_PK},
    pick_id TEXT NOT NULL,
    logged_at TEXT NOT NULL,
    model_version TEXT NOT NULL,
    player TEXT NOT NULL,
    stat TEXT NOT NULL,
    book TEXT NOT NULL,
    entry_type TEXT NOT NULL,
    legs INTEGER NOT NULL,
    recommendation TEXT NOT NULL,
    projection REAL NOT NULL,
    std REAL,
    line_capture REAL NOT NULL,
    line_close REAL,
    hit_probability REAL NOT NULL,
    breakeven_probability REAL NOT NULL,
    status TEXT NOT NULL,
    reason_codes TEXT NOT NULL,
    flags TEXT NOT NULL,
    grade TEXT,
    payload_hash TEXT,
    payload_json TEXT,
    actual_value REAL,
    hit INTEGER,
    clv REAL
)
"""


def payload_hash(payload: NarrativePayload | dict | None) -> str | None:
    if payload is None:
        return None
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def ensure_pick_log(conn) -> None:
    conn.execute(PICK_LOG_TABLE)


def log_pick(conn, pick: Pick, status: str,
             payload: NarrativePayload | dict | None = None,
             reason_codes: tuple[str, ...] = (),
             logged_at: str | None = None) -> None:
    """Append-only: one row per pick per run, never updated except scoring."""
    ensure_pick_log(conn)
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload
    conn.execute(
        """INSERT INTO pick_log
           (pick_id, logged_at, model_version, player, stat, book, entry_type,
            legs, recommendation, projection, std, line_capture,
            hit_probability, breakeven_probability, status, reason_codes,
            flags, grade, payload_hash, payload_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            pick.pick_id,
            logged_at or datetime.now(timezone.utc).isoformat(),
            pick.model_version,
            pick.player,
            pick.stat,
            pick.book,
            pick.entry_type,
            pick.legs,
            pick.recommendation.value,
            pick.projection,
            pick.std,
            pick.line,
            pick.hit_probability,
            pick.breakeven_probability,
            status,
            json.dumps(list(reason_codes)),
            json.dumps(list(pick.flags)),
            pick.grade,
            payload_hash(payload),
            json.dumps(data) if data is not None else None,
        ),
    )


def clv(recommendation: str, line_capture: float, line_close: float) -> float:
    """Closing line minus capture line, signed by pick direction: positive
    means the market moved toward the pick after capture."""
    delta = line_close - line_capture
    return delta if recommendation == Recommendation.BUY.value else -delta


def record_result(conn, pick_id: str, actual_value: float,
                  line_close: float) -> None:
    """Score every logged row for this pick against the actual result."""
    rows = conn.execute(
        "SELECT id, recommendation, line_capture FROM pick_log WHERE pick_id = ?",
        (pick_id,),
    ).fetchall()
    for row_id, recommendation, line_capture in rows:
        if actual_value == line_capture:
            hit = None  # push
        elif recommendation == Recommendation.BUY.value:
            hit = int(actual_value > line_capture)
        else:
            hit = int(actual_value < line_capture)
        conn.execute(
            "UPDATE pick_log SET actual_value = ?, line_close = ?, hit = ?, clv = ? WHERE id = ?",
            (actual_value, line_close, hit,
             round(clv(recommendation, line_capture, line_close), 4), row_id),
        )


def brier_score(rows: list[tuple[float, int]]) -> float | None:
    """Mean squared error of hit probabilities vs outcomes. Target <= 0.24."""
    if not rows:
        return None
    return sum((p - hit) ** 2 for p, hit in rows) / len(rows)


def reliability_buckets(rows: list[tuple[float, int]], buckets: int = 10) -> list[dict]:
    """Predicted-probability deciles vs realized hit rate (calibration plot data)."""
    out = []
    for b in range(buckets):
        lo, hi = b / buckets, (b + 1) / buckets
        in_bucket = [(p, h) for p, h in rows if lo <= p < hi or (b == buckets - 1 and p == hi)]
        if not in_bucket:
            continue
        out.append({
            "bucket": f"{lo:.1f}-{hi:.1f}",
            "n": len(in_bucket),
            "mean_predicted": round(sum(p for p, _ in in_bucket) / len(in_bucket), 4),
            "hit_rate": round(sum(h for _, h in in_bucket) / len(in_bucket), 4),
        })
    return out


def weekly_report(conn, model_version: str | None = None) -> dict:
    """Calibration + CLV + per-reason-code rejection counts, optionally
    filtered to one model version (calibration is reportable per version)."""
    ensure_pick_log(conn)
    where, params = "", ()
    if model_version:
        where, params = " AND model_version = ?", (model_version,)

    scored = conn.execute(
        "SELECT hit_probability, hit FROM pick_log WHERE hit IS NOT NULL" + where,
        params,
    ).fetchall()
    rows = [(float(p), int(h)) for p, h in scored]

    clv_rows = conn.execute(
        "SELECT clv FROM pick_log WHERE clv IS NOT NULL AND status = 'PUBLISHED'" + where,
        params,
    ).fetchall()
    clvs = [float(r[0]) for r in clv_rows]

    reason_rows = conn.execute(
        "SELECT reason_codes FROM pick_log WHERE reason_codes != '[]'" + where, params
    ).fetchall()
    rejection_counts: dict[str, int] = {}
    for (raw,) in reason_rows:
        for code in json.loads(raw):
            rejection_counts[code] = rejection_counts.get(code, 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_version": model_version or "all",
        "n_scored": len(rows),
        "brier": round(brier_score(rows), 4) if rows else None,
        "reliability": reliability_buckets(rows),
        "n_published_with_clv": len(clvs),
        "avg_clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
        "rejection_counts": rejection_counts,
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Weekly pick calibration & CLV report",
        f"- Generated: {report['generated_at']}",
        f"- Model version: {report['model_version']}",
        f"- Scored picks: {report['n_scored']}",
        f"- Brier score: {report['brier']} (target <= 0.24)",
        f"- Avg CLV (published): {report['avg_clv']} over {report['n_published_with_clv']} picks",
        "",
        "## Reliability",
        "| Bucket | n | Mean predicted | Hit rate |",
        "|---|---|---|---|",
    ]
    for bucket in report["reliability"]:
        lines.append(
            f"| {bucket['bucket']} | {bucket['n']} | {bucket['mean_predicted']} "
            f"| {bucket['hit_rate']} |"
        )
    lines += ["", "## Rejections by reason code", "| Code | Count |", "|---|---|"]
    for code, count in sorted(report["rejection_counts"].items()):
        lines.append(f"| {code} | {count} |")
    return "\n".join(lines)


def run_weekly_report(db_path: str, out_path: str | None = None,
                      model_version: str | None = None) -> dict:
    """Job entrypoint (cron or mesh-scheduled): build the report and write
    the markdown artifact next to the database unless out_path is given."""
    with shared_db.transaction(db_path) as conn:
        report = weekly_report(conn, model_version)
    if out_path:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(render_markdown(report))
    return report
