import time
import logging
import sqlite3
import os
from fastapi import FastAPI
import uvicorn
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Agent20_HedgeExecutor")

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
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Select all unexecuted hedging opportunities
        cursor.execute("""
            SELECT id, bet_id, hedged_player, original_line, original_odds, live_line, live_odds, potential_profit, hedge_instructions 
            FROM hedging_opportunities
        """)
        rows = cursor.fetchall()
        
        for id_opp, bet_id, player, orig_line, orig_odds, live_line, live_odds, profit, instructions in rows:
            logger.info(f"🚨 Agent 20: Initiating automated hedge execution for Bet #{bet_id} ({player})")
            
            # Fetch original bet details to get stat and opposing team
            cursor.execute("SELECT stat, opposing_team FROM bets WHERE id = ?", (bet_id,))
            bet_details = cursor.fetchone()
            stat = bet_details[0] if bet_details else "PTS"
            opp = bet_details[1] if bet_details else "OPP"
            
            # Calculate dynamic hedge stake based on original stake and odds
            cursor.execute("SELECT stake FROM bets WHERE id = ?", (bet_id,))
            orig_stake_row = cursor.fetchone()
            orig_stake = orig_stake_row[0] if orig_stake_row else 100.0
            
            # Convert live odds to decimal to calculate stake
            def to_dec(american):
                if american > 0:
                    return (american / 100.0) + 1.0
                else:
                    return (100.0 / abs(american)) + 1.0
                    
            dec_orig = to_dec(orig_odds)
            dec_live = to_dec(live_odds)
            hedge_stake = round((orig_stake * dec_orig) / dec_live, 2)
            
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
                "UNDER" if orig_odds < 0 else "OVER", # opposite side
                live_odds,
                hedge_stake,
                int(time.time() * 1000),
                opp,
                f"Agent 20: Auto-Hedge placed to secure +${profit:.2f} EV."
            ))
            
            # Delete this opportunity so we don't double execute
            cursor.execute("DELETE FROM hedging_opportunities WHERE id = ?", (id_opp,))
            logger.info(f"✅ Agent 20: Automated hedge bet placed successfully for Bet #{bet_id} (${hedge_stake} @ {live_odds:+d})")
            
        conn.commit()
        conn.close()
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
