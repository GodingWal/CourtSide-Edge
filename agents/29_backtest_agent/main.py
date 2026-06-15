"""Agent 29: Backtesting Agent.

Runs periodic backtests on historical pick and bet data to evaluate
strategy performance across time windows, markets, and player segments.

Publishes:
  - recent:backtest_reports      → capped Redis list for dashboard queries
  - channel_backtest_summary     → Pub/Sub for real-time alerts

Writes:
  - agent_context_store (via ContextClient) for long-term trend tracking

Env:
  BACKTEST_INTERVAL_MINUTES  sweep cadence (default 60)
  PICKS_DB_PATH              pick log database (default ./data/hoopstats_wnba.db)
"""
import json
import os
import time
from datetime import datetime, timezone

from shared.audit_logger import AuditLogger, generate_trace_id
from shared.base_agent import run_polling_loop, setup_logging
from shared.context_client import ContextClient
from shared.db import db_available, connect as db_connect
from shared.redis_client import RedisPubSub

logger = setup_logging("Agent29_Backtesting")

DB_PATH = os.getenv("PICKS_DB_PATH", "./data/hoopstats_wnba.db")
INTERVAL_MINUTES = float(os.getenv("BACKTEST_INTERVAL_MINUTES", "60"))
REPORTS_KEY = "recent:backtest_reports"

context = ContextClient()


class BacktestAgent:
    """Runs backtests over historical bets and picks to surface performance
    by market, player, time window, and system mode.
    """

    def __init__(self, pubsub: RedisPubSub, db_path: str = DB_PATH):
        self.pubsub = pubsub
        self.db_path = db_path
        self.audit = AuditLogger()

    def _get_settled_bets(self, days: int = 30) -> list[dict]:
        if not db_available(self.db_path):
            return []
        try:
            conn = db_connect(self.db_path)
            cutoff = int(time.time() - days * 86400)
            cursor = conn.execute(
                "SELECT * FROM bets WHERE result IS NOT NULL AND placed_at > ? ORDER BY placed_at DESC",
                (cutoff,),
            )
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description] if cursor.description else []
            conn.close()
            return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.warning("Could not fetch settled bets: %s", e)
            return []

    def _get_picks_log(self, days: int = 30) -> list[dict]:
        if not db_available(self.db_path):
            return []
        try:
            conn = db_connect(self.db_path)
            cutoff = int(time.time() - days * 86400)
            cursor = conn.execute(
                "SELECT * FROM pick_log WHERE created_at > ? ORDER BY created_at DESC",
                (cutoff,),
            )
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description] if cursor.description else []
            conn.close()
            return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.warning("Could not fetch pick log: %s", e)
            return []

    def _compute_backtest(self, bets: list[dict], picks: list[dict]) -> dict:
        if not bets:
            return {"note": "no settled bets in window"}

        wins = sum(1 for b in bets if b.get("result") == "WIN")
        losses = sum(1 for b in bets if b.get("result") == "LOSS")
        pushes = sum(1 for b in bets if b.get("result") == "PUSH")
        total = wins + losses + pushes
        win_rate = wins / max(total, 1)

        pnl = sum(
            b.get("profit_loss", 0) or 0 for b in bets
        )
        avg_stake = sum(b.get("stake", 0) or 0 for b in bets) / max(len(bets), 1)
        roi = pnl / max(avg_stake * len(bets), 1) * 100

        # By stat category
        by_stat: dict[str, dict] = {}
        for b in bets:
            stat = b.get("stat") or "UNKNOWN"
            if stat not in by_stat:
                by_stat[stat] = {"wins": 0, "losses": 0, "pnl": 0, "count": 0}
            by_stat[stat]["count"] += 1
            by_stat[stat]["pnl"] += b.get("profit_loss", 0) or 0
            if b.get("result") == "WIN":
                by_stat[stat]["wins"] += 1
            elif b.get("result") == "LOSS":
                by_stat[stat]["losses"] += 1

        for stat in by_stat:
            s = by_stat[stat]
            s["win_rate"] = round(s["wins"] / max(s["wins"] + s["losses"], 1), 3)
            s["roi"] = round(s["pnl"] / max(avg_stake * s["count"], 1) * 100, 2)

        # By player
        by_player: dict[str, dict] = {}
        for b in bets:
            player = b.get("player") or "UNKNOWN"
            if player not in by_player:
                by_player[player] = {"wins": 0, "losses": 0, "pnl": 0, "count": 0}
            by_player[player]["count"] += 1
            by_player[player]["pnl"] += b.get("profit_loss", 0) or 0
            if b.get("result") == "WIN":
                by_player[player]["wins"] += 1
            elif b.get("result") == "LOSS":
                by_player[player]["losses"] += 1

        for player in by_player:
            p = by_player[player]
            p["win_rate"] = round(p["wins"] / max(p["wins"] + p["losses"], 1), 3)
            p["roi"] = round(p["pnl"] / max(avg_stake * p["count"], 1) * 100, 2)

        # Rejection rate from picks
        rejected = sum(1 for p in picks if p.get("status") == "REJECTED")
        lean = sum(1 for p in picks if p.get("status") == "LEAN")
        published = sum(1 for p in picks if p.get("status") == "PUBLISHED")
        total_picks = len(picks)

        return {
            "window_days": 30,
            "total_bets": len(bets),
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "win_rate": round(win_rate, 3),
            "pnl": round(pnl, 2),
            "roi": round(roi, 2),
            "avg_stake": round(avg_stake, 2),
            "by_stat": {k: v for k, v in sorted(by_stat.items(), key=lambda x: -x[1]["count"])[:10]},
            "by_player": {k: v for k, v in sorted(by_player.items(), key=lambda x: -x[1]["count"])[:10]},
            "pick_pipeline": {
                "total_picks": total_picks,
                "published": published,
                "rejected": rejected,
                "lean": lean,
                "rejection_rate": round(rejected / max(total_picks, 1), 3),
            },
        }

    def run_backtest(self) -> dict:
        bets = self._get_settled_bets(days=30)
        picks = self._get_picks_log(days=30)
        report = self._compute_backtest(bets, picks)

        record = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report": report,
            "ts": time.time(),
        }

        self.pubsub.push_recent(REPORTS_KEY, record, cap=20)
        self.pubsub.publish("channel_backtest_summary", record)

        context.write_context(
            game_id="system_wide",
            agent_id="Agent_29",
            context_key="backtest_30d",
            context_value=report,
            confidence=0.95,
            ttl_seconds=86400,
        )

        self.audit.log_decision(
            trace_id=generate_trace_id(),
            agent_id="Agent_29",
            action="ABSTAIN",
            reason=f"backtest report: {report.get('total_bets', 0)} bets, {report.get('win_rate', 0)} WR",
            output_payload=record,
        )

        logger.info(
            "Backtest complete: %s bets, %s WR, $%s PnL",
            report.get("total_bets", 0),
            report.get("win_rate", 0),
            report.get("pnl", 0),
        )
        return record

    def sweep(self) -> None:
        self.run_backtest()


def main():
    pubsub = RedisPubSub()
    agent = BacktestAgent(pubsub)
    logger.info("Agent 29 (Backtesting) started — interval %s min", INTERVAL_MINUTES)
    try:
        run_polling_loop(task=agent.sweep, interval=INTERVAL_MINUTES * 60, initial_delay=30.0, logger=logger)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
