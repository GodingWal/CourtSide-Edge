import json
import os
import time
import urllib.request

from shared.odds_math import american_to_decimal
from shared.redis_client import StreamConsumer
from shared.audit_logger import AuditLogger

from shared.base_agent import run_polling_loop, setup_logging, db_connect

logger = setup_logging('Agent8_BankrollSizer')

audit = AuditLogger()
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../data/hoopstats_wnba.db'))
KELLY_MAX_FRACTION = float(os.environ.get('KELLY_MAX_FRACTION', '0.03'))
# Agent 18 (Liquidity Oracle). In docker compose the service name resolves;
# localhost only works when both agents run natively on one host.
AGENT18_URL = os.environ.get('AGENT18_URL', 'http://localhost:8014')
DEFAULT_BANKROLL = float(os.environ.get('DEFAULT_BANKROLL', '1000.0'))
BANKROLL_REFRESH_SECONDS = 60


class BankrollSizer:
    def __init__(self, redis_client=None):
        self.redis_client = redis_client
        self._bankroll = DEFAULT_BANKROLL
        self._bankroll_fetched_at = 0.0

    @property
    def bankroll(self) -> float:
        """Current bankroll from the real ledger, cached briefly.

        Prefers the latest bankroll_history row in SQLite (web tier); falls
        back to the bankroll:current key the web server mirrors into Redis
        (agent tier); only then the configured default.
        """
        now = time.time()
        if now - self._bankroll_fetched_at < BANKROLL_REFRESH_SECONDS:
            return self._bankroll
        self._bankroll_fetched_at = now
        balance = self._fetch_bankroll_sqlite()
        if balance is None:
            balance = self._fetch_bankroll_redis()
        if balance is not None and balance > 0:
            self._bankroll = balance
        else:
            logger.warning(f'No real bankroll found; using default ${self._bankroll:.2f}')
        return self._bankroll

    def _fetch_bankroll_sqlite(self):
        try:
            if not os.path.exists(DB_PATH):
                return None
            conn = db_connect(DB_PATH)
            row = conn.execute(
                'SELECT balance FROM bankroll_history ORDER BY timestamp DESC LIMIT 1'
            ).fetchone()
            conn.close()
            return float(row[0]) if row else None
        except Exception as e:
            logger.warning(f'Could not read bankroll from SQLite: {e}')
            return None

    def _fetch_bankroll_redis(self):
        if self.redis_client is None:
            return None
        try:
            raw = self.redis_client.get('bankroll:current')
            return float(raw) if raw else None
        except Exception as e:
            logger.warning(f'Could not read bankroll from Redis: {e}')
            return None

    def _fetch_recent_win_rate(self, n: int = 50) -> float:
        """Query SQLite for the realized win rate over the last N settled bets."""
        try:
            if not os.path.exists(DB_PATH):
                return 0.55  # Default fallback
            conn = db_connect(DB_PATH)
            cursor = conn.execute(
                """SELECT result FROM bets
                   WHERE result IN ('WIN', 'LOSS') AND is_parlay != 1 AND parent_id IS NULL
                   ORDER BY settled_at DESC LIMIT ?""",
                (n,)
            )
            results = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            if len(results) < 10:
                return 0.55  # Not enough data, use conservative default
            
            wins = sum(1 for r in results if r == 'WIN')
            return wins / len(results)
        except Exception as e:
            logger.error(f'Error fetching win rate: {e}')
            return 0.55
        
    def calculate_sizing(self, edge_data) -> tuple[float, str]:
        # Real model probability and posted price are REQUIRED. Sizing used to
        # default to p=0.55 @ -110, which made every "Kelly" stake fictional.
        try:
            prob_win = float(edge_data['true_odds'])
            book_odds = int(edge_data['book_odds'])
        except (KeyError, TypeError, ValueError):
            return 0.0, 'MISSING_PRICING'
        if not 0.0 < prob_win < 1.0:
            return 0.0, 'MISSING_PRICING'
        try:
            decimal_odds = american_to_decimal(book_odds)
        except ValueError:
            return 0.0, 'MISSING_PRICING'
        
        # Scale Kelly by signal confidence
        signal_confidence = edge_data.get('confidence', 0.5)
        
        # Pull realized performance from DB
        recent_win_rate = self._fetch_recent_win_rate(n=50)
        logger.info(f'Recent win rate (last 50): {recent_win_rate:.1%}')
        
        # Adaptive Kelly fraction based on recent performance
        if recent_win_rate >= 0.60:
            kelly_fraction = 0.25
            regime = 'HOT_STREAK'
        elif recent_win_rate >= 0.50:
            kelly_fraction = 0.167
            regime = 'NORMAL'
        elif recent_win_rate >= 0.48:
            kelly_fraction = 0.10
            regime = 'COLD_STREAK'
        else:
            kelly_fraction = 0.0
            regime = 'HALTED'
            return 0.0, regime
        logger.info(f'Kelly regime: {regime}, fraction: {kelly_fraction}, max cap: {KELLY_MAX_FRACTION}')
        
        # Kelly formula: f = (bp - q) / b
        b = decimal_odds - 1
        q = 1 - prob_win
        kelly_f = ((b * prob_win) - q) / b
        
        # Apply fractional Kelly scaled by confidence
        adjusted_kelly = kelly_f * kelly_fraction * signal_confidence
        
        # Cap at max Kelly fraction of bankroll
        bet_fraction = min(max(adjusted_kelly, 0), KELLY_MAX_FRACTION)
        if bet_fraction <= 0:
            return 0.0, 'NEGATIVE_EV'
            
        base_size = round(self.bankroll * bet_fraction, 2)
        
        # Cap based on Agent 18 (Liquidity Oracle) limits if available
        book = edge_data.get('book', 'FanDuel')
        max_book_limit = 250.0 # default fallback
        try:
            with urllib.request.urlopen(f"{AGENT18_URL}/limits", timeout=1) as response:
                limits = json.loads(response.read().decode())
                if book in limits:
                    max_book_limit = limits[book].get("max_limit", 250.0)
                    logger.info(f"  → Fetched limit for {book} from Agent 18: ${max_book_limit}")
        except Exception:
            # Fallback to local default limits if Agent 18 is offline
            logger.warning("Agent 18 limits offline. Using default book limits.")
            local_limits = {"Pinnacle": 2000.0, "Circa": 1500.0, "FanDuel": 250.0, "DraftKings": 200.0}
            max_book_limit = local_limits.get(book, 250.0)

        final_size = min(base_size, max_book_limit)
        if final_size < base_size:
            logger.info(f"  → Sizing capped by book limit from ${base_size} to ${final_size} on {book}")
            
        return final_size, regime


