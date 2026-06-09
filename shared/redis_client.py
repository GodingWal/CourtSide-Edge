import redis
import json
import os
from typing import Callable, Any

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

class RedisPubSub:
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
