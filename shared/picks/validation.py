"""Pick validation gates (P0-1, P0-4, P1-2, P1-3).

`validate_pick` is a pure function — no I/O, no Redis, no clock reads when
`now` is supplied — so every gate is unit-testable in isolation and the
validation agent is a thin transport wrapper around it.

A failed gate is terminal for that pick in that run: rejections are never
auto-corrected, and no downstream agent receives a rejected pick.
"""
from datetime import datetime, timedelta, timezone

from shared.picks.config import load_config
from shared.picks.minutes_risk import blowout_flags
from shared.picks.models import (
    COUNTING_STATS,
    NarrativePayload,
    Pick,
    PickStatus,
    Recommendation,
    ValidationResult,
)
from shared.picks.reason_codes import FLAG_BLOWOUT_RISK, ReasonCode


def check_edge_sign(pick: Pick) -> str | None:
    """P0-1: recommendation must agree with the sign of the computed edge.

    Buy requires projection - line > 0; Sell requires projection - line < 0;
    a zero edge is no pick at all. Returns a reason code or None.
    """
    if pick.recommendation == Recommendation.BUY and pick.edge > 0:
        return None
    if pick.recommendation == Recommendation.SELL and pick.edge < 0:
        return None
    return ReasonCode.EDGE_SIGN_MISMATCH.value


def _parse_ts(raw: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def check_injury_freshness(
    payload: NarrativePayload,
    now: datetime,
    staleness_hours: float,
) -> list[dict]:
    """P0-4: every injury record carried by a pick must be fresh.

    Returns one violation dict per stale (or unparseable-timestamp) record.
    """
    violations = []
    cutoff = now - timedelta(hours=staleness_hours)
    for record in payload.injuries:
        ts = _parse_ts(record.last_updated)
        if ts is None or ts < cutoff:
            violations.append(
                {
                    "code": ReasonCode.STALE_INJURY_DATA.value,
                    "player": record.player,
                    "last_updated": record.last_updated,
                }
            )
    return violations


def check_roster(payload: NarrativePayload, roster: dict[str, str]) -> list[dict]:
    """P0-4: player→team assertions must match the roster table.

    `roster` maps lowercased player name → team. Players absent from the
    table are not violations (the table may lag signings); contradictions are.
    """
    violations = []
    assertions = [(payload.player.name, payload.player.team)]
    assertions += [(rec.player, rec.team) for rec in payload.injuries]
    for name, claimed_team in assertions:
        actual = roster.get(name.lower().strip())
        if actual and claimed_team and actual.lower() != claimed_team.lower():
            violations.append(
                {
                    "code": ReasonCode.ROSTER_MISMATCH.value,
                    "player": name,
                    "claimed_team": claimed_team,
                    "roster_team": actual,
                }
            )
    return violations


def blowout_assessment(
    pick: Pick, payload: NarrativePayload, config: dict
) -> tuple[tuple[str, ...], float]:
    """P1-3: flags and any threshold escalation (in pp) for game-script risk.

    The escalation applies to Buys on counting stats for starters on the
    favored team: those picks must clear threshold + escalation_pp.
    """
    cfg = config["blowout"]
    flags = blowout_flags(payload.game.spread, payload.game.win_probability, config)
    escalation = 0.0
    if (
        FLAG_BLOWOUT_RISK in flags
        and pick.recommendation == Recommendation.BUY
        and pick.stat.upper() in COUNTING_STATS
        and payload.game.win_probability >= cfg["flag_win_prob"]
        and payload.form.minutes_l5 >= config["starter_minutes_l5"]
    ):
        escalation = cfg["escalation_pp"]
    return flags, escalation


def validate_pick(
    pick: Pick,
    payload: NarrativePayload | None = None,
    *,
    config: dict | None = None,
    now: datetime | None = None,
    roster: dict[str, str] | None = None,
) -> ValidationResult:
    """Run every validation gate against one pick. Pure function, no I/O.

    Gate order: edge sign → injury freshness/roster → blowout-escalated
    breakeven threshold. The first terminal failure rejects; threshold
    shortfalls above 50% demote to LEAN (retained for calibration, never
    published).
    """
    cfg = config or load_config()
    now = now or datetime.now(timezone.utc)

    reasons: list[str] = []
    details: dict = {}

    sign_failure = check_edge_sign(pick)
    if sign_failure:
        return ValidationResult(
            status=PickStatus.REJECTED,
            reason_codes=(sign_failure,),
            details={
                "projection": pick.projection,
                "line": pick.line,
                "edge": pick.edge,
                "recommendation": pick.recommendation.value,
            },
        )

    if payload is not None:
        stale = check_injury_freshness(payload, now, cfg["injury_staleness_hours"])
        if stale:
            reasons.append(ReasonCode.STALE_INJURY_DATA.value)
            details["stale_injuries"] = stale
        if roster:
            mismatches = check_roster(payload, roster)
            if mismatches:
                reasons.append(ReasonCode.ROSTER_MISMATCH.value)
                details["roster_mismatches"] = mismatches
        if reasons:
            return ValidationResult(
                status=PickStatus.REJECTED, reason_codes=tuple(reasons), details=details
            )

    flags: tuple[str, ...] = ()
    escalation_pp = 0.0
    if payload is not None:
        flags, escalation_pp = blowout_assessment(pick, payload, cfg)

    margin_pp = cfg["publish_margin_pp"]
    threshold = pick.breakeven_probability + (margin_pp + escalation_pp) / 100.0
    details.update(
        {
            "breakeven_probability": pick.breakeven_probability,
            "publish_threshold": round(threshold, 6),
            "escalation_pp": escalation_pp,
            "hit_probability": pick.hit_probability,
        }
    )

    if pick.hit_probability >= threshold:
        return ValidationResult(status=PickStatus.PUBLISHABLE, flags=flags, details=details)

    reasons.append(ReasonCode.BELOW_THRESHOLD.value)
    if escalation_pp and pick.hit_probability >= pick.breakeven_probability + margin_pp / 100.0:
        # Would have published but for the blowout escalation — record why.
        reasons.append(ReasonCode.BLOWOUT_RISK.value)
    status = PickStatus.LEAN if pick.hit_probability > 0.5 else PickStatus.REJECTED
    return ValidationResult(
        status=status, reason_codes=tuple(reasons), flags=flags, details=details
    )
