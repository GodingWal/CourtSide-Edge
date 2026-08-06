"""Record one ESPN game-market snapshot for today's WNBA slate.

The CLV timer runs this every five minutes alongside the near-lock quote poll. The logic
lives in wnba_services.ingestion.espn.snapshot_game_odds; this is only the entry point.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from wnba_services.ingestion.espn import snapshot_game_odds

if __name__ == "__main__":
    day = datetime.now(ZoneInfo("America/New_York")).date()
    result = snapshot_game_odds(day)
    print(
        f"odds-snapshot date={result.game_date} games={result.games_seen} "
        f"snapshots={result.snapshots_written}"
    )
