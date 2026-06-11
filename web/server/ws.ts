import { IncomingMessage } from 'http';
import { WebSocket } from 'ws';
import { config } from './config';
import { safeTokenEqual } from './middleware';

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
  if (authHeader && authHeader.startsWith('Bearer ') && safeTokenEqual(authHeader.slice(7), config.API_KEY)) {
    return true;
  }
  try {
    // Query-param tokens are a fallback for browser WebSocket clients that
    // can't set headers. Note they can land in proxy logs — prefer the
    // header, or front the upgrade with nginx-injected auth.
    const url = new URL(info.req.url || '', 'http://localhost');
    const token = url.searchParams.get('token');
    if (token && safeTokenEqual(token, config.API_KEY)) {
      return true;
    }
  } catch {
    // Malformed URL — fall through to reject
  }
  return false;
};
