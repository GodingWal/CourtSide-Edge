import json
import time
import os
from fastapi import FastAPI
import uvicorn
import threading

from shared.base_agent import setup_logging, db_transaction
from shared.odds_math import american_to_decimal
from shared.redis_client import RedisPubSub
from shared.db import db_available

logger = setup_logging("Agent16_HedgeOracle")

app = FastAPI(title="Agent 16: Dynamic Hedging & Arbitrage Oracle")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))

@app.get("/health")
def health():
    return {"status": "healthy"}

def calculate_hedge_profit(original_odds, live_odds, stake):
    """Locked-in profit from an equal-payout hedge on the opposite side.

    hedge_stake equalizes the payout of both outcomes; the profit is that
    payout minus the total outlay. Negative profit means hedging here LOSES
    money - the caller must skip it, never floor it to a fake positive.
    """
    dec_orig = american_to_decimal(original_odds)
    dec_live = american_to_decimal(live_odds)

    hedge_stake = round((stake * dec_orig) / dec_live, 2)
    total_outlay = stake + hedge_stake
    payout = stake * dec_orig
    potential_profit = round(payout - total_outlay, 2)

    return hedge_stake, potential_profit


def load_live_props(pubsub):
    """Latest live prop per (player, stat) from Agent 1's props:lines cache."""
    best = {}
    try:
        raw = pubsub.client.hgetall("props:lines")
    except Exception as e:
        logger.warning(f"Could not read live props from Redis: {e}")
        return {}
    for payload in raw.values():
        try:
            prop = json.loads(payload)
        except (TypeError, ValueError):
            continue
        player, stat = prop.get("player"), prop.get("stat")
        if not player or not stat or prop.get("line") is None:
            continue
        key = (player, stat)
        if key not in best or (prop.get("timestamp") or 0) > (best[key].get("timestamp") or 0):
            best[key] = prop
    return best

def scan_for_hedges(pubsub):
    logger.info("Scanning for dynamic hedging and middle opportunities...")

    if not db_available(DB_PATH):
        logger.warning("Database not found. Skipping hedge scan.")
        return

    try:
        # All reads and writes happen in a single transaction: the DELETE +
        # INSERT pair per bet is atomic (rolled back together on any error).
        with db_transaction(DB_PATH) as conn:
            cursor = conn.cursor()

            # Pending straight wagers that have not already been hedged. The
            # NOT EXISTS guard breaks the old loop where Agent 20 placed a
            # fresh hedge bet for the same wager on every scan cycle.
            cursor.execute("""
                SELECT b.id, b.player, b.stat, b.line, b.over_under, b.book_odds, b.stake
                FROM bets b
                WHERE b.result IS NULL
                  AND b.is_parlay = 0
                  AND b.parent_id IS NULL
                  AND (b.is_hedge IS NULL OR b.is_hedge = 0)
                  AND NOT EXISTS (
                      SELECT 1 FROM bets h WHERE h.parent_id = b.id AND h.is_hedge = 1
                  )
            """)
            pending_bets = cursor.fetchall()

            if not pending_bets:
                logger.info("No active pending wagers to hedge.")
                return

            # Real live prop lines only (Agent 1's props:lines cache, keyed by
            # player|stat|book). No live market for the prop -> no hedge.
            live_props = load_live_props(pubsub)

            for bet_id, player, stat, line, over_under, book_odds, stake in pending_bets:
                prop = live_props.get((player, stat))
                if prop is None or line is None or over_under not in ("OVER", "UNDER"):
                    continue
                live_line = prop.get("line")
                if live_line is None or live_line == line:
                    continue

                # The hedge takes the OPPOSITE side of the original wager, at
                # that side's real posted price.
                hedge_side = "UNDER" if over_under == "OVER" else "OVER"
                live_odds = prop.get("under_odds") if hedge_side == "UNDER" else prop.get("odds")
                if live_odds is None:
                    continue  # no real price for the opposite side

                try:
                    hedge_stake, profit = calculate_hedge_profit(book_odds, int(live_odds), stake)
                except (TypeError, ValueError) as e:
                    logger.warning(f"Skipping hedge for bet {bet_id}: bad odds ({e})")
                    continue

                # Clear any stale opportunity for this bet before deciding.
                cursor.execute("DELETE FROM hedging_opportunities WHERE bet_id = ?", (bet_id,))

                if profit <= 0:
                    continue  # hedging locks in a LOSS here - never surface it

                instructions = (
                    f"Bet ${hedge_stake} on {player} {hedge_side} {live_line} {stat} "
                    f"at {int(live_odds):+d} ({prop.get('book', 'book')}) to lock in "
                    f"${profit:.2f} regardless of outcome (original: {over_under} {line})."
                )

                cursor.execute("""
                    INSERT INTO hedging_opportunities
                    (bet_id, hedged_player, original_line, original_odds, live_line, live_odds, potential_profit, hedge_instructions, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (bet_id, player, line, book_odds, live_line, int(live_odds), profit, instructions, int(time.time() * 1000)))

                logger.info(f"Hedge opportunity locked for {player} {stat} (Potential Profit: ${profit:.2f})")

    except Exception as e:
        logger.error(f"Error scanning for hedges: {e}")

def hedge_oracle_loop():
    time.sleep(10)
    pubsub = RedisPubSub()
    while True:
        try:
            scan_for_hedges(pubsub)
        except Exception as e:
            logger.error(f"Error in hedge oracle loop: {e}")
        time.sleep(30) # scan for hedges every 30s

if __name__ == "__main__":
    logger.info("Starting Agent 16 (Dynamic Hedging & Arbitrage Oracle)...")
    
    # Run loop in background thread
    loop_thread = threading.Thread(target=hedge_oracle_loop, daemon=True)
    loop_thread.start()
    
    # Start FastAPI server on port 8012
    uvicorn.run(app, host="0.0.0.0", port=8012)
