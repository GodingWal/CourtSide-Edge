import time
import logging
import threading
from fastapi import FastAPI
import uvicorn
from shared.redis_client import StreamConsumer
from shared.audit_logger import AuditLogger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Agent4_ExecutionOracle')

audit = AuditLogger()

app = FastAPI(title='Execution Oracle API')
execution_log = []
MAX_DRAWDOWN = 0.15
current_drawdown = 0.0
MIN_CONFIDENCE = 0.5  # Minimum confidence gate


@app.get('/health')
def health_check():
    return {
        'status': 'healthy',
        'circuit_breaker': current_drawdown >= MAX_DRAWDOWN,
        'min_confidence_gate': MIN_CONFIDENCE
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
    
    logger.info(f'Received execution order (trace: {trace_id[:8]}..., confidence: {confidence})')
    
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


def start_stream_consumer():
    stream = StreamConsumer(group_name='agent_4_group', consumer_name='agent_4_worker')
    logger.info('Subscribing to stream_execution_queue')
    
    stream.consume(
        'stream_execution_queue',
        lambda msg_id, m: on_execution_order(msg_id, m, stream)
    )
    
    try:
        while True:
            time.sleep(1)
    except Exception as e:
        logger.error(f'Stream consumer error: {e}')
        stream.close()


if __name__ == '__main__':
    logger.info('Agent 4 (P&L Oracle & Execution) started.')
    
    consumer_thread = threading.Thread(target=start_stream_consumer, daemon=True)
    consumer_thread.start()
    
    uvicorn.run(app, host='0.0.0.0', port=8001)
