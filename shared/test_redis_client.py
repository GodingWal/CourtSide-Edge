import unittest
import json
import time
from unittest.mock import patch
import fakeredis

from shared.redis_client import RedisPubSub, StreamConsumer

class TestRedisPubSub(unittest.TestCase):
    def setUp(self):
        self.server = fakeredis.FakeServer()

        def fake_redis_factory(*args, **kwargs):
            kwargs.pop('host', None)
            kwargs.pop('port', None)
            return fakeredis.FakeRedis(server=self.server, **kwargs)

        self.patcher = patch('redis.Redis', side_effect=fake_redis_factory)
        self.mock_redis = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_publish(self):
        pubsub = RedisPubSub()

        # We can inspect the fakeredis state by creating our own client pointing to the same server
        client = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        ps = client.pubsub()
        ps.subscribe('test_channel')

        # Publish a message
        test_message = {'key': 'value'}
        pubsub.publish('test_channel', test_message)

        # Wait a tiny bit for the message to propagate
        time.sleep(0.05)

        # First message is the subscription confirmation
        msg = ps.get_message()
        self.assertEqual(msg['type'], 'subscribe')

        # Second message is our actual data
        msg = ps.get_message()
        self.assertEqual(msg['type'], 'message')
        self.assertEqual(msg['channel'], 'test_channel')
        self.assertEqual(json.loads(msg['data']), test_message)

        pubsub.close()

    def test_subscribe(self):
        pubsub = RedisPubSub()

        received_messages = []
        def callback(msg):
            received_messages.append(msg)

        pubsub.subscribe('test_channel', callback)

        # Wait a tiny bit for the subscription thread to start
        time.sleep(0.05)

        publisher = RedisPubSub()
        test_message = {'event': 'test_event'}
        publisher.publish('test_channel', test_message)

        # Wait for the thread to process the message
        time.sleep(0.1)

        self.assertEqual(len(received_messages), 1)
        self.assertEqual(received_messages[0], test_message)

        publisher.close()
        pubsub.close()

    def test_close(self):
        pubsub = RedisPubSub()

        def callback(msg):
            pass

        pubsub.subscribe('test_channel', callback)

        # Ensure thread is running
        self.assertTrue(pubsub.thread.is_alive())

        # Close the connection
        pubsub.close()

        # Ensure thread stops
        pubsub.thread.join(timeout=1.0)
        self.assertFalse(pubsub.thread.is_alive())

class TestStreamConsumer(unittest.TestCase):
    def setUp(self):
        self.server = fakeredis.FakeServer()

        def fake_redis_factory(*args, **kwargs):
            kwargs.pop('host', None)
            kwargs.pop('port', None)
            return fakeredis.FakeRedis(server=self.server, **kwargs)

        self.patcher = patch('redis.Redis', side_effect=fake_redis_factory)
        self.mock_redis = self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_produce(self):
        consumer = StreamConsumer('test_group', 'test_consumer')

        msg_id = consumer.produce('test_stream', {'key': 'value'})
        self.assertIsNotNone(msg_id)

        # Verify it's in the stream
        client = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        messages = client.xread({'test_stream': '0'})
        self.assertEqual(len(messages), 1)

        stream_name, stream_data = messages[0]
        self.assertEqual(stream_name, 'test_stream')
        self.assertEqual(len(stream_data), 1)

        stored_id, stored_msg = stream_data[0]
        self.assertEqual(stored_id, msg_id)
        self.assertEqual(json.loads(stored_msg['data']), {'key': 'value'})

        consumer.close()

    def test_consume(self):
        consumer = StreamConsumer('test_group', 'test_consumer')

        received_messages = []
        def callback(msg_id, msg):
            received_messages.append((msg_id, msg))

        consumer.consume('test_stream', callback, block_ms=10)

        # Give thread time to start and wait for messages
        time.sleep(0.05)

        msg_id = consumer.produce('test_stream', {'hello': 'world'})

        # Give thread time to process
        time.sleep(0.1)

        self.assertEqual(len(received_messages), 1)
        self.assertEqual(received_messages[0][0], msg_id)
        self.assertEqual(received_messages[0][1], {'hello': 'world'})

        consumer.close()

    def test_pending_message_recovery(self):
        # We need to simulate a message that was read but not acknowledged
        client = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        client.xgroup_create('test_stream', 'test_group', id='0', mkstream=True)

        # Add a message to the stream
        msg_id = client.xadd('test_stream', {'data': json.dumps({'pending': 'message'})})

        # Read the message via group to put it in pending state
        client.xreadgroup('test_group', 'test_consumer', {'test_stream': '>'}, count=1)

        # Verify it is in pending state (unacknowledged)
        pending = client.xpending('test_stream', 'test_group')
        self.assertEqual(pending['pending'], 1)

        # Now start our consumer, which should recover the pending message
        consumer = StreamConsumer('test_group', 'test_consumer')

        received_messages = []
        def callback(m_id, msg):
            received_messages.append((m_id, msg))

        consumer.consume('test_stream', callback, block_ms=10)

        # Wait for recovery
        time.sleep(0.1)

        self.assertEqual(len(received_messages), 1)
        self.assertEqual(received_messages[0][0], msg_id)
        self.assertEqual(received_messages[0][1], {'pending': 'message'})

        # Verify the message was acknowledged and removed from pending
        pending = client.xpending('test_stream', 'test_group')
        self.assertEqual(pending['pending'], 0)

        consumer.close()

    def test_dead_letter_queue(self):
        consumer = StreamConsumer('test_group', 'test_consumer')

        # Callback that raises an exception
        def failing_callback(msg_id, msg):
            raise ValueError("Simulated processing error")

        consumer.consume('test_stream', failing_callback, block_ms=10)
        time.sleep(0.05)

        msg_id = consumer.produce('test_stream', {'bad': 'data'})

        # Wait for processing and DLQ routing
        time.sleep(0.1)

        # Check DLQ stream
        client = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        dlq_messages = client.xread({'test_stream_dlq': '0'})

        self.assertEqual(len(dlq_messages), 1)
        stream_name, stream_data = dlq_messages[0]
        self.assertEqual(stream_name, 'test_stream_dlq')
        self.assertEqual(len(stream_data), 1)

        stored_id, stored_msg = stream_data[0]
        self.assertEqual(stored_msg['original_stream'], 'test_stream')
        self.assertEqual(stored_msg['original_id'], msg_id)
        self.assertEqual(json.loads(stored_msg['data']), {'bad': 'data'})
        self.assertIn("Simulated processing error", stored_msg['error'])

        # Verify original message was acked so it doesn't block
        pending = client.xpending('test_stream', 'test_group')
        self.assertEqual(pending['pending'], 0)

        consumer.close()

    def test_close(self):
        consumer = StreamConsumer('test_group', 'test_consumer')

        def callback(msg_id, msg):
            pass

        consumer.consume('test_stream', callback, block_ms=10)

        self.assertEqual(len(consumer._threads), 1)
        thread = consumer._threads[0]
        self.assertTrue(thread.is_alive())

        consumer.close()

        self.assertFalse(thread.is_alive())


if __name__ == '__main__':
    unittest.main()
