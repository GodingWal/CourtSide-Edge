import time
import logging
from shared.redis_client import StreamConsumer
from shared.audit_logger import AuditLogger

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('Agent7_CorrelationGuard')

audit = AuditLogger()

class CorrelationGuard:
    def __init__(self):
        self.active_game_exposures = {}
        
    def check_correlation(self, edge_data):
        game_id = edge_data.get('game_id', 'UNKNOWN')
        confidence = edge_data.get('confidence', 0.5)
        exposure = self.active_game_exposures.get(game_id, 0)
        
        # Dynamic exposure limit based on confidence
        max_exposure = 4 if confidence > 0.85 else 3
        
        if exposure >= max_exposure:
            logger.warning(f'Rejecting edge for game {game_id}: exposure {exposure} >= max {max_exposure}')
            return False, f'Game exposure {exposure} exceeds max {max_exposure}'
            
        self.active_game_exposures[game_id] = exposure + 1
        return True, f'Game exposure now {exposure + 1}/{max_exposure}'

def on_market_intelligence(msg_id, message, stream_producer, guard):
    trace_id = message.get('trace_id', 'unknown')
    logger.info(f'Received market edge (trace: {trace_id[:8]}...)')
    
    approved, reason = guard.check_correlation(message)
    
    if approved:
        message['approved_by'] = 'Agent 7'
        
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_7',
            action='APPROVE',
            reason=reason,
            input_payload=message,
            output_payload=message,
            confidence=message.get('confidence', 0.5)
        )
        
        logger.info(f'Edge approved, publishing to stream_approved_edges')
        stream_producer.produce('stream_approved_edges', message)
    else:
        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_7',
            action='REJECT',
            reason=reason,
            input_payload=message,
            confidence=message.get('confidence', 0.5)
        )

def main():
    stream = StreamConsumer(group_name='agent_7_group', consumer_name='agent_7_worker')
    guard = CorrelationGuard()
    logger.info('Agent 7 (Correlation Guard) started.')
    
    # Consume from Redis Stream instead of Pub/Sub
    stream.consume(
        'stream_market_intelligence',
        lambda msg_id, m: on_market_intelligence(msg_id, m, stream, guard)
    )
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stream.close()

if __name__ == '__main__':
    main()
