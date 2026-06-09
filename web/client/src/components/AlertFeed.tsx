import { AlertTriangle, TrendingUp, Zap } from 'lucide-react';

const mockAlerts = [
  { id: 1, type: 'steam', text: 'Line movement: A. Wilson PTS O 21.5 -> 22.5', time: '2m ago' },
  { id: 2, type: 'injury', text: 'B. Stewart (Questionable) upgraded to Probable', time: '14m ago' },
  { id: 3, type: 'ev', text: 'New +EV opportunity found: J. Loyd AST', time: '28m ago' },
];

export function AlertFeed() {
  return (
    <div className="glass-card p-4 mt-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-bold uppercase tracking-wider text-slate-400">Live Alert Feed</h2>
        <Zap className="w-4 h-4 text-primary" />
      </div>
      <div className="flex flex-col gap-3">
        {mockAlerts.map(alert => (
          <div key={alert.id} className="flex items-start gap-3 p-2 rounded-lg bg-slate-800/50 border border-slate-700/30">
            {alert.type === 'steam' && <TrendingUp className="w-4 h-4 text-primary mt-1 flex-shrink-0" />}
            {alert.type === 'injury' && <AlertTriangle className="w-4 h-4 text-danger mt-1 flex-shrink-0" />}
            {alert.type === 'ev' && <Zap className="w-4 h-4 text-success mt-1 flex-shrink-0" />}
            <div className="flex-1">
              <p className="text-sm text-slate-300">{alert.text}</p>
              <span className="text-xs text-slate-500 mt-1 block">{alert.time}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
