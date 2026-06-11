import redis
import json
import os
import time
import logging
from typing import Any, Dict, List, Optional

from shared import db as shared_db

REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))
REDIS_PASSWORD = os.getenv('REDIS_PASSWORD') or None
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../data/hoopstats_wnba.db'))

logger = logging.getLogger('ContextClient')

class ContextClient:
    def __init__(self):
        try:
            self.redis = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, decode_responses=True)
            self.redis.ping()
            self.redis_available = True
        except Exception:
            self.redis_available = False
            logger.warning('Redis unavailable for context store. Using SQLite fallback only.')

    def write_context(self, game_id: str, agent_id: str, context_key: str, context_value: Any, confidence: float = 0.8, ttl_seconds: int = 3600):
        """Write a context entry to both Redis hash and SQLite."""
        payload = json.dumps(context_value) if not isinstance(context_value, str) else context_value
        
        # Write to Redis hash for fast in-memory reads
        if self.redis_available:
            try:
                hash_key = f'agent:context:{game_id}'
                field_key = f'{agent_id}:{context_key}'
                entry = json.dumps({
                    'value': context_value,
                    'confidence': confidence,
                    'agent_id': agent_id,
                    'timestamp': int(time.time() * 1000)
                })
                self.redis.hset(hash_key, field_key, entry)
                self.redis.expire(hash_key, ttl_seconds)
            except Exception as e:
                logger.error(f'Failed to write context to Redis: {e}')

        # Write to the database for persistence. The table has no unique key
        # on (game_id, agent_id, context_key) — entries accumulate and readers
        # take the newest by created_at, so a plain INSERT is correct (the old
        # INSERT OR REPLACE never replaced anything without that constraint).
        try:
            if shared_db.db_available(DB_PATH):
                conn = shared_db.connect(DB_PATH)
                conn.execute(
                    '''INSERT INTO agent_context_store
                       (game_id, agent_id, context_key, context_value, confidence, ttl_seconds, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (game_id, agent_id, context_key, payload, confidence, ttl_seconds, int(time.time() * 1000))
                )
                conn.commit()
                conn.close()
        except Exception as e:
            logger.error(f'Failed to write context to database: {e}')

    def read_context(self, game_id: str) -> List[Dict]:
        """Read all context entries for a game. Prefers Redis, falls back to SQLite."""
        entries = []
        
        if self.redis_available:
            try:
                hash_key = f'agent:context:{game_id}'
                raw = self.redis.hgetall(hash_key)
                for field_key, value in raw.items():
                    try:
                        parsed = json.loads(value)
                        parts = field_key.split(':', 1)
                        entries.append({
                            'agent_id': parts[0] if len(parts) > 1 else 'unknown',
                            'context_key': parts[1] if len(parts) > 1 else field_key,
                            **parsed
                        })
                    except json.JSONDecodeError:
                        pass
                if entries:
                    return entries
            except Exception as e:
                logger.error(f'Failed to read context from Redis: {e}')

        # Fallback to the database
        try:
            if shared_db.db_available(DB_PATH):
                conn = shared_db.connect(DB_PATH)
                cursor = conn.execute(
                    'SELECT agent_id, context_key, context_value, confidence, created_at FROM agent_context_store WHERE game_id = ? ORDER BY created_at DESC',
                    (game_id,)
                )
                for row in cursor.fetchall():
                    try:
                        val = json.loads(row[2])
                    except json.JSONDecodeError:
                        val = row[2]
                    entries.append({
                        'agent_id': row[0],
                        'context_key': row[1],
                        'value': val,
                        'confidence': row[3],
                        'timestamp': row[4]
                    })
                conn.close()
        except Exception as e:
            logger.error(f'Failed to read context from database: {e}')

        return entries

    def read_context_key(self, game_id: str, agent_id: str, context_key: str) -> Optional[Dict]:
        """Read a specific context entry."""
        if self.redis_available:
            try:
                hash_key = f'agent:context:{game_id}'
                field_key = f'{agent_id}:{context_key}'
                raw = self.redis.hget(hash_key, field_key)
                if raw:
                    return json.loads(raw)
            except Exception:
                pass

        # Database fallback — newest entry wins (entries accumulate).
        try:
            if shared_db.db_available(DB_PATH):
                conn = shared_db.connect(DB_PATH)
                cursor = conn.execute(
                    '''SELECT context_value, confidence, created_at FROM agent_context_store
                       WHERE game_id = ? AND agent_id = ? AND context_key = ?
                       ORDER BY created_at DESC LIMIT 1''',
                    (game_id, agent_id, context_key)
                )
                row = cursor.fetchone()
                conn.close()
                if row:
                    try:
                        val = json.loads(row[0])
                    except json.JSONDecodeError:
                        val = row[0]
                    return {'value': val, 'confidence': row[1], 'timestamp': row[2]}
        except Exception:
            pass
        return None