def on_approved_edge(msg_id, message, stream_producer, sizer):
    trace_id = message.get('trace_id', 'unknown')
    logger.info(f'Received approved edge (trace: {trace_id[:8]}...)')
    
    bet_amount, regime = sizer.calculate_sizing(message)

    if regime == 'MISSING_PRICING':
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_8',
            action='ABSTAIN',
            reason='Edge carries no real model probability/book price - refusing to size on defaults.',
            input_payload=message,
            confidence=message.get('confidence', 0.5)
        )
        logger.warning('Edge abstained: missing true_odds/book_odds (no fabricated sizing).')
        return

    if regime == 'HALTED':
        logger.error('⛔ SIZING HALTED: Win rate below 48%. All bets suspended.')
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_8',
            action='HALT',
            reason=f'Win rate below 48% threshold. Regime: {regime}',
            input_payload=message,
            confidence=message.get('confidence', 0.5)
        )
        return
    
    if bet_amount > 0:
        execution_order = {
            'source': 'Agent 8',
            'edge': message,
            'recommended_bet_amount': bet_amount,
            'kelly_regime': regime,
            'confidence': message.get('confidence', 0.5),
            'trace_id': trace_id,
            'timestamp': time.time()
        }
        
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_8',
            action='SIZE',
            reason=f'Sized at ${bet_amount} ({regime} regime, confidence-adjusted Kelly)',
            input_payload=message,
            output_payload=execution_order,
            confidence=message.get('confidence', 0.5)
        )
        
        logger.info(f'Publishing execution order: ${bet_amount} ({regime})')
        stream_producer.produce('stream_execution_queue', execution_order)
    else:
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_8',
            action='REJECT',
            reason=f'Negative Kelly sizing. Regime: {regime}',
            input_payload=message,
            confidence=message.get('confidence', 0.5)
        )
        logger.warning('Edge rejected due to negative Kelly sizing')


def main():
    stream = StreamConsumer(group_name='agent_8_group', consumer_name='agent_8_worker')
    sizer = BankrollSizer(redis_client=stream.client)
    logger.info('Agent 8 (Bankroll & Kelly Sizer) started.')
    
    stream.consume(
        'stream_approved_edges',
        lambda msg_id, m: on_approved_edge(msg_id, m, stream, sizer)
    )
    
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        stream.close()

if __name__ == '__main__':
    main()
