import os
import time
import sqlite3
import logging
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import RedisPubSub

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Agent14_CLVTracker')

app = FastAPI(title='Agent 14: CLV Tracker')

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/hoopstats_wnba.db'))
pubsub = None

# In-memory cache of opening lines at bet placement
bet_opening_lines: dict = {}


def to_implied_prob(american: int) -> float:
    """Convert American odds to implied probability."""
    if american > 0:
        return 100.0 / (american + 100.0)
    else:
        return abs(american) / (abs(american) + 100.0)


def calculate_clv(opening_odds: int, closing_odds: int) -> float:
    """Calculate CLV percentage.
    Positive CLV means the line moved in your favor (you got a better number).
    """
    opening_prob = to_implied_prob(opening_odds)
    closing_prob = to_implied_prob(closing_odds)
    return round((closing_prob - opening_prob) / opening_prob * 100, 2)


@app.get('/health')
def health():
    return {'status': 'healthy'}


@app.get('/api/clv/summary')
def clv_summary():
    """Return aggregate CLV statistics."""
    try:
        if not os.path.exists(DB_PATH):
            return {'error': 'Database not found'}
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute(
            'SELECT clv_pct, stat, result FROM bets WHERE clv_pct IS NOT NULL AND parent_id IS NULL'
        )
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return {
                'total_tracked': 0,
                'avg_clv': 0.0,
                'positive_clv_pct': 0.0,
                'clv_by_stat': {},
                'clv_by_result': {}
            }
        
        total = len(rows)
        avg_clv = round(sum(r[0] for r in rows) / total, 2)
        positive = sum(1 for r in rows if r[0] > 0)
        
        # CLV by stat category
        stat_clv: dict = {}
        for clv, stat, _ in rows:
            if stat:
                if stat not in stat_clv:
                    stat_clv[stat] = []
                stat_clv[stat].append(clv)
        clv_by_stat = {k: round(sum(v) / len(v), 2) for k, v in stat_clv.items()}
        
        # CLV by result
        result_clv: dict = {}
        for clv, _, result in rows:
            if result:
                if result not in result_clv:
                    result_clv[result] = []
                result_clv[result].append(clv)
        clv_by_result = {k: round(sum(v) / len(v), 2) for k, v in result_clv.items()}
        
        return {
            'total_tracked': total,
            'avg_clv': avg_clv,
            'positive_clv_pct': round(positive / total * 100, 1),
            'clv_by_stat': clv_by_stat,
            'clv_by_result': clv_by_result
        }
    except Exception as e:
        logger.error(f'Error computing CLV summary: {e}')
        return {'error': str(e)}


@app.post('/api/clv/record')
def record_closing_line(data: dict):
    """Record closing odds for a bet and calculate CLV."""
    bet_id = data.get('bet_id')
    closing_odds = data.get('closing_odds')
    
    if not bet_id or closing_odds is None:
        return {'error': 'bet_id and closing_odds required'}
    
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.execute('SELECT book_odds FROM bets WHERE id = ?', (bet_id,))
        row = cursor.fetchone()
        
        if not row:
            conn.close()
            return {'error': f'Bet {bet_id} not found'}
        
        opening_odds = row[0]
        clv_pct = calculate_clv(opening_odds, closing_odds)
        
        conn.execute(
            'UPDATE bets SET closing_odds = ?, clv_pct = ? WHERE id = ?',
            (closing_odds, clv_pct, bet_id)
        )
        conn.commit()
        conn.close()
        
        logger.info(f'Recorded CLV for bet {bet_id}: opening={opening_odds}, closing={closing_odds}, CLV={clv_pct}%')
        return {
            'bet_id': bet_id,
            'opening_odds': opening_odds,
            'closing_odds': closing_odds,
            'clv_pct': clv_pct
        }
    except Exception as e:
        logger.error(f'Error recording CLV: {e}')
        return {'error': str(e)}


def on_live_odds(message):
    """When live odds update comes in, check if any active bets need CLV recorded."""
    logger.info(f'Agent 14 received live odds update for CLV tracking')
    
    try:
        if not os.path.exists(DB_PATH):
            return
        
        conn = sqlite3.connect(DB_PATH)
        # Find unsettled bets that don't yet have closing odds recorded
        cursor = conn.execute(
            'SELECT id, book_odds, player, stat FROM bets WHERE closing_odds IS NULL AND result IS NULL AND is_parlay != 1 AND parent_id IS NULL'
        )
        pending = cursor.fetchall()
        
        for bet_id, opening_odds, player, stat in pending:
            # Simulate closing line detection from the odds update
            # In production, this would match player/stat from the message payload
            incoming_player = message.get('player', '')
            incoming_stat = message.get('stat', '')
            incoming_closing = message.get('closing_odds')
            
            if incoming_closing and incoming_player == player and incoming_stat == stat:
                clv_pct = calculate_clv(opening_odds, incoming_closing)
                conn.execute(
                    'UPDATE bets SET closing_odds = ?, clv_pct = ? WHERE id = ?',
                    (incoming_closing, clv_pct, bet_id)
                )
                logger.info(f'Auto-recorded CLV for bet {bet_id}: {clv_pct}%')
        
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f'Error in CLV auto-tracker: {e}')


def start_redis_listener():
    global pubsub
    pubsub = RedisPubSub()
    pubsub.subscribe('channel_live_odds', on_live_odds)
    logger.info('Subscribed to channel_live_odds for CLV tracking')
    try:
        while True:
            time.sleep(1)
    except Exception as e:
        logger.error(f'Redis listener error: {e}')


if __name__ == '__main__':
    logger.info('Agent 14 (CLV Tracker) starting...')
    
    listener_thread = threading.Thread(target=start_redis_listener, daemon=True)
    listener_thread.start()
    
    uvicorn.run(app, host='0.0.0.0', port=8010)
