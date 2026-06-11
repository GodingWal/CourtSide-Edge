import time
import os
from fastapi import FastAPI
import uvicorn
import threading

from shared.base_agent import setup_logging, db_transaction
from shared.odds_math import american_to_decimal

logger = setup_logging("Agent20_HedgeExecutor")

app = FastAPI(title="Agent 20: Auto-Execution Hedging Engine")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))

@app.get("/health")
def health():
    return {"status": "healthy"}

def check_and_execute_hedges():
    """Checks the database for new hedging opportunities and automatically executes them."""
    if not os.path.exists(DB_PATH):
        return
        
    try:
        # INSERT of the hedge wager and DELETE of the opportunity are atomic.
        with db_transaction(DB_PATH) as conn:
            cursor = conn.cursor()

            # Select all unexecuted hedging opportunities
            cursor.execute("""
                SELECT id, bet_id, hedged_player, original_line, original_odds, live_line, live_odds, potential_profit, hedge_instructions 
                FROM hedging_opportunities
            """)
            rows = cursor.fetchall()

            for id_opp, bet_id, player, orig_line, orig_odds, live_line, live_odds, profit, instructions in rows:
                logger.info(f"🚨 Agent 20: Initiating automated hedge execution for Bet #{bet_id} ({player})")

                # Original bet details: stat, opponent, side and stake. The
                # hedge takes the OPPOSITE side of the original wager (the old
                # code guessed the side from the odds sign, which is wrong).
                cursor.execute(
                    "SELECT stat, opposing_team, over_under, stake FROM bets WHERE id = ?",
                    (bet_id,),
                )
                bet_details = cursor.fetchone()
                if not bet_details:
                    logger.warning(f"Original bet #{bet_id} not found - dropping opportunity.")
                    cursor.execute("DELETE FROM hedging_opportunities WHERE id = ?", (id_opp,))
                    continue
                stat, opp, over_under, orig_stake = bet_details
                hedge_side = "UNDER" if over_under == "OVER" else "OVER"

                # Skip if a hedge was already placed for this wager - placing
                # a fresh one every cycle was unbounded ledger pollution.
                cursor.execute(
                    "SELECT 1 FROM bets WHERE parent_id = ? AND is_hedge = 1 LIMIT 1",
                    (bet_id,),
                )
                if cursor.fetchone():
                    cursor.execute("DELETE FROM hedging_opportunities WHERE id = ?", (id_opp,))
                    continue

                try:
                    dec_orig = american_to_decimal(orig_odds)
                    dec_live = american_to_decimal(live_odds)
                except ValueError as e:
                    logger.warning(f"Skipping hedge for bet #{bet_id}: bad odds ({e})")
                    cursor.execute("DELETE FROM hedging_opportunities WHERE id = ?", (id_opp,))
                    continue
                hedge_stake = round(((orig_stake or 0.0) * dec_orig) / dec_live, 2)
                if hedge_stake <= 0:
                    cursor.execute("DELETE FROM hedging_opportunities WHERE id = ?", (id_opp,))
                    continue

                # Insert hedge wager into bets table
                cursor.execute("""
                    INSERT INTO bets 
                    (parent_id, is_parlay, player, stat, line, over_under, book_odds, true_odds, edge_pct, stake, result, actual_value, profit_loss, placed_at, settled_at, opposing_team, notes, closing_odds, clv_pct, is_hedge)
                    VALUES (?, 0, ?, ?, ?, ?, ?, NULL, NULL, ?, NULL, NULL, NULL, ?, NULL, ?, ?, NULL, NULL, 1)
                """, (
                    bet_id,
                    player,
                    stat,
                    live_line,
                    hedge_side,
                    live_odds,
                    hedge_stake,
                    int(time.time() * 1000),
                    opp,
                    f"Agent 20: Auto-Hedge ({hedge_side} {live_line}) locking in ${profit:.2f}."
                ))

                # Delete this opportunity so we don't double execute
                cursor.execute("DELETE FROM hedging_opportunities WHERE id = ?", (id_opp,))
                logger.info(f"✅ Agent 20: Automated hedge bet placed successfully for Bet #{bet_id} (${hedge_stake} @ {live_odds:+d})")

    except Exception as e:
        logger.error(f"Error executing hedges: {e}")

def hedge_execution_loop():
    time.sleep(12)
    while True:
        try:
            check_and_execute_hedges()
        except Exception as e:
            logger.error(f"Error in hedge execution loop: {e}")
        time.sleep(10) # check for execution wagers every 10 seconds

if __name__ == "__main__":
    logger.info("Starting Agent 20 (Auto-Execution Hedging Engine)...")
    
    # Run loop in background thread
    exec_thread = threading.Thread(target=hedge_execution_loop, daemon=True)
    exec_thread.start()
    
    # Start FastAPI on port 8016
    uvicorn.run(app, host="0.0.0.0", port=8016)
