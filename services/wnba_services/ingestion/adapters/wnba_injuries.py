"""Official WNBA injury-report index and PDF parser."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any

import pdfplumber

INDEX_URL = "https://www.wnba.com/api/injury-reports"


@dataclass(frozen=True)
class OfficialInjury:
    game_date: str
    matchup: str
    team_name: str
    player_name: str
    status: str
    reason: str


def latest_report(index: dict[str, Any]) -> tuple[str, str]:
    links = index.get("links")
    if not isinstance(links, list) or not links:
        raise ValueError("official injury index has no reports")
    item = links[-1]
    if not isinstance(item, dict) or not item.get("href") or not item.get("label"):
        raise ValueError("official injury index latest report is malformed")
    return str(item["href"]), str(item["label"])


def _name(value: str) -> str:
    parts = [part.strip() for part in value.split(",", maxsplit=1)]
    return f"{parts[1]} {parts[0]}" if len(parts) == 2 else value.strip()


def _context(words: list[dict[str, Any]], top: float, left: float, right: float) -> str:
    candidates = [
        word
        for word in words
        if 120 < float(word["top"]) <= top and left <= float(word["x0"]) < right
    ]
    if not candidates:
        return ""
    nearest = max(float(word["top"]) for word in candidates)
    return " ".join(
        str(word["text"]) for word in candidates if abs(float(word["top"]) - nearest) < 4
    )


def parse_report(document: bytes) -> list[OfficialInjury]:
    """Parse positioned words because the league PDF has no semantic table cells."""
    injuries: list[OfficialInjury] = []
    with pdfplumber.open(BytesIO(document)) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            player_words = [
                w for w in words if 400 <= float(w["x0"]) < 580 and float(w["top"]) > 120
            ]
            for player in player_words:
                top = float(player["top"])
                same = [w for w in words if abs(float(w["top"]) - top) < 4]
                status = " ".join(str(w["text"]) for w in same if 580 <= float(w["x0"]) < 665)
                if not status:
                    continue
                reason_words = [
                    w for w in words if float(w["x0"]) >= 665 and abs(float(w["top"]) - top) <= 12
                ]
                injuries.append(
                    OfficialInjury(
                        game_date=_context(words, top, 0, 115),
                        matchup=_context(words, top, 190, 260),
                        team_name=_context(words, top, 260, 420),
                        player_name=_name(str(player["text"])),
                        status=status,
                        reason=" ".join(
                            str(w["text"])
                            for w in sorted(reason_words, key=lambda x: float(x["top"]))
                        ),
                    )
                )
    return injuries
