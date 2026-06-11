"""Confidence grading (P3-1).

Every publishable pick gets an A-D grade from edge size (hit probability vs
the publish threshold) and factor alignment. D never publishes; C is
flex-only. "Threshold" here is the publish threshold — breakeven + margin —
so a C pick already clears the P1-2 gate.

Factors (aligned = supports pick direction):
- matchup: opponent defensive rank vs the stat (1 = stingiest)
- form:    L5 average vs the line
- injury/lineup: teammate OUT boosts a Buy; pick player listed
  questionable/doubtful supports a Sell (minutes risk)
- pace:    game pace rank (1 = fastest)
"""
from shared.picks.breakeven import publish_threshold
from shared.picks.config import load_config
from shared.picks.models import NarrativePayload, Pick, Recommendation

GRADES = ("A", "B", "C", "D")
POWER_ELIGIBLE = {"A", "B"}
FLEX_ELIGIBLE = {"A", "B", "C"}


def factor_alignment(pick: Pick, payload: NarrativePayload,
                     config: dict | None = None) -> dict[str, bool]:
    cfg = config or load_config()
    buying = pick.recommendation == Recommendation.BUY
    half_league = cfg["league_size"] / 2

    def_rank = payload.matchup.opp_def_rank_vs_stat
    matchup = (def_rank > half_league) if buying else (0 < def_rank <= half_league)
    if def_rank <= 0:
        matchup = False

    form = payload.form.l5_avg > pick.line if buying else payload.form.l5_avg < pick.line

    player_name = payload.player.name.lower()
    teammate_out = any(
        rec.status.upper() == "OUT"
        and rec.team.lower() == payload.player.team.lower()
        and player_name not in rec.player.lower()
        for rec in payload.injuries
    )
    self_limited = any(
        player_name in rec.player.lower()
        and rec.status.upper() in ("QUESTIONABLE", "DOUBTFUL", "PROBABLE")
        for rec in payload.injuries
    )
    injury = teammate_out if buying else self_limited

    pace_rank = payload.matchup.pace_rank
    pace = (0 < pace_rank <= half_league) if buying else (pace_rank > half_league)

    return {"matchup": matchup, "form": form, "injury": injury, "pace": pace}


def grade_pick(pick: Pick, payload: NarrativePayload,
               config: dict | None = None) -> str:
    """A-D grade for a pick that has already been priced against its line."""
    cfg = config or load_config()
    grading = cfg["grading"]
    threshold = publish_threshold(pick.book, pick.entry_type, pick.legs, cfg)
    aligned = sum(factor_alignment(pick, payload, cfg).values())
    hp = pick.hit_probability

    if hp >= threshold + grading["a_margin_pp"] / 100.0 and aligned >= grading["a_min_factors"]:
        return "A"
    if hp >= threshold + grading["b_margin_pp"] / 100.0 and aligned >= grading["b_min_factors"]:
        return "B"
    if hp >= threshold:
        return "C"
    return "D"


def allowed_in_entry(grade: str, entry_type: str) -> bool:
    """Entry-builder enforcement: power plays are A/B only; C is flex-only;
    D is never published."""
    if entry_type.lower() == "power":
        return grade in POWER_ELIGIBLE
    if entry_type.lower() == "flex":
        return grade in FLEX_ELIGIBLE
    return False
