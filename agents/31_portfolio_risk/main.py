"""Agent 31: Portfolio Risk Agent.

Monitors the live bet portfolio for concentration risk, correlated
exposure, drawdown, and bankroll utilization. Publishes risk alerts when
exposure limits are breached.

Publishes:
  - recent:risk_reports          → capped Redis list for dashboard queries
  - channel_risk_alert           → Pub/Sub for real-time risk alerts

Writes:
  - agent_context_store (via ContextClient) for risk trend tracking

Env:
  RISK_INTERVAL_MINUTES      sweep cadence (default 15)
  RISK_MAX_SINGLE_EXPOSURE   max % of bankroll on one player (default 0.08)
  RISK_MAX_STAT_EXPOSURE     max % of bankroll on one stat category (default 0.15)
  RISK_MAX_CORRELATED_LEGS   max correlated legs in open parlays (default 3)
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

logger = setup_logging("Agent31_PortfolioRisk")

DB_PATH = os.getenv("PICKS_DB_PATH", "./data/hoopstats_wnba.db")
INTERVAL_MINUTES = float(os.getenv("RISK_INTERVAL_MINUTES", "15"))
MAX_SINGLE_EXPOSURE = float(os.getenv("RISK_MAX_SINGLE_EXPOSURE", "0.08"))
MAX_STAT_EXPOSURE = float(os.getenv("RISK_MAX_STAT_EXPOSURE", "0.15"))
MAX_CORRELATED_LEGS = int(os.getenv("RISK_MAX_CORRELATED_LEGS", "3"))

REPORTS_KEY = "recent:risk_reports"
context = ContextClient()


class PortfolioRiskAgent:
    """Monitors portfolio-level risk: concentration, correlation, drawdown."""

    def __init__(self, pubsub: RedisPubSub, db_path: str = DB_PATH):
        self.pubsub = pubsub
        self.db_path = db_path
        self.audit = AuditLogger()

    def _get_open_bets(self) -> list[dict]:
        if not db_available(self.db_path):
            return []
        try:
            conn = db_connect(self.db_path)
            cursor = conn.execute(
                "SELECT * FROM bets WHERE result IS NULL ORDER BY placed_at DESC"
            )
            rows = cursor.fetchall()
            cols = [d[0] for d in cursor.description] if cursor.description else []
            conn.close()
            return [dict(zip(cols, row)) for row in rows]
        except Exception as e:
            logger.warning("Could not fetch open bets: %s", e)
            return []

    def _get_bankroll(self) -> float:
        try:
            if not db_available(self.db_path):
                return 0.0
            conn = db_connect(self.db_path)
            cursor = conn.execute(
                "SELECT balance FROM bankroll_history ORDER BY timestamp DESC LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return float(row[0])
        except Exception as e:
            logger.warning("Could not fetch bankroll: %s", e)
        return 0.0

    def _get_drawdown(self) -> dict:
        try:
            if not db_available(self.db_path):
                return {"drawdown_pct": 0}
            conn = db_connect(self.db_path)
            cursor = conn.execute(
                "SELECT balance, drawdown_pct FROM bankroll_history ORDER BY timestamp DESC LIMIT 1"
            )
            row = cursor.fetchone()
            conn.close()
            if row:
                return {"balance": float(row[0]), "drawdown_pct": float(row[1])}
        except Exception as e:
            logger.warning("Could not fetch drawdown: %s", e)
        return {"drawdown_pct": 0}

    def _analyze_risk(self, bets: list[dict], bankroll: float) -> dict:
        if not bets or bankroll <= 0:
            return {"note": "no open bets or bankroll unknown"}

        total_staked = sum(b.get("stake", 0) or 0 for b in bets)
        utilization = total_staked / bankroll

        # Player concentration
        by_player: dict[str, float] = {}
        by_stat: dict[str, float] = {}
        for b in bets:
            player = b.get("player") or "PARLAY"
            stat = b.get("stat") or "MIXED"
            stake = b.get("stake", 0) or 0
            by_player[player] = by_player.get(player, 0) + stake
            by_stat[stat] = by_stat.get(stat, 0) + stake

        player_exposure = {
            k: round(v / bankroll, 4)
            for k, v in sorted(by_player.items(), key=lambda x: -x[1])
        }
        stat_exposure = {
            k: round(v / bankroll, 4)
            for k, v in sorted(by_stat.items(), key=lambda x: -x[1])
        }

        # Detect breaches
        breaches = []
        for player, exp in player_exposure.items():
            if exp > MAX_SINGLE_EXPOSURE:
                breaches.append({
                    "type": "PLAYER_CONCENTRATION",
                    "entity": player,
                    "exposure": exp,
                    "limit": MAX_SINGLE_EXPOSURE,
                })
        for stat, exp in stat_exposure.items():
            if exp > MAX_STAT_EXPOSURE:
                breaches.append({
                    "type": "STAT_CONCENTRATION",
                    "entity": stat,
                    "exposure": exp,
                    "limit": MAX_STAT_EXPOSURE,
                })

        # Parlay leg correlation (count legs in open parlays)
        parlay_legs = 0
        for b in bets:
            if b.get("is_parlay") == 1 and b.get("parent_id") is not None:
                parlay_legs += 1

        if parlay_legs > MAX_CORRELATED_LEGS:
            breaches.append({
                "type": "CORRELATED_LEGS",
                "entity": "open_parlay_legs",
                "count": parlay_legs,
                "limit": MAX_CORRELATED_LEGS,
            })

        drawdown = self._get_drawdown()
        if drawdown.get("drawdown_pct", 0) > 15:
            breaches.append({
                "type": "DRAWDOWN",
                "drawdown_pct": drawdown["drawdown_pct"],
                "limit": 15,
            })

        return {
            "bankroll": round(bankroll, 2),
            "total_staked": round(total_staked, 2),
            "utilization": round(utilization, 4),
            "open_bets": len(bets),
            "player_exposure": player_exposure,
            "stat_exposure": stat_exposure,
            "parlay_legs": parlay_legs,
            "drawdown": drawdown,
            "breaches": breaches,
            "risk_level": "HIGH" if breaches else "NORMAL",
        }

    def run_risk_check(self) -> dict:
        bets = self._get_open_bets()
        bankroll = self._get_bankroll()
        report = self._analyze_risk(bets, bankroll)

        record = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "report": report,
            "ts": time.time(),
        }

        self.pubsub.push_recent(REPORTS_KEY, record, cap=20)

        if report.get("breaches"):
            self.pubsub.publish("channel_risk_alert", record)
            logger.warning("Risk breaches detected: %s", report["breaches"])

        context.write_context(
            game_id="system_wide",
            agent_id="Agent_31",
            context_key="portfolio_risk",
            context_value=report,
            confidence=0.85,
            ttl_seconds=3600,
        )

        self.audit.log_decision(
            trace_id=generate_trace_id(),
            agent_id="Agent_31",
            action="ABSTAIN",
            reason=f"risk check: {report.get('risk_level')} — {len(report.get('breaches', []))} breaches",
            output_payload=record,
        )

        logger.info(
            "Risk check: %s open bets, %s utilization, %s breaches",
            report.get("open_bets", 0),
            report.get("utilization", 0),
            len(report.get("breaches", [])),
        )
        return record

    def sweep(self) -> None:
        self.run_risk_check()


def main():
    pubsub = RedisPubSub()
    agent = PortfolioRiskAgent(pubsub)
    logger.info("Agent 31 (Portfolio Risk) started — interval %s min", INTERVAL_MINUTES)
    try:
        run_polling_loop(task=agent.sweep, interval=INTERVAL_MINUTES * 60, initial_delay=30.0, logger=logger)
    except KeyboardInterrupt:
        pubsub.close()


if __name__ == "__main__":
    main()
