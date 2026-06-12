"""Rejection triage: a bounded agentic loop as an ANALYST, never a trader.

When rejection volume spikes on the pick pipeline, an LLM investigates with a
small whitelist of read-only tools (rejection counts, payload samples, pick
log slices, feed freshness, agent heartbeats) and writes a markdown diagnosis
for humans. Hard guardrails, in order of importance:

1. Output is a report on `recent:triage_reports` — there is no code path from
   this module to any picks.* channel, the pick log's status fields, or
   anything else the publisher reads.
2. Tools are read-only and dispatched through an explicit whitelist; the
   model names a tool, it never names code.
3. The loop is bounded: `max_steps` tool calls, then it must conclude. Two
   malformed responses in a row abort the loop.
4. Every step is recorded; if the loop aborts (or no LLM is reachable) a
   deterministic baseline report is produced instead, so triage degrades to
   "facts without narrative" rather than to silence.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from shared import db as shared_db
from shared.picks.calibration import ensure_pick_log
from shared.picks.channels import RECENT_REJECTED_KEY

OBSERVATION_CHAR_LIMIT = 1500
HEARTBEAT_PREFIX = "heartbeat:agent:"

TRIAGE_SYSTEM_PROMPT = (
    "You are the rejection-triage analyst for a sports betting pick pipeline. "
    "Picks are rejected with reason codes (EDGE_SIGN_MISMATCH, STALE_INJURY_DATA, "
    "FABRICATED_NUMERIC, BELOW_THRESHOLD, ...); your job is to diagnose WHY a "
    "spike in rejections is happening — validator drift, an upstream data feed "
    "failure, a dead agent, or a genuine model regression — using ONLY the "
    "provided read-only tools. You never recommend bets and never produce picks; "
    "your output is a diagnostic report for humans.\n"
    "Respond with ONLY a JSON object, no prose:\n"
    '  {"action": "tool", "tool": "<tool_name>", "args": {...}}   to investigate\n'
    '  {"action": "final", "report": "<markdown diagnosis>"}      when done\n'
    "Cite tool observations in the report; do not assert facts you did not observe."
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Read-only toolbox ────────────────────────────────────────────────────────

class TriageToolbox:
    """The complete set of actions the analyst loop can take.

    Every tool reads existing state (Redis keys written by other agents, the
    pick_log table) and returns JSON-serializable data. Dispatch goes through
    `call()`, which only resolves names registered in TOOLS.
    """

    TOOLS = {
        "rejection_counts": "rejection_counts(hours: int = 24) — rejected-pick "
                            "counts by reason code from the pick log over the window",
        "rejection_buckets": "rejection_buckets() — per-hour rejection counts by "
                             "reason code accumulated from the live picks.rejected stream",
        "sample_rejections": "sample_rejections(reason_code: str = '', limit: int = 5) — "
                             "most recent rejection events (payload snapshots), optionally "
                             "filtered by reason code",
        "pick_status_counts": "pick_status_counts(hours: int = 24) — pick log counts "
                              "by status (PUBLISHED/LEAN/REJECTED/...) over the window",
        "injury_feed_freshness": "injury_feed_freshness() — whether the injury report "
                                 "cache exists and the age of every roster-store record",
        "agent_heartbeats": "agent_heartbeats() — liveness of every agent "
                            "(seconds since last heartbeat)",
    }

    def __init__(self, redis_client, db_path: str, now: datetime | None = None):
        self.redis = redis_client
        self.db_path = db_path
        self.now = now or _utcnow()

    def call(self, name: str, args: dict) -> dict:
        if name not in self.TOOLS:
            return {"error": f"unknown tool '{name}'; available: {sorted(self.TOOLS)}"}
        try:
            return getattr(self, name)(**(args or {}))
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}"}
        except Exception as exc:  # tool failures are observations, not crashes
            return {"error": f"{name} failed: {exc}"}

    def describe(self) -> str:
        return "\n".join(f"- {sig}" for sig in self.TOOLS.values())

    # ── tools ────────────────────────────────────────────────────────────

    def rejection_counts(self, hours: int = 24) -> dict:
        cutoff = (self.now - timedelta(hours=hours)).isoformat()
        counts: dict[str, int] = {}
        with shared_db.transaction(self.db_path) as conn:
            ensure_pick_log(conn)
            rows = conn.execute(
                "SELECT reason_codes FROM pick_log "
                "WHERE logged_at >= ? AND reason_codes != '[]'",
                (cutoff,),
            ).fetchall()
        for (raw,) in rows:
            for code in json.loads(raw):
                counts[code] = counts.get(code, 0) + 1
        return {"window_hours": hours, "counts": counts}

    def rejection_buckets(self) -> dict:
        buckets = {}
        for key in sorted(self.redis.keys(f"{REJECTION_BUCKET_PREFIX}*")):
            hour = key.removeprefix(REJECTION_BUCKET_PREFIX)
            buckets[hour] = {code: int(n) for code, n in self.redis.hgetall(key).items()}
        return {"buckets": buckets}

    def sample_rejections(self, reason_code: str = "", limit: int = 5) -> dict:
        samples = []
        for raw in self.redis.lrange(RECENT_REJECTED_KEY, 0, 49):
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if reason_code and reason_code not in (event.get("reason_codes") or []):
                continue
            samples.append(event)
            if len(samples) >= max(1, min(int(limit), 10)):
                break
        return {"reason_code": reason_code or "any", "samples": samples}

    def pick_status_counts(self, hours: int = 24) -> dict:
        cutoff = (self.now - timedelta(hours=hours)).isoformat()
        with shared_db.transaction(self.db_path) as conn:
            ensure_pick_log(conn)
            rows = conn.execute(
                "SELECT status, COUNT(*) FROM pick_log WHERE logged_at >= ? "
                "GROUP BY status",
                (cutoff,),
            ).fetchall()
        return {"window_hours": hours, "counts": {status: int(n) for status, n in rows}}

    def injury_feed_freshness(self) -> dict:
        report_cached = bool(self.redis.exists("injuries:report"))
        record_ages = {}
        for name, raw in (self.redis.hgetall("roster:players") or {}).items():
            try:
                ts = json.loads(raw).get("ts")
                if ts:
                    record_ages[name] = round(self.now.timestamp() - float(ts))
            except (json.JSONDecodeError, TypeError, ValueError):
                record_ages[name] = None
        return {"injury_report_cached": report_cached,
                "roster_record_age_seconds": record_ages}

    def agent_heartbeats(self) -> dict:
        beats = {}
        for key in self.redis.keys(f"{HEARTBEAT_PREFIX}*"):
            agent = key.removeprefix(HEARTBEAT_PREFIX)
            raw = self.redis.get(key)
            try:
                beats[agent] = round(self.now.timestamp() - float(raw))
            except (TypeError, ValueError):
                beats[agent] = None
        return {"seconds_since_heartbeat": beats}


# ── Rejection accumulation + spike detection (deterministic) ─────────────────

REJECTION_BUCKET_PREFIX = "triage:rejections:"
BUCKET_TTL_SECONDS = 48 * 3600


def record_rejection(redis_client, event: dict, now: datetime | None = None) -> None:
    """Accumulate one picks.rejected event into its hour bucket (TTL 48h)."""
    hour = (now or _utcnow()).strftime("%Y-%m-%dT%H")
    key = f"{REJECTION_BUCKET_PREFIX}{hour}"
    for code in event.get("reason_codes") or [event.get("reason_code") or "UNKNOWN"]:
        redis_client.hincrby(key, code, 1)
    redis_client.expire(key, BUCKET_TTL_SECONDS)


def detect_spikes(redis_client, now: datetime | None = None,
                  baseline_hours: int = 24, factor: float = 3.0,
                  min_count: int = 5) -> list[dict]:
    """Reason codes whose current-hour rate exceeds `factor` x the trailing
    per-hour baseline (and at least `min_count` events this hour)."""
    now = now or _utcnow()
    current_key = f"{REJECTION_BUCKET_PREFIX}{now.strftime('%Y-%m-%dT%H')}"
    current = {c: int(n) for c, n in (redis_client.hgetall(current_key) or {}).items()}

    baseline_totals: dict[str, int] = {}
    for offset in range(1, baseline_hours + 1):
        hour = (now - timedelta(hours=offset)).strftime("%Y-%m-%dT%H")
        for code, n in (redis_client.hgetall(f"{REJECTION_BUCKET_PREFIX}{hour}") or {}).items():
            baseline_totals[code] = baseline_totals.get(code, 0) + int(n)

    spikes = []
    for code, count in current.items():
        per_hour_baseline = baseline_totals.get(code, 0) / baseline_hours
        if count >= min_count and count > factor * max(per_hour_baseline, 0.5):
            spikes.append({"reason_code": code, "current_hour": count,
                           "baseline_per_hour": round(per_hour_baseline, 2)})
    return sorted(spikes, key=lambda s: -s["current_hour"])


def baseline_report(toolbox: TriageToolbox, focus: str) -> str:
    """Deterministic facts-only report: the fallback when no LLM is reachable
    or the agentic loop aborts. Facts, no narrative."""
    counts = toolbox.rejection_counts()
    statuses = toolbox.pick_status_counts()
    beats = toolbox.agent_heartbeats()
    lines = [
        "# Rejection triage (deterministic baseline)",
        f"- Generated: {toolbox.now.isoformat()}",
        f"- Focus: {focus}",
        "",
        "## Rejections by reason code (24h)",
    ]
    for code, n in sorted(counts["counts"].items(), key=lambda kv: -kv[1]) or [("none", 0)]:
        lines.append(f"- {code}: {n}")
    lines += ["", "## Pick statuses (24h)"]
    for status, n in sorted(statuses["counts"].items()):
        lines.append(f"- {status}: {n}")
    lines += ["", "## Agent heartbeats (seconds since last beat)"]
    for agent, age in sorted(beats["seconds_since_heartbeat"].items()) or [("none", None)]:
        lines.append(f"- Agent {agent}: {age}")
    lines += ["", "_No LLM diagnosis available; facts only._"]
    return "\n".join(lines)


# ── Bounded agentic loop ─────────────────────────────────────────────────────

@dataclass
class TriageStep:
    step: int
    tool: str
    args: dict
    observation: str  # JSON, truncated


@dataclass
class TriageResult:
    report: str
    steps: list[TriageStep] = field(default_factory=list)
    completed: bool = False       # model produced a final report within budget
    fallback_used: bool = False   # deterministic baseline substituted


class TriageLoop:
    """Plan → tool call → observe, at most `max_steps` times, then conclude.

    `ask(question, system=..., temperature=...)` is the HermesClient.ask
    signature. The loop tolerates one malformed response (it re-prompts with
    the parse error); a second consecutive one aborts to the baseline report.
    """

    def __init__(self, ask, toolbox: TriageToolbox, max_steps: int = 6,
                 temperature: float = 0.2):
        self.ask = ask
        self.toolbox = toolbox
        self.max_steps = max_steps
        self.temperature = temperature

    @staticmethod
    def _parse_action(text: str) -> dict:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("no JSON object in response")
        action = json.loads(match.group(0))
        kind = action.get("action")
        if kind == "tool":
            if not isinstance(action.get("tool"), str):
                raise ValueError("tool action missing 'tool' name")
            action.setdefault("args", {})
        elif kind == "final":
            if not isinstance(action.get("report"), str) or not action["report"].strip():
                raise ValueError("final action missing 'report'")
        else:
            raise ValueError(f"unknown action {kind!r}")
        return action

    def _prompt(self, focus: str, steps: list[TriageStep], nudge: str) -> str:
        parts = [
            f"Investigation focus: {focus}",
            "",
            "Available tools (read-only):",
            self.toolbox.describe(),
            "",
            f"Budget: {self.max_steps - len(steps)} tool call(s) remaining; "
            "then you MUST return a final report.",
        ]
        for s in steps:
            parts.append(f"\nStep {s.step}: called {s.tool}({json.dumps(s.args)})"
                         f"\nObservation: {s.observation}")
        if nudge:
            parts.append(f"\nYour previous response was invalid: {nudge}. "
                         "Respond with ONLY the JSON action object.")
        return "\n".join(parts)

    def run(self, focus: str) -> TriageResult:
        steps: list[TriageStep] = []
        nudge = ""
        attempts = 0
        # max_steps tool calls + one mandatory concluding turn + nudge retries.
        while attempts < self.max_steps * 2 + 2:
            attempts += 1
            try:
                raw = self.ask(self._prompt(focus, steps, nudge),
                               system=TRIAGE_SYSTEM_PROMPT,
                               temperature=self.temperature)
                action = self._parse_action(raw)
            except Exception as exc:
                if nudge:  # second consecutive failure: abort to baseline
                    return TriageResult(
                        report=baseline_report(self.toolbox, focus),
                        steps=steps, completed=False, fallback_used=True)
                nudge = str(exc)
                continue

            nudge = ""
            if action["action"] == "final":
                return TriageResult(report=action["report"], steps=steps,
                                    completed=True)

            if len(steps) >= self.max_steps:
                # Budget exhausted but the model keeps investigating: force
                # the deterministic conclusion rather than looping.
                return TriageResult(report=baseline_report(self.toolbox, focus),
                                    steps=steps, completed=False,
                                    fallback_used=True)

            observation = json.dumps(self.toolbox.call(action["tool"], action["args"]),
                                     default=str)
            steps.append(TriageStep(
                step=len(steps) + 1,
                tool=action["tool"],
                args=action["args"],
                observation=observation[:OBSERVATION_CHAR_LIMIT],
            ))

        return TriageResult(report=baseline_report(self.toolbox, focus),
                            steps=steps, completed=False, fallback_used=True)
