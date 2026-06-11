import json
import time

from shared.redis_client import StreamConsumer
from shared.audit_logger import AuditLogger

from shared.base_agent import setup_logging, run_polling_loop

logger = setup_logging('Agent7_CorrelationGuard')

audit = AuditLogger()

# Correlation thresholds for flagging stacked legs in the same game.
CORR_BONUS_THRESHOLD = 0.25   # positively correlated same-player legs
CORR_TRAP_THRESHOLD = -0.15   # negatively correlated legs
# Exposure entries expire once a game is long over; without expiry the
# counter only grows and permanently blocks a game_id after 3-4 edges.
EXPOSURE_TTL_SECONDS = 6 * 3600

class CorrelationGuard:
    def __init__(self, redis_client=None):
        self.active_game_exposures = {}   # game_id -> [exposure timestamps]
        self.game_legs = {}          # game_id -> [(player, stat), ...]
        self.redis_client = redis_client
        self._correlations = {}

    def _prune_expired(self):
        cutoff = time.time() - EXPOSURE_TTL_SECONDS
        for game_id in list(self.active_game_exposures):
            kept = [ts for ts in self.active_game_exposures[game_id] if ts >= cutoff]
            if kept:
                self.active_game_exposures[game_id] = kept
            else:
                self.active_game_exposures.pop(game_id, None)
                self.game_legs.pop(game_id, None)

    def _stat_corr(self, stat_a, stat_b):
        """League correlation between two stats (matrix published by Agent 3)."""
        if not self._correlations and self.redis_client is not None:
            try:
                raw = self.redis_client.get('stats:correlations')
                self._correlations = json.loads(raw) if raw else {}
            except Exception as e:
                logger.warning(f'Could not load correlation matrix: {e}')
                self._correlations = {}
        return self._correlations.get(f'{stat_a}|{stat_b}') or self._correlations.get(f'{stat_b}|{stat_a}')

    def classify_correlation(self, edge_data):
        """Flag this edge against legs already taken in the same game.

        Same-player positively correlated stats (e.g. PTS+AST in a
        high-usage game) -> correlation_bonus; negatively correlated
        combinations -> correlation_trap. Uses the real league correlation
        matrix Agent 3 derives from box scores; no matrix -> no flag.
        """
        game_id = edge_data.get('game_id', 'UNKNOWN')
        player, stat = edge_data.get('player'), edge_data.get('stat')
        flag, flagged_against = None, None
        if player and stat:
            for prev_player, prev_stat in self.game_legs.get(game_id, []):
                corr = self._stat_corr(stat, prev_stat)
                if corr is None:
                    continue
                if prev_player == player and corr >= CORR_BONUS_THRESHOLD:
                    flag, flagged_against = 'correlation_bonus', f'{prev_player} {prev_stat} (r={corr})'
                    break
                if corr <= CORR_TRAP_THRESHOLD:
                    flag, flagged_against = 'correlation_trap', f'{prev_player} {prev_stat} (r={corr})'
                    break
            self.game_legs.setdefault(game_id, []).append((player, stat))
        return flag, flagged_against

    def check_correlation(self, edge_data):
        self._prune_expired()
        game_id = edge_data.get('game_id', 'UNKNOWN')
        confidence = edge_data.get('confidence', 0.5)
        exposure = len(self.active_game_exposures.get(game_id, []))

        # Dynamic exposure limit based on confidence
        max_exposure = 4 if confidence > 0.85 else 3

        if exposure >= max_exposure:
            logger.warning(f'Rejecting edge for game {game_id}: exposure {exposure} >= max {max_exposure}')
            return False, f'Game exposure {exposure} exceeds max {max_exposure}'

        self.active_game_exposures.setdefault(game_id, []).append(time.time())
        return True, f'Game exposure now {exposure + 1}/{max_exposure}'

# `stream` is the same StreamConsumer used for consumption; it doubles as the
# producer for the downstream approved-edges stream.
def on_market_intelligence(msg_id, message, stream, guard):
    trace_id = message.get('trace_id', 'unknown')
    logger.info(f'Received market edge (trace: {trace_id[:8]}...)')
    
    approved, reason = guard.check_correlation(message)

    if approved:
        message['approved_by'] = 'Agent 7'

        # Annotate correlated-leg context for downstream parlay logic.
        flag, against = guard.classify_correlation(message)
        if flag:
            message['correlation_flag'] = flag
            message['correlated_with'] = against
            reason += f' | {flag} vs {against}'
            logger.info(f'Correlation flag: {flag} ({against})')

        audit.log_decision(
            trace_id=trace_id,
            agent_id='Agent_7',
            action='APPROVE',
            reason=reason,
            input_payload=message,
            output_payload=message,
            confidence=message.get('confidence', 0.5)
        )
        
        logger.info('Edge approved, publishing to stream_approved_edges')
        stream.produce('stream_approved_edges', message)
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
    guard = CorrelationGuard(redis_client=stream.client)
    logger.info('Agent 7 (Correlation Guard) started.')
    
    # Consume from Redis Stream instead of Pub/Sub
    stream.consume(
        'stream_market_intelligence',
        lambda msg_id, m: on_market_intelligence(msg_id, m, stream, guard)
    )
    
    try:
        # Idle keepalive: actual work happens in Redis callback threads.
        # Block in long interruptible waits instead of waking every second.
        run_polling_loop(interval=30.0)
    except KeyboardInterrupt:
        stream.close()

if __name__ == '__main__':
    main()
