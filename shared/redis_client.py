import redis
import json
import os
import time
import logging
import threading
from typing import Callable, Any, Optional

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

logger = logging.getLogger('RedisClient')


class RedisPubSub:
    """Standard Redis Pub/Sub for non-critical informational channels."""
    def __init__(self):
        self.client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        self.pubsub = self.client.pubsub()

    def publish(self, channel: str, message: Any):
        """Publish a message to a Redis channel."""
        self.client.publish(channel, json.dumps(message))

    def subscribe(self, channel: str, callback: Callable[[dict], None]):
        """Subscribe to a Redis channel and execute callback on new messages."""
        self.pubsub.subscribe(**{channel: lambda m: callback(json.loads(m['data']))})
        
        # Start a thread to listen for messages
        self.thread = self.pubsub.run_in_thread(sleep_time=0.001)

    def close(self):
        """Close the pubsub connection."""
        if hasattr(self, 'thread'):
            self.thread.stop()
        self.pubsub.close()
        self.client.close()


class StreamConsumer:
    """Redis Streams consumer with consumer groups, acknowledgment, and dead-letter queue support.
    
    Used for critical-path channels (market_intelligence -> approved_edges -> execution_queue)
    where message loss is unacceptable.
    """
    
    def __init__(self, group_name: str, consumer_name: str):
        self.client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        self.group_name = group_name
        self.consumer_name = consumer_name
        self._running = False
        self._threads: list[threading.Thread] = []

    def _ensure_group(self, stream: str):
        """Create consumer group if it doesn't exist."""
        try:
            self.client.xgroup_create(stream, self.group_name, id='0', mkstream=True)
            logger.info(f'Created consumer group {self.group_name} on stream {stream}')
        except redis.ResponseError as e:
            if 'BUSYGROUP' not in str(e):
                raise

    def produce(self, stream: str, message: Any) -> str:
        """Add a message to a Redis stream. Returns the message ID."""
        payload = json.dumps(message) if not isinstance(message, str) else message
        msg_id = self.client.xadd(stream, {'data': payload})
        return msg_id

    def consume(self, stream: str, callback: Callable[[str, dict], None], 
                block_ms: int = 5000, batch_size: int = 1):
        """Consume messages from a stream using consumer groups.
        
        The callback receives (message_id, parsed_data).
        Messages are auto-acknowledged after successful callback execution.
        Failed messages are moved to a dead-letter queue.
        """
        self._ensure_group(stream)
        self._running = True
        
        def _consume_loop():
            # First, process any pending (unacknowledged) messages
            try:
                pending = self.client.xreadgroup(
                    self.group_name, self.consumer_name,
                    {stream: '0'}, count=10
                )
                if pending:
                    for stream_name, messages in pending:
                        for msg_id, msg_data in messages:
                            if msg_data:  # Skip empty entries
                                try:
                                    data = json.loads(msg_data.get('data', '{}'))
                                    callback(msg_id, data)
                                    self.client.xack(stream, self.group_name, msg_id)
                                    logger.info(f'Recovered pending message {msg_id} on {stream}')
                                except Exception as e:
                                    logger.error(f'Failed to process pending message {msg_id}: {e}')
                                    self._move_to_dlq(stream, msg_id, msg_data, str(e))
            except Exception as e:
                logger.error(f'Error processing pending messages: {e}')

            # Main consumption loop
            while self._running:
                try:
                    results = self.client.xreadgroup(
                        self.group_name, self.consumer_name,
                        {stream: '>'}, count=batch_size, block=block_ms
                    )
                    if not results:
                        continue
                    
                    for stream_name, messages in results:
                        for msg_id, msg_data in messages:
                            try:
                                data = json.loads(msg_data.get('data', '{}'))
                                callback(msg_id, data)
                                self.client.xack(stream, self.group_name, msg_id)
                            except Exception as e:
                                logger.error(f'Error processing message {msg_id}: {e}')
                                self._move_to_dlq(stream, msg_id, msg_data, str(e))
                except Exception as e:
                    if self._running:
                        logger.error(f'Stream consumer error on {stream}: {e}')
                        time.sleep(1)  # Backoff on error

        thread = threading.Thread(target=_consume_loop, daemon=True)
        thread.start()
        self._threads.append(thread)
        logger.info(f'Started consuming stream {stream} as {self.consumer_name} in group {self.group_name}')

    def _move_to_dlq(self, stream: str, msg_id: str, msg_data: dict, error: str):
        """Move a failed message to the dead-letter queue stream."""
        dlq_stream = f'{stream}_dlq'
        try:
            self.client.xadd(dlq_stream, {
                'original_stream': stream,
                'original_id': msg_id,
                'data': msg_data.get('data', ''),
                'error': error,
                'failed_at': str(int(time.time() * 1000))
            })
            # Acknowledge the original message so it doesn't block the group
            self.client.xack(stream, self.group_name, msg_id)
            logger.warning(f'Moved message {msg_id} to DLQ {dlq_stream}')
        except Exception as e:
            logger.error(f'Failed to move message to DLQ: {e}')

    def close(self):
        """Stop all consumer threads."""
        self._running = False
        for t in self._threads:
            t.join(timeout=2)
        self.client.close()
