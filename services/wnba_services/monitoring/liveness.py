"""Productivity checks: did the pipeline actually do anything?

Three failures in two days shared one shape. A component ran, exited zero, logged a cheerful
summary, and produced nothing:

* the ingest ledger closed a date while two games were still in the fourth quarter, so those
  games never went final and never settled;
* role projection reported ``projected=0 skipped=9`` because every quoted athlete was a
  duplicate identity with no box-score history;
* the forecaster reported ``forecasts=0 skipped=231`` because it had no roles to work from.

None of them failed. ``OnFailure=`` would have stayed silent through all three, and each was
caught only because a human looked at a screen and found it empty.

So these checks do not ask "is the service up". They ask **"given what we know, should there
be output by now, and is there?"** Each one is a question with a known-good answer of *zero
rows*; anything returned is an anomaly recorded as a data-quality incident.

Every check is scoped to avoid crying wolf -- an empty board at 4am with no games scheduled is
correct behaviour, not an outage. A monitor that alerts on quiet nights gets muted, and a
muted monitor is worse than none because it also carries the illusion of coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from wnba_domain.enums import Severity, ValidationLevel
from wnba_domain.identity import SourceName
from wnba_store.db import connect

__all__ = ["CHECKS", "LivenessCheck", "LivenessReport", "run_liveness_checks"]


@dataclass(frozen=True)
class LivenessCheck:
    """One question whose healthy answer is no rows."""

    code: str
    question: str
    severity: Severity
    sql: str
    blocks_recommendations: bool = False

    @property
    def level(self) -> ValidationLevel:
        return ValidationLevel.TEMPORAL


@dataclass
class Finding:
    check: LivenessCheck
    detail: str


@dataclass
class LivenessReport:
    checked_at: datetime
    findings: list[Finding] = field(default_factory=list)
    incidents_written: int = 0
    incidents_resolved: int = 0
    incidents_refreshed: int = 0
    checks_run: int = 0

    @property
    def failing_codes(self) -> set[str]:
        return {finding.check.code for finding in self.findings}

    @property
    def healthy(self) -> bool:
        return not self.findings

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.check.blocks_recommendations]

    def summary(self) -> str:
        resolved = f", {self.incidents_resolved} resolved" if self.incidents_resolved else ""
        if self.healthy:
            return f"liveness OK ({self.checks_run} checks, nothing to report{resolved})"
        parts = [f"{f.check.code}({f.detail})" for f in self.findings]
        return f"liveness {len(self.findings)}/{self.checks_run} FAILING{resolved}: " + "; ".join(
            parts
        )


# Each query returns problem rows with a single `detail` column. An empty result is health.
CHECKS: tuple[LivenessCheck, ...] = (
    LivenessCheck(
        code="MARKET_ARCHIVE_STALE",
        question="Has the archiver written anything recently?",
        severity=Severity.CRITICAL,
        # The archive is the one asset that cannot be rebuilt, so a silent stop is the most
        # expensive failure available. 45 minutes is three missed 15-minute polls.
        sql="""
            SELECT 'last quote ' || round(extract(epoch FROM now() - max(system_from))/60)
                   || ' minutes ago' AS detail
            FROM wnba.prop_quotes
            HAVING max(system_from) < now() - interval '45 minutes'
        """,
    ),
    LivenessCheck(
        code="GAME_PAST_TIPOFF_NOT_FINAL",
        question="Did a game finish hours ago without ever being marked final?",
        severity=Severity.CRITICAL,
        # This is the ingest-ledger bug. A game six hours past tip is over; if it is still
        # 'scheduled' the box-score ingest never claimed it, and everything downstream --
        # settlement, scoring, calibration -- is stalled behind it.
        sql="""
            SELECT count(*) || ' game(s) past tipoff still scheduled, oldest '
                   || min(scheduled_tipoff)::timestamp(0) AS detail
            FROM wnba.games
            WHERE status <> 'final' AND scheduled_tipoff < now() - interval '6 hours'
            HAVING count(*) > 0
        """,
        blocks_recommendations=True,
    ),
    LivenessCheck(
        code="QUOTED_GAME_WITHOUT_FORECASTS",
        question="Is there a game we have priced but not forecast?",
        severity=Severity.BLOCKING,
        # Tonight's failure. Quotes present, forecasts absent, everything reporting success.
        # Scoped to games at least 30 minutes old in the archive and not yet started, so a
        # board that has only just appeared is not treated as an outage.
        sql="""
            SELECT g.game_id || ' tips ' || g.scheduled_tipoff::timestamp(0)
                   || ' with ' || count(DISTINCT q.player_id) || ' players quoted, 0 forecasts'
                   AS detail
            FROM wnba.games g
            JOIN wnba.prop_quotes q ON q.game_id = g.game_id
            WHERE g.status = 'scheduled'
              AND g.scheduled_tipoff > now()
              AND q.system_from < now() - interval '30 minutes'
              AND NOT EXISTS (SELECT 1 FROM wnba.stat_forecasts f WHERE f.game_id = g.game_id)
            GROUP BY g.game_id, g.scheduled_tipoff
        """,
        blocks_recommendations=True,
    ),
    LivenessCheck(
        code="QUOTED_PLAYERS_WITHOUT_ROLES",
        question="Are we pricing athletes we have no projected role for?",
        severity=Severity.CRITICAL,
        # The proximate cause of tonight's empty board: role projection skipped every athlete
        # because each was a duplicate identity with no history. Catching it here names the
        # failure one step earlier than "the board is empty".
        # Resolves through player_merges exactly as the role projector does. Checking the
        # raw quote id instead reports every merged athlete as missing a role -- which this
        # check did on its first live run, and a monitor that cries wolf gets muted.
        sql="""
            SELECT count(DISTINCT q.player_id) || ' quoted athlete(s) have no projected role'
                   AS detail
            FROM wnba.prop_quotes q
            JOIN wnba.games g ON g.game_id = q.game_id
            LEFT JOIN wnba.player_merges m
              ON m.from_player_id = q.player_id AND m.system_to IS NULL
            WHERE g.status = 'scheduled' AND g.scheduled_tipoff > now()
              AND q.system_from < now() - interval '30 minutes'
              AND NOT EXISTS (
                SELECT 1 FROM wnba.projected_roles r
                WHERE r.player_id = coalesce(m.to_player_id, q.player_id)
                  AND r.game_id = q.game_id AND r.system_to IS NULL)
            HAVING count(DISTINCT q.player_id) > 0
        """,
    ),
    LivenessCheck(
        code="UNMERGED_QUOTED_ATHLETES",
        question="Are athletes on the board still split across sources?",
        severity=Severity.WARNING,
        # The upstream cause. Warning rather than critical because the identity timer usually
        # clears it within twenty minutes; it matters when it persists.
        sql="""
            SELECT count(DISTINCT q.player_id) || ' quoted athlete(s) have no box-score history'
                   AS detail
            FROM wnba.prop_quotes q
            JOIN wnba.games g ON g.game_id = q.game_id
            WHERE g.status = 'scheduled' AND g.scheduled_tipoff > now()
              AND NOT EXISTS (
                SELECT 1 FROM wnba.player_game_lines l
                WHERE l.player_id = coalesce(
                  (SELECT m2.to_player_id FROM wnba.player_merges m2
                   WHERE m2.from_player_id = q.player_id AND m2.system_to IS NULL LIMIT 1),
                  q.player_id))
              AND NOT EXISTS (
                SELECT 1 FROM wnba.player_merges m
                WHERE m.from_player_id = q.player_id AND m.system_to IS NULL)
            HAVING count(DISTINCT q.player_id) > 0
        """,
    ),
    LivenessCheck(
        code="SETTLED_GAME_WITH_UNSCORED_EPISODES",
        question="Did a game finish without its forecasts being scored?",
        severity=Severity.CRITICAL,
        # Settlement stalling is invisible by construction: the learning page simply stays
        # empty, which looks identical to having nothing to learn from yet.
        sql="""
            SELECT count(*) || ' episode(s) on final games remain unsettled' AS detail
            FROM wnba.decision_episodes d
            JOIN wnba.games g ON g.game_id = d.game_id
            LEFT JOIN wnba.episode_outcomes o ON o.episode_id = d.episode_id
            WHERE g.status = 'final'
              AND g.scheduled_tipoff < now() - interval '12 hours'
              AND o.episode_id IS NULL
            HAVING count(*) > 0
        """,
    ),
    LivenessCheck(
        code="CURRENT_FORECAST_WITHOUT_JOINT_SIMULATION",
        question="Did the shadow simulator cover the current forecast runs?",
        severity=Severity.WARNING,
        sql="""
            SELECT count(DISTINCT (f.game_id,f.model_run_id))
                   || ' current game/run group(s) have no joint simulation' AS detail
            FROM wnba.stat_forecasts f
            WHERE f.expires_at > now()
              AND NOT EXISTS (
                SELECT 1 FROM wnba.joint_game_simulations js
                WHERE js.game_id=f.game_id AND js.model_run_id=f.model_run_id)
            HAVING count(DISTINCT (f.game_id,f.model_run_id)) > 0
        """,
    ),
    LivenessCheck(
        code="TRUST_ARTIFACT_STALE",
        question="Has new settled evidence gone through trust fitting?",
        severity=Severity.CRITICAL,
        sql="""
            SELECT 'latest settlement ' || max(o.settled_at)::timestamp(0)
                   || ', latest trust fit '
                   || coalesce((SELECT max(calculated_at)::timestamp(0)::text
                                FROM wnba.selective_policy_snapshots),'never') AS detail
            FROM wnba.episode_outcomes o
            WHERE NOT o.was_voided AND NOT o.was_push
            HAVING count(*) >= 40 AND (
              (SELECT max(calculated_at) FROM wnba.selective_policy_snapshots) IS NULL
              OR max(o.settled_at) >
                 (SELECT max(calculated_at) FROM wnba.selective_policy_snapshots)
                    + interval '8 days')
        """,
    ),
)


def run_liveness_checks(database_url: str | None = None, *, record: bool = True) -> LivenessReport:
    """Run every check, then reconcile the incident table with what is currently true.

    A check that raises is itself a finding. A monitor that dies quietly on a schema change
    would reproduce the exact class of failure it exists to detect.

    **Incidents have a lifecycle now.** The first version only ever inserted: while a condition
    persisted it wrote a fresh row every fifteen minutes, and when the condition cleared it wrote
    nothing at all -- so the incident stayed open forever. The console accumulated hundreds of
    duplicate rows describing one problem, and permanent critical warnings for problems that had
    been fixed weeks earlier. Both failures corrode the same thing: a dashboard that is always red
    conveys exactly as much as one that is always green.

    So each run does three things. A check that is failing and has no open incident opens one. A
    check that is failing and already has one refreshes its detail in place. A check that is now
    clean resolves whatever it left behind.
    """
    report = LivenessReport(checked_at=datetime.now(UTC))

    with connect(database_url) as conn:
        for check in CHECKS:
            report.checks_run += 1
            try:
                with conn.cursor() as cur:
                    cur.execute(check.sql)
                    rows: list[Any] = cur.fetchall()
            except Exception as exc:
                conn.rollback()
                report.findings.append(
                    Finding(check=check, detail=f"check failed to execute: {exc}")
                )
                continue

            for row in rows:
                detail = str(row.get("detail", row))
                report.findings.append(Finding(check=check, detail=detail))

        if record:
            with conn.cursor() as cur:
                # Resolve first. A check that has started passing should stop being red before
                # anything else is written, so a run that fixes everything leaves a clean board
                # even if a later statement fails.
                failing = sorted(report.failing_codes)
                cur.execute(
                    """UPDATE wnba.dq_incidents SET resolved_at=%s
                       WHERE resolved_at IS NULL AND code=ANY(%s)
                         AND NOT (code = ANY(%s))""",
                    (report.checked_at, [check.code for check in CHECKS], failing),
                )
                report.incidents_resolved = cur.rowcount

                for finding in report.findings:
                    detail = f"{finding.check.question} -> {finding.detail}"
                    # One open incident per code. The detail is refreshed because the *shape* of
                    # a persisting problem matters -- "2 games stuck" becoming "9 games stuck" is
                    # news, and a frozen first-observed detail would hide it.
                    cur.execute(
                        """UPDATE wnba.dq_incidents SET detail=%s,detected_at=%s
                           WHERE resolved_at IS NULL AND code=%s""",
                        (detail, report.checked_at, finding.check.code),
                    )
                    if cur.rowcount:
                        report.incidents_refreshed += cur.rowcount
                        continue
                    cur.execute(
                        """INSERT INTO wnba.dq_incidents
                           (incident_id,detected_at,level,severity,code,source,detail,
                            blocks_recommendations)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (
                            uuid4(),
                            report.checked_at,
                            finding.check.level.value,
                            finding.check.severity.value,
                            finding.check.code,
                            SourceName.DERIVED.value,
                            detail,
                            finding.check.blocks_recommendations,
                        ),
                    )
                    report.incidents_written += 1
            conn.commit()

    return report
