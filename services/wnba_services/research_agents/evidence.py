"""What the agents are allowed to look at, and where it comes from.

The research corpus used to be the frozen feature snapshot, re-serialised into five groups. That
made every agent a commentator on numbers it could not interrogate: the matchup analyst was asked
to assess pace, defense, rest and blowout risk from three multipliers, and the only honest
conclusion available was a restatement of its inputs.

Everything added here was already in the database at forecast time and was simply never put in
front of the model:

* **Recent form** -- the player's last games in *this* market, with minutes, so "the model says
  0.62" can be checked against what actually happened the last ten times.
* **Line movement** -- the archive is a changed-state record, so the path from opening line to
  current line is free. A market analyst asked about "quote freshness" with no movement history
  is being asked to guess.
* **Market-implied probability** -- the de-vigged price. Without it no agent can tell whether the
  model's disagreement with the market is large or ordinary.
* **Teammate availability** -- who else is out, rather than a count of role effects.
* **Precedents** -- comparable settled decisions, which the PAT workflow already retrieved and
  then never showed to anybody.

Two invariants hold over every group.

**Point-in-time or nothing.** Every query is bounded by the forecast timestamp. A settled game,
a later quote, an injury report filed after the fact -- none of them can enter, because evidence
that postdates the decision turns research into hindsight and the leakage suite into a liar.

**Deterministic identity.** Evidence IDs stay content-addressed (uuid5 over the document hash and
the group name), so re-running research over an unchanged snapshot cites the same evidence rather
than growing a new copy of it every night.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from wnba_marketmath import remove_vig_decimal

from wnba_services.learning_loop.settlement import STAT_COLUMNS
from wnba_services.research_agents.precedents import retrieve_precedents

__all__ = ["EVIDENCE_GROUPS", "build_evidence_document"]

# Named so a reader can see the corpus at a glance, and so a group that stops being produced is
# visible as a gap rather than as an absence nobody notices.
EVIDENCE_GROUPS = (
    "availability",
    "role_effects",
    "matchup",
    "market",
    "forecast_audit",
    "recent_form",
    "line_movement",
    "market_implied",
    "teammate_availability",
    "precedents",
)

# How many past games the form group carries. Ten is long enough to show a trend and short enough
# that a mid-season role change is not averaged away by a player's July.
_FORM_GAMES = 10


def _snapshot_groups(projection: dict[str, Any]) -> dict[str, Any]:
    """The five original groups, unchanged: everything derivable from the frozen snapshot."""
    features = projection["features"]
    if not isinstance(features, dict):
        raise ValueError("feature snapshot is not an object")
    return {
        "availability": {
            key: features.get(key)
            for key in (
                "injury_designation",
                "injury_detail",
                "expected_minutes",
                "start_probability",
                "closing_lineup_probability",
            )
        },
        "role_effects": {
            key: features.get(key)
            for key in (
                "history_games",
                "teammate_effect_count",
                "teammate_rate_multiplier",
                "teammate_effect_confidence",
            )
        },
        "matchup": {
            key: features.get(key)
            for key in (
                "expected_possessions",
                "pace_multiplier",
                "defense_multiplier",
                "expected_margin",
                "blowout_probability",
                "team_rest_days",
                "opponent_rest_days",
            )
        },
        "market": {
            "source": projection["source"],
            "line": projection["line"],
            "observed_at": str(projection["observed_at"]),
            "locks_at": str(projection["locks_at"]),
            "prop_type": projection["prop_type"],
        },
        "forecast_audit": {
            "model_mean": projection["mean"],
            "model_median": projection["median"],
            "model_stddev": projection["stddev"],
            "model_disagreement": projection["model_disagreement"],
            "data_quality_score": projection["data_quality_score"],
        },
    }


def _recent_form(cur: Any, projection: dict[str, Any]) -> dict[str, Any]:
    """The player's last games in this market, as of the forecast timestamp.

    Bounded by ``scheduled_tipoff < generated_at`` rather than by settlement, because a game that
    had tipped but not finished when we forecast was not knowable either.
    """
    columns = STAT_COLUMNS.get(str(projection["prop_type"]))
    if columns is None:
        return {"available": False, "reason": f"unsupported market {projection['prop_type']!r}"}
    total = " + ".join(f"l.{column}" for column in columns)
    cur.execute(
        f"""SELECT g.scheduled_tipoff,l.minutes,l.started,({total})::float AS stat
            FROM wnba.player_game_lines l
            JOIN wnba.games g ON g.game_id=l.game_id
            WHERE l.player_id=%s AND l.system_to IS NULL AND g.scheduled_tipoff < %s
            ORDER BY g.scheduled_tipoff DESC LIMIT %s""",
        (projection["player_id"], projection["generated_at"], _FORM_GAMES),
    )
    rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        return {"available": False, "reason": "no prior games on record"}
    line = float(str(projection["line"]))
    values = [float(str(row["stat"])) for row in rows]
    cleared = [value for value in values if value > line]
    return {
        "available": True,
        "games": [
            {
                "played_at": str(row["scheduled_tipoff"]),
                "minutes": float(str(row["minutes"])),
                "started": bool(row["started"]),
                "stat": float(str(row["stat"])),
                "cleared_current_line": float(str(row["stat"])) > line,
            }
            for row in rows
        ],
        "sample_size": len(values),
        "mean": sum(values) / len(values),
        "cleared_current_line": len(cleared),
        # A raw hit rate over ten games, offered as a base rate and nothing more. It ignores
        # opponent, rest and role, all of which the model does not -- an agent that treats this
        # as a competing forecast is misreading it.
        "cleared_rate": len(cleared) / len(values),
    }


def _line_movement(cur: Any, projection: dict[str, Any]) -> dict[str, Any]:
    """Every archived state of this market before the forecast, per source."""
    cur.execute(
        """SELECT source,line,over_multiplier,under_multiplier,is_available,system_from
           FROM wnba.prop_quotes
           WHERE player_id=%s AND game_id=%s AND prop_type=%s AND system_from<=%s
           ORDER BY system_from""",
        (
            projection["player_id"],
            projection["game_id"],
            projection["prop_type"],
            projection["generated_at"],
        ),
    )
    rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        return {"available": False, "reason": "no archived states for this market"}
    states = [
        {
            "source": str(row["source"]),
            "line": float(str(row["line"])),
            "observed_at": str(row["system_from"]),
            "is_available": bool(row["is_available"]),
        }
        for row in rows
    ]
    opening = float(str(states[0]["line"]))
    current = float(str(states[-1]["line"]))
    return {
        "available": True,
        "states": states,
        "opening_line": opening,
        "current_line": current,
        "movement": current - opening,
        "state_changes": len(states),
        "sources": sorted({state["source"] for state in states}),
    }


def _market_implied(projection: dict[str, Any], movement: dict[str, Any]) -> dict[str, Any]:
    """The de-vigged price, where the operator quoted both directions.

    A pick'em product that offers one multiplier per side is still two-sided for this purpose;
    a market quoted on one side only is not, and returns unavailable rather than a number
    manufactured from the vig-inclusive price.
    """
    if not movement.get("available"):
        return {"available": False, "reason": "no archived quote to price"}
    over = projection.get("over_multiplier")
    under = projection.get("under_multiplier")
    if over is None or under is None:
        return {
            "available": False,
            "reason": "market is priced on one side only, so vig cannot be removed",
        }
    fair = remove_vig_decimal(float(str(over)), float(str(under)))
    if fair is None:
        return {"available": False, "reason": "quoted prices do not admit a fair probability"}
    fair_over, fair_under = fair
    model_over = float(str(projection["probability_over"]))
    return {
        "available": True,
        "market_probability_over": fair_over,
        "market_probability_under": fair_under,
        "model_probability_over": model_over,
        # The number an agent needs to judge whether our disagreement with the market is large.
        # It is not evidence that we are right; it is the size of the claim we are making.
        "model_minus_market": model_over - fair_over,
    }


def _teammate_availability(cur: Any, projection: dict[str, Any]) -> dict[str, Any]:
    """Who else on this team was designated something other than available, as of the forecast."""
    cur.execute(
        """SELECT p.full_name,i.designation,i.detail,i.system_from
           FROM wnba.injury_status i
           JOIN wnba.players p ON p.player_id=i.player_id
           WHERE i.game_id=%s AND i.player_id<>%s AND i.system_from<=%s
             AND (i.system_to IS NULL OR i.system_to>%s)
             AND i.designation<>'available'
           ORDER BY i.designation,p.full_name""",
        (
            projection["game_id"],
            projection["player_id"],
            projection["generated_at"],
            projection["generated_at"],
        ),
    )
    rows = [dict(row) for row in cur.fetchall()]
    return {
        "available": True,
        "count": len(rows),
        "players": [
            {
                "full_name": str(row["full_name"]),
                "designation": str(row["designation"]),
                "detail": None if row["detail"] is None else str(row["detail"]),
                "reported_at": str(row["system_from"]),
            }
            for row in rows
        ],
    }


def _precedents(cur: Any, projection: dict[str, Any]) -> dict[str, Any]:
    """Comparable settled decisions, retrieved point-in-time.

    This is the strongest base-rate signal the platform holds, it was already being computed for
    the audit trail, and until now no prompt contained it.
    """
    found = retrieve_precedents(cur, projection)
    if not found:
        return {"available": False, "reason": "no settled comparable decisions"}
    hits = sum(1 for item in found if "hit=True" in item.summary)
    return {
        "available": True,
        "count": len(found),
        "cleared": hits,
        "episodes": [
            {
                "episode_id": str(item.episode_id),
                "similarity": item.similarity,
                "summary": item.summary,
            }
            for item in found
        ],
    }


def build_evidence_document(
    cur: Any, projection: dict[str, Any], *, now: datetime | None = None
) -> tuple[str, dict[UUID, str]]:
    """Assemble the full corpus for one projection.

    Returns the canonical document (for the source-document record) and one evidence item per
    group. Groups that could not be built are still emitted, carrying ``available: false`` and a
    reason: an agent that can see *what was missing* can flag it, while a silently omitted group
    is indistinguishable from one that said nothing interesting.
    """
    _ = now  # groups are bounded by the projection's own timestamp, not by wall clock
    groups: dict[str, Any] = _snapshot_groups(projection)
    movement = _line_movement(cur, projection)
    groups["recent_form"] = _recent_form(cur, projection)
    groups["line_movement"] = movement
    groups["market_implied"] = _market_implied(projection, movement)
    groups["teammate_availability"] = _teammate_availability(cur, projection)
    groups["precedents"] = _precedents(cur, projection)

    document = json.dumps(groups, sort_keys=True, default=str)
    document_hash = hashlib.sha256(document.encode()).hexdigest()
    evidence = {
        uuid5(NAMESPACE_URL, f"courtside-edge:evidence:{document_hash}:{name}"): json.dumps(
            values, sort_keys=True, default=str
        )
        for name, values in groups.items()
    }
    return document, evidence
