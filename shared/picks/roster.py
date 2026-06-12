"""Roster store (P0-4): current player→team mapping with timestamped status.

Backed by a Redis hash (`roster:players`, lowercased player name → JSON
record). The validation agent keeps it fresh from `channel_roster_updates`
(Agent 2) and exposes it to `validate_pick` as a plain mapping, so the gate
itself stays pure. Refresh cadence: continuous in-season via the channel,
plus on-demand before each slate run.
"""
import json
import time
from datetime import datetime, timezone

ROSTER_KEY = "roster:players"


class RosterStore:
    def __init__(self, client):
        self.client = client

    def update(self, player: str, team: str, status: str = "ACTIVE",
               last_updated: str | None = None) -> None:
        record = {
            "team": team,
            "status": status,
            "last_updated": last_updated or datetime.now(timezone.utc).isoformat(),
            "ts": time.time(),
        }
        self.client.hset(ROSTER_KEY, player.lower().strip(), json.dumps(record))

    def get(self, player: str) -> dict | None:
        raw = self.client.hget(ROSTER_KEY, player.lower().strip())
        return json.loads(raw) if raw else None

    def team_mapping(self) -> dict[str, str]:
        """player (lowercased) -> team, the shape validate_pick consumes."""
        mapping = {}
        for name, raw in (self.client.hgetall(ROSTER_KEY) or {}).items():
            key = name.decode() if isinstance(name, bytes) else name
            value = raw.decode() if isinstance(raw, bytes) else raw
            try:
                mapping[key] = json.loads(value)["team"]
            except (json.JSONDecodeError, KeyError):
                continue
        return mapping
