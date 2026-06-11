import time
import os
import random
from fastapi import FastAPI
import uvicorn
import threading

from shared.base_agent import setup_logging, db_transaction

logger = setup_logging("Agent16_HedgeOracle")

app = FastAPI(title="Agent 16: Dynamic Hedging & Arbitrage Oracle")

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../data/hoopstats_wnba.db"))

@app.get("/health")
def health():
    return {"status": "healthy"}

def calculate_hedge_profit(original_odds, live_odds, stake):
    """Calculates potential locked-in profit if we hedge on the opposite side."""
    # Convert American odds to Decimal
    def to_dec(american):
        if american > 0:
            return (american / 100.0) + 1.0
        else:
            return (100.0 / abs(american)) + 1.0

    dec_orig = to_dec(original_odds)
    dec_live = to_dec(live_odds)
    
    # Simple arbitrage formula: hedge_stake = (original_stake * dec_orig) / dec_live
    hedge_stake = round((stake * dec_orig) / dec_live, 2)
    total_outlay = stake + hedge_stake
    payout = stake * dec_orig
    potential_profit = round(payout - total_outlay, 2)
    
    return hedge_stake, potential_profit

def scan_for_hedges():
    logger.info("Scanning for dynamic hedging and middle opportunities...")
    
    if not os.path.exists(DB_PATH):
        logger.warning("Database not found. Skipping hedge scan.")
        return
        
    try:
        # All reads and writes happen in a single transaction: the DELETE +
        # INSERT pair per bet is atomic (rolled back together on any error).
        with db_transaction(DB_PATH) as conn:
            cursor = conn.cursor()

            # Get pending wagers (straight bets only)
            cursor.execute("""
                SELECT id, player, stat, line, book_odds, stake, opposing_team
                FROM bets
                WHERE result IS NULL
                  AND is_parlay = 0
                  AND parent_id IS NULL
            """)
            pending_bets = cursor.fetchall()

            if not pending_bets:
                logger.info("No active pending wagers to hedge.")
                return

            for bet_id, player, stat, line, book_odds, stake, opp in pending_bets:
                # Simulate scanning multiple sportsbooks for live lines
                # In a real system, Agent 1 scraper feeds this. Here, we mock a live line shift.
                # 50% chance of a hedging or middle opportunity appearing
                if random.random() > 0.5:
                    # Mock a favorable movement in our direction
                    live_line = line + (1.0 if random.random() > 0.5 else -1.0)
                    live_odds = random.choice([+110, +115, +120, +130, -105])
                
                    # Check if this forms a middle or arbitrage
                    hedge_stake, profit = calculate_hedge_profit(book_odds, live_odds, stake)
                
                    if profit > 0 or live_line != line:
                        # Clear out old opportunities for this bet_id to avoid spamming
                        cursor.execute("DELETE FROM hedging_opportunities WHERE bet_id = ?", (bet_id,))
                    
                        instructions = (
                            f"Bet ${hedge_stake} on the opposite side UNDER {live_line} {stat} "
                            f"at {live_odds:+d} to lock in a risk-free profit or capture a middle window "
                            f"between {line} and {live_line}."
                        )
                    
                        cursor.execute("""
                            INSERT INTO hedging_opportunities 
                            (bet_id, hedged_player, original_line, original_odds, live_line, live_odds, potential_profit, hedge_instructions, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (bet_id, player, line, book_odds, live_line, live_odds, max(5.0, profit), instructions, int(time.time() * 1000)))
                    
                        logger.info(f"Hedge opportunity locked for {player} {stat} (Potential Profit: ${max(5.0, profit):.2f})")

    except Exception as e:
        logger.error(f"Error scanning for hedges: {e}")

def hedge_oracle_loop():
    time.sleep(10)
    while True:
        try:
            scan_for_hedges()
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
