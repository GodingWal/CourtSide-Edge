import { IncomingMessage } from 'http';
import { WebSocket } from 'ws';
import { config } from './config';

// Store active WebSocket connections
export const wsClients = new Set<WebSocket>();

export const broadcast = (data: any) => {
  const msg = JSON.stringify(data);
  for (const client of wsClients) {
    if (client.readyState === WebSocket.OPEN) {
      client.send(msg);
    }
  }
};

// ── WebSocket Auth ──────────────────────────────────────────────────────────
// When API_KEY is set, validate the token on WebSocket upgrade. Accepts either
// an `Authorization: Bearer <token>` header or a `?token=` query parameter.
export const verifyWsClient = (info: { req: IncomingMessage }): boolean => {
  if (!config.API_KEY) {
    return true;
  }
  const authHeader = info.req.headers.authorization;
  if (authHeader && authHeader.startsWith('Bearer ') && authHeader.slice(7) === config.API_KEY) {
    return true;
  }
  try {
    const url = new URL(info.req.url || '', 'http://localhost');
    if (url.searchParams.get('token') === config.API_KEY) {
      return true;
    }
  } catch {
    // Malformed URL — fall through to reject
  }
  return false;
};
