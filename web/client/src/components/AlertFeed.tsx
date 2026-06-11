import { useEffect, useState } from 'react';
import { AlertTriangle, TrendingUp, Zap } from 'lucide-react';
import { API_BASE } from '../lib/config';

interface FeedAlert {
  id: number;
  type: 'steam' | 'injury' | 'ev';
  text: string;
  time: string;
}

type AlertPayload = Record<string, unknown> & { message?: unknown; player?: unknown; stat?: unknown; line?: unknown; direction?: unknown };

function classify(data: AlertPayload): FeedAlert['type'] {
  const channel = String(data.channel || data.source || '').toLowerCase();
  if (channel.includes('steam') || channel.includes('velocity')) return 'steam';
  if (channel.includes('injury') || channel.includes('news')) return 'injury';
  return 'ev';
}

function describe(data: AlertPayload): string {
  if (typeof data.message === 'string') return data.message;
  if (data.player && data.stat) {
    return `${data.player} ${data.stat}${data.line !== undefined ? ` ${data.line}` : ''}${data.direction ? ` ${data.direction}` : ''}`;
  }
  return JSON.stringify(data).slice(0, 120);
}

export function AlertFeed() {
  const [alerts, setAlerts] = useState<FeedAlert[]>([]);

  // Live alerts only — streamed from the backend SSE feed (agent signals).
  useEffect(() => {
    const sse = new EventSource(`${API_BASE}/stream/alerts`);
    let nextId = 1;
    sse.onmessage = (e) => {
      if (e.data === 'heartbeat') return;
      try {
        const data = JSON.parse(e.data);
        const alert: FeedAlert = {
          id: nextId++,
          type: classify(data),
          text: describe(data),
          time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        };
        setAlerts((prev) => [alert, ...prev].slice(0, 8));
      } catch {
        /* ignore malformed events */
      }
    };
    return () => sse.close();
  }, []);

  return (
    <div className="glass-card p-4 mt-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-bold uppercase tracking-wider text-slate-400">Live Alert Feed</h2>
        <Zap className="w-4 h-4 text-primary" />
      </div>
      <div className="flex flex-col gap-3">
        {alerts.length === 0 ? (
          <p className="text-xs text-slate-500 text-center py-4 font-mono">
            Listening for live agent signals…
          </p>
        ) : (
          alerts.map(alert => (
            <div key={alert.id} className="flex items-start gap-3 p-2 rounded-lg bg-slate-800/50 border border-slate-700/30">
              {alert.type === 'steam' && <TrendingUp className="w-4 h-4 text-primary mt-1 flex-shrink-0" />}
              {alert.type === 'injury' && <AlertTriangle className="w-4 h-4 text-danger mt-1 flex-shrink-0" />}
              {alert.type === 'ev' && <Zap className="w-4 h-4 text-success mt-1 flex-shrink-0" />}
              <div className="flex-1">
                <p className="text-sm text-slate-300">{alert.text}</p>
                <span className="text-xs text-slate-500 mt-1 block">{alert.time}</span>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
