"""Line snapshot + movement tracking (P2-2).

Lines are timestamped at capture and re-checked at publication. A move
against the pick direction (line up on a Buy, down on a Sell) of at least the
configured threshold flags ADVERSE_LINE_MOVE and forces a re-run of the
threshold gate against the new line; if the new line drops the hit
probability below threshold the pick is demoted to LEAN.
"""
import json
import time
from datetime import datetime, timezone

from shared.picks.config import load_config
from shared.picks.distributions import normal_p_over
from shared.picks.models import Pick, PickStatus, Recommendation
from shared.picks.reason_codes import FLAG_ADVERSE_LINE_MOVE, ReasonCode

HISTORY_KEY = "picks:line_history:{book}|{player}|{stat}"
HISTORY_CAP = 200


class LineHistory:
    """Redis-backed line snapshot store: every scrape is timestamped, and the
    freshest snapshot within the staleness window is used at publication."""

    def __init__(self, client, config: dict | None = None):
        self.client = client
        self.config = config or load_config()

    def _key(self, book: str, player: str, stat: str) -> str:
        return HISTORY_KEY.format(book=book.lower(), player=player.lower(),
                                  stat=stat.upper())

    def record(self, book: str, player: str, stat: str, line: float,
               ts: float | None = None) -> dict:
        snapshot = {"book": book, "player": player, "stat": stat,
                    "line": line, "ts": ts if ts is not None else time.time()}
        key = self._key(book, player, stat)
        self.client.lpush(key, json.dumps(snapshot))
        self.client.ltrim(key, 0, HISTORY_CAP - 1)
        return snapshot

    def latest(self, book: str, player: str, stat: str,
               max_age_minutes: float | None = None,
               now: float | None = None) -> dict | None:
        raw = self.client.lindex(self._key(book, player, stat), 0)
        if not raw:
            return None
        snapshot = json.loads(raw)
        if max_age_minutes is not None:
            now = now if now is not None else time.time()
            if now - snapshot["ts"] > max_age_minutes * 60:
                return None
        return snapshot


def is_adverse_move(recommendation: Recommendation, capture_line: float,
                    current_line: float, threshold: float) -> bool:
    if recommendation == Recommendation.BUY:
        return current_line - capture_line >= threshold
    return capture_line - current_line >= threshold


def revalidate_at_publish(pick: Pick, current_line: float,
                          config: dict | None = None) -> dict:
    """Re-check a pick against the freshest line just before publication.

    Returns {pick, publication_line, adverse, demoted, status}. The returned
    pick is a new frozen instance re-priced against the publication line
    (normal approximation from the pick's stored mean/std) — both the capture
    line and the publication line are recorded for CLV.
    """
    cfg = config or load_config()
    move_cfg = cfg["adverse_line_move"]
    adverse = is_adverse_move(pick.recommendation, pick.line, current_line,
                              move_cfg["threshold"])
    if not adverse:
        return {"pick": pick, "publication_line": current_line,
                "adverse": False, "demoted": False,
                "status": PickStatus.PUBLISHABLE}

    if pick.std is None:
        # No distribution to re-price against: fail safe, demote.
        new_hp = 0.0
    else:
        over_p = normal_p_over(pick.projection, pick.std, current_line)
        new_hp = over_p if pick.recommendation == Recommendation.BUY else 1.0 - over_p

    threshold = pick.breakeven_probability + cfg["publish_margin_pp"] / 100.0
    demoted = new_hp < threshold
    repriced = pick.model_copy(update={
        "line": current_line,
        "hit_probability": round(new_hp, 4),
        "flags": tuple(pick.flags) + (FLAG_ADVERSE_LINE_MOVE,),
    })
    return {
        "pick": repriced,
        "publication_line": current_line,
        "adverse": True,
        "demoted": demoted,
        "status": PickStatus.LEAN if demoted else PickStatus.PUBLISHABLE,
        "reason_code": ReasonCode.ADVERSE_LINE_MOVE.value,
        "capture_line": pick.line,
        "hit_probability_at_publish": round(new_hp, 4),
        "ts": datetime.now(timezone.utc).isoformat(),
    }
