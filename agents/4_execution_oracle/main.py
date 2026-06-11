import time
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import StreamConsumer, RedisPubSub
from shared.audit_logger import AuditLogger

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging('Agent4_ExecutionOracle')

audit = AuditLogger()

app = FastAPI(title='Execution Oracle API')
execution_log = []
MAX_DRAWDOWN = 0.15
current_drawdown = 0.0
MIN_CONFIDENCE = 0.5  # Minimum confidence gate

# ── Health & Game State tracking ─────────────────────────────────────────────
# Track upstream data health status (default to "OK" to allow startup grace period)
upstream_health = {
    "channel_live_odds": "OK",
    "channel_true_projections": "OK",
    "channel_ev_alerts": "OK"
}

# Track active games by their game ID
active_games = {}
state_lock = threading.Lock()


@app.get('/health')
def health_check():
    with state_lock:
        is_stale = any(status in ("STALE", "DEAD") for status in upstream_health.values())
    return {
        'status': 'healthy',
        'circuit_breaker': current_drawdown >= MAX_DRAWDOWN,
        'min_confidence_gate': MIN_CONFIDENCE,
        'upstream_healthy': not is_stale,
        'upstream_status': upstream_health,
        'active_games': active_games
    }


@app.get('/log')
def get_log():
    return {'executions': execution_log[-50:]}


@app.get('/api/audit/recent')
def recent_audit():
    """Return recent audit trail entries."""
    return {'recent': execution_log[-20:]}


def on_execution_order(msg_id, message, stream):
    global current_drawdown
    trace_id = message.get('trace_id', 'unknown')
    confidence = message.get('confidence', 0.5)
    
    edge = message.get('edge', {})
    game_id = edge.get('game_id', 'UNKNOWN')
    
    logger.info(f'Received execution order (trace: {trace_id[:8]}..., game: {game_id}, confidence: {confidence})')
    
    # 1. Upstream Data Watchdog Gate Check
    with state_lock:
        stale_channels = [ch for ch, stat in upstream_health.items() if stat in ("STALE", "DEAD")]
    
    if stale_channels:
        reason = f'Blocked execution: Upstream channels are unhealthy/stale: {stale_channels}'
        logger.error(f'⛔ {reason}')
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_4',
            action='REJECT',
            reason=reason,
            input_payload=message,
            confidence=confidence
        )
        return
        
    # 2. Game Session active Gate Check
    with state_lock:
        game_status = active_games.get(game_id, 'UNKNOWN')
        
    if game_status != 'LIVE':
        reason = f'Blocked execution: Game {game_id} status is {game_status} (expected LIVE)'
        logger.error(f'⛔ {reason}')
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_4',
            action='REJECT',
            reason=reason,
            input_payload=message,
            confidence=confidence
        )
        return

    # Circuit breaker check
    if current_drawdown >= MAX_DRAWDOWN:
        logger.error('⛔ CIRCUIT BREAKER ACTIVE: Max drawdown reached. Bet aborted.')
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_4',
            action='REJECT',
            reason=f'Circuit breaker active: drawdown {current_drawdown:.1%} >= {MAX_DRAWDOWN:.0%}',
            input_payload=message,
            confidence=confidence
        )
        return
    
    # Confidence gate
    if confidence < MIN_CONFIDENCE:
        logger.warning(f'⚠️ Confidence {confidence} below minimum {MIN_CONFIDENCE}. Bet rejected.')
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_4',
            action='REJECT',
            reason=f'Confidence {confidence:.2f} below minimum gate {MIN_CONFIDENCE}',
            input_payload=message,
            confidence=confidence
        )
        return
    
    # Execute the bet
    bet_amount = message.get('recommended_bet_amount', 0)
    logger.info(f'✅ EXECUTING BET: ${bet_amount} (confidence: {confidence}, trace: {trace_id[:8]}...)')
    
    execution_record = {
        **message,
        'executed_at': time.time(),
        'status': 'EXECUTED'
    }
    execution_log.append(execution_record)
    
    audit.log_decision(
        trace_id=trace_id,
        agent_id='Agent_4',
        action='EXECUTE',
        reason=f'Executed ${bet_amount} bet. Drawdown: {current_drawdown:.1%}, Confidence: {confidence:.2f}',
        input_payload=message,
        output_payload=execution_record,
        confidence=confidence
    )


def process_health_message(msg):
    channel = msg.get("channel")
    status = msg.get("status")
    if channel in upstream_health:
        with state_lock:
            upstream_health[channel] = status
        logger.info(f"Updated watch status for {channel} to {status}")


def process_game_active_message(msg):
    game_id = msg.get("gameId")
    status = msg.get("status")
    if game_id:
        with state_lock:
            active_games[game_id] = status
        logger.info(f"Updated game status for {game_id} to {status}")


def start_stream_consumer():
    # Start Redis Pub/Sub subscription for health and game status
    pubsub = RedisPubSub()
    pubsub.subscribe("channel_system_health", process_health_message)
    pubsub.subscribe("channel_game_active", process_game_active_message)
    
    # Start Redis Stream consumer for execution queue
    stream = StreamConsumer(group_name='agent_4_group', consumer_name='agent_4_worker')
    logger.info('Subscribing to stream_execution_queue')
    
    stream.consume(
        'stream_execution_queue',
        lambda msg_id, m: on_execution_order(msg_id, m, stream)
    )
    
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except Exception as e:
        logger.error(f'Stream consumer error: {e}')
        stream.close()
        pubsub.close()


if __name__ == '__main__':
    logger.info('Agent 4 (P&L Oracle & Execution) started.')
    
    consumer_thread = threading.Thread(target=start_stream_consumer, daemon=True)
    consumer_thread.start()
    
    uvicorn.run(app, host='0.0.0.0', port=8001)
