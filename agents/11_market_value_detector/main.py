import json
import time
from shared.redis_client import RedisPubSub, StreamConsumer
from shared.audit_logger import AuditLogger, generate_trace_id

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging('Agent11_MarketValue')

audit = AuditLogger()

class MarketIntelligence:
    def __init__(self):
        self.line_history = {}

    def track_movement(self, odds_data):
        """Classify real observed movement by its velocity (units/min).

        Agent 1 only publishes when a line actually changed and includes the
        previous value, so each message is one genuine movement event. The
        velocity is that move divided by the time since the last update we saw
        for the same market.
        """
        prev = odds_data.get('prev_over_under') if odds_data.get('prev_over_under') is not None else odds_data.get('prev_line')
        curr = odds_data.get('over_under') if odds_data.get('over_under') is not None else odds_data.get('line')
        market = odds_data.get('player') or odds_data.get('game_id')
        stat = odds_data.get('stat') or ('TOTAL' if odds_data.get('over_under') is not None else 'LINE')
        ts = odds_data.get('timestamp', time.time())

        if market is None or curr is None:
            return 'noise', 0.35

        key = f'{market}:{stat}'
        last_ts = self.line_history.get(key)
        self.line_history[key] = ts

        if prev is None or last_ts is None or ts <= last_ts:
            return 'noise', 0.35  # opening line / first sighting: no velocity yet

        minutes = (ts - last_ts) / 60.0
        velocity = abs(curr - prev) / max(minutes, 1.0 / 60)
        if velocity > 0.8:
            return 'sharp_money', 0.92
        elif velocity > 0.5:
            return 'sharp_money', 0.75
        elif velocity > 0.2:
            return 'public_drift', 0.60
        else:
            return 'noise', 0.35

def lookup_true_line(pubsub, player, stat):
    """Agent 3's cached projection for this market, if one exists."""
    if not player or not stat:
        return None
    try:
        raw = pubsub.client.hget("props:projections", f"{player}|{stat}")
        return json.loads(raw)["projected_value"] if raw else None
    except Exception:
        return None


def on_live_odds(message, stream_producer, intelligence, pubsub):
    movement_type, confidence = intelligence.track_movement(message)
    logger.info(f'Tracking market movement: {movement_type} (confidence: {confidence})')
    
    # Skip noise-level signals
    if confidence < 0.4:
        logger.info(f'Signal too weak ({confidence}), skipping.')
        return
    
    # Derive divergence from the REAL observed movement: % change of the
    # market number (total/spread/line), scaled by signal confidence.
    prev = message.get("prev_over_under") if message.get("prev_over_under") is not None else message.get("prev_line")
    curr = message.get("over_under") if message.get("over_under") is not None else message.get("line")
    if prev in (None, 0) or curr is None:
        logger.info("No quantifiable line movement in message; skipping divergence alert.")
        return
    movement_pct = abs(curr - prev) / abs(prev) * 100
    divergence_score = round(movement_pct * confidence, 2)
    if divergence_score <= 0:
        return
    trace_id = generate_trace_id()
    
    alert = {
        'source': 'Agent 11',
        'type': 'market_divergence',
        'market_classification': movement_type,
        'game_id': message.get('game_id'),
        'player': message.get('player'),
        'stat': message.get('stat') or ('TOTAL' if message.get('over_under') is not None else None),
        'line': curr,
        'prev_line': prev,
        'odds': message.get('odds'),
        'book': message.get('book'),
        'true_line': lookup_true_line(pubsub, message.get('player'), message.get('stat')),
        'divergence_score': divergence_score,
        'confidence': confidence,
        'sample_size': len(intelligence.line_history),
        'decay_seconds': 300,
        'trace_id': trace_id,
        'timestamp': time.time()
    }
    
    # Log the decision to audit trail
    audit.log_decision(
        trace_id=trace_id,
        agent_id='Agent_11',
        action='APPROVE',
        reason=f'Detected {movement_type} with {confidence:.0%} confidence, divergence {divergence_score}%',
        input_payload=message,
        output_payload=alert,
        confidence=confidence
    )
    logger.info(f'Publishing market intelligence (trace: {trace_id[:8]}...)')
    # Use Redis Streams for critical-path channel
    stream_producer.produce('stream_market_intelligence', alert)

def on_sharp_move(message, stream_producer, pubsub):
    sharp_data = message.get("data", {})
    player = sharp_data.get("player")
    book = sharp_data.get("book", "Consensus")
    move = sharp_data.get("move", "")
    if not player or not move:
        return  # no real movement data — nothing to act on
    
    logger.info(f"Agent 11 received sharp move trigger from Agent 19: {player} on {book} {move}")

    # Quantify the move from the real prev → curr values; skip if unparseable.
    try:
        prev_str, curr_str = (part.strip() for part in move.split('→'))
        prev_line, curr_line = float(prev_str), float(curr_str)
    except (ValueError, AttributeError):
        logger.info(f"Unparseable sharp move '{move}' — skipping.")
        return
    if prev_line == 0:
        return
    confidence = message.get('confidence', 0.9)
    divergence_score = round(abs(curr_line - prev_line) / abs(prev_line) * 100 * confidence, 2)
    if divergence_score <= 0:
        return

    trace_id = generate_trace_id()

    alert = {
        'source': 'Agent 11',
        'type': 'market_divergence',
        'market_classification': 'sharp_money',
        'player': player,
        'stat': sharp_data.get('stat'),
        'line': curr_line,
        'prev_line': prev_line,
        'book': book,
        'true_line': lookup_true_line(pubsub, player, sharp_data.get('stat')),
        'divergence_score': divergence_score,
        'confidence': confidence,
        'sample_size': 1,
        'decay_seconds': 300,
        'trace_id': trace_id,
        'timestamp': time.time(),
    }
    
    audit.log_decision(
        trace_id=trace_id,
        agent_id='Agent_11',
        action='APPROVE',
        reason=f'Sharp book {book} moved line ({move}), divergence {divergence_score}%',
        input_payload=message,
        output_payload=alert,
        confidence=confidence
    )
    
    logger.info(f'Publishing market intelligence for sharp retail lag (trace: {trace_id[:8]}...)')
    stream_producer.produce('stream_market_intelligence', alert)

def main():
    pubsub = RedisPubSub()
    stream = StreamConsumer(group_name='agent_11_group', consumer_name='agent_11_worker')
    intelligence = MarketIntelligence()
    logger.info('Agent 11 (Market Value Detector) started.')
    
    pubsub.subscribe('channel_live_odds', lambda m: on_live_odds(m, stream, intelligence, pubsub))
    pubsub.subscribe('channel_sharp_moves', lambda m: on_sharp_move(m, stream, pubsub))
    
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        pubsub.close()
        stream.close()

if __name__ == '__main__':
    main()
