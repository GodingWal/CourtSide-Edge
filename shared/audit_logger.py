import os
import json
import uuid
import time
import logging
import sqlite3
from typing import Optional

import requests

BACKEND_URL = os.getenv('BACKEND_URL', 'http://localhost:3000')
API_KEY = os.getenv('API_KEY')
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '../data/hoopstats_wnba.db'))
HTTP_TIMEOUT = 5

logger = logging.getLogger('AuditLogger')

def generate_trace_id() -> str:
    """Generate a new trace ID for tracking an edge through the decision pipeline."""
    return str(uuid.uuid4())

class AuditLogger:
    def __init__(self):
        pass

    def log_decision(self, trace_id: str, agent_id: str, action: str, 
                     reason: Optional[str] = None,
                     input_payload: Optional[dict] = None,
                     output_payload: Optional[dict] = None,
                     confidence: Optional[float] = None):
        """Log an agent decision to the audit trail.

        Writes SQLite directly when the ledger file is on this host;
        otherwise (the remote agent tier) posts to the web API so the
        decision is persisted instead of silently dropped.

        action: 'APPROVE', 'REJECT', 'ABSTAIN', 'SIZE', 'EXECUTE', 'HALT'
        """
        try:
            if not os.path.exists(DB_PATH):
                self._log_decision_http(
                    trace_id, agent_id, action, reason,
                    input_payload, output_payload, confidence,
                )
                return

            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            conn.execute(
                '''INSERT INTO decision_audit 
                   (trace_id, agent_id, action, reason, input_payload, output_payload, confidence, timestamp)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                (
                    trace_id,
                    agent_id,
                    action,
                    reason,
                    json.dumps(input_payload) if input_payload else None,
                    json.dumps(output_payload) if output_payload else None,
                    confidence,
                    int(time.time() * 1000)
                )
            )
            conn.commit()
            conn.close()
            logger.info(f'[AUDIT] {agent_id} -> {action} (trace: {trace_id[:8]}...)')
        except Exception as e:
            logger.error(f'Failed to log audit decision: {e}')

    def _log_decision_http(self, trace_id, agent_id, action, reason,
                           input_payload, output_payload, confidence):
        """Persist a decision via POST /api/audit on the web tier."""
        try:
            resp = requests.post(
                f'{BACKEND_URL}/api/audit',
                json={
                    'trace_id': trace_id,
                    'agent_id': agent_id,
                    'action': action,
                    'reason': reason,
                    'input_payload': input_payload,
                    'output_payload': output_payload,
                    'confidence': confidence,
                },
                headers={'Authorization': f'Bearer {API_KEY}'} if API_KEY else {},
                timeout=HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            logger.info(f'[AUDIT/HTTP] {agent_id} -> {action} (trace: {trace_id[:8]}...)')
        except Exception as e:
            logger.error(f'Failed to log audit decision over HTTP: {e}')

    def get_decisions(self, trace_id: str) -> list:
        """Retrieve all decisions for a given trace ID."""
        try:
            if not os.path.exists(DB_PATH):
                return []
            conn = sqlite3.connect(DB_PATH, timeout=5.0)
            cursor = conn.execute(
                'SELECT agent_id, action, reason, input_payload, output_payload, confidence, timestamp FROM decision_audit WHERE trace_id = ? ORDER BY timestamp ASC',
                (trace_id,)
            )
            results = []
            for row in cursor.fetchall():
                results.append({
                    'agent_id': row[0],
                    'action': row[1],
                    'reason': row[2],
                    'input_payload': json.loads(row[3]) if row[3] else None,
                    'output_payload': json.loads(row[4]) if row[4] else None,
                    'confidence': row[5],
                    'timestamp': row[6]
                })
            conn.close()
            return results
        except Exception as e:
            logger.error(f'Failed to retrieve audit decisions: {e}')
            return []
