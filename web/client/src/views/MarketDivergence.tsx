import { useEffect, useState } from 'react';
import { Activity, TrendingUp, Zap, CircleDot, ArrowUpRight, ArrowDownRight, Sparkles } from 'lucide-react';

/* ── mock data ─────────────────────────────────────────────────────── */
const MOCK_EDGES = [
  { player: "A'ja Wilson",      market: 'Points O/U',   bookLine: '22.5 O -110', trueLine: '24.8', edge: 8.2, signal: 'STRONG'   },
  { player: 'Breanna Stewart',  market: 'Rebounds O/U', bookLine: '9.5 O -115',  trueLine: '11.1', edge: 6.4, signal: 'STRONG'   },
  { player: 'Kelsey Plum',      market: 'Assists O/U',  bookLine: '5.5 O -105',  trueLine: '6.3',  edge: 4.1, signal: 'MODERATE' },
  { player: 'Alyssa Thomas',    market: 'Pts+Reb+Ast',  bookLine: '29.5 O -108', trueLine: '31.9', edge: 5.7, signal: 'STRONG'   },
  { player: 'Sabrina Ionescu',  market: 'Three-Pts O/U',bookLine: '2.5 O +100',  trueLine: '3.1',  edge: 3.9, signal: 'MODERATE' },
];

/* ── mini sparkline SVG ────────────────────────────────────────────── */
function MiniSparkline() {
  return (
    <svg viewBox="0 0 80 28" className="w-20 h-7 ml-auto" fill="none">
      <polyline
        points="0,22 10,18 20,20 30,12 40,15 50,8 60,10 70,4 80,6"
        stroke="url(#spark-grad)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <defs>
        <linearGradient id="spark-grad" x1="0" y1="0" x2="80" y2="0">
          <stop offset="0%" stopColor="#ef4444" stopOpacity="0.4" />
          <stop offset="100%" stopColor="#ef4444" />
        </linearGradient>
      </defs>
    </svg>
  );
}

/* ── radar pulse for empty alert state ─────────────────────────────── */
function RadarPulse() {
  return (
    <div className="relative flex items-center justify-center w-16 h-16 mx-auto mb-4">
      <span className="absolute inline-flex h-full w-full rounded-full bg-cs-red/20 animate-ping" />
      <span className="absolute inline-flex h-10 w-10 rounded-full bg-cs-red/10 animate-pulse-slow" />
      <CircleDot className="relative z-10 w-6 h-6 text-cs-red" />
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════ */
export default function MarketDivergence() {
  const [messages, setMessages] = useState<any[]>([]);
  const [velocityAlerts, setVelocityAlerts] = useState<any[]>([]);

  /* SSE listener */
  useEffect(() => {
    const sse = new EventSource('http://localhost:3000/api/stream/alerts');
    sse.onmessage = (e) => {
      if (e.data !== 'heartbeat') {
        try {
          const data = JSON.parse(e.data);
          setMessages((prev) => [data, ...prev].slice(0, 50));
        } catch {
          /* ignore parse errors */
        }
      }
    };
    return () => sse.close();
  }, []);

  /* Fetch velocity alerts */
  useEffect(() => {
    const fetchVelocity = async () => {
      try {
        const res = await fetch('http://localhost:3000/api/velocity/alerts');
        if (res.ok) {
          const data = await res.json();
          setVelocityAlerts(data);
        }
      } catch (err) {
        console.error("Failed to fetch velocity:", err);
      }
    };
    fetchVelocity();
    const interval = setInterval(fetchVelocity, 5000);
    return () => clearInterval(interval);
  }, []);

  /* ── render ─────────────────────────────────────────────────────── */
  return (
    <div className="p-8 space-y-6 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-extrabold text-white tracking-tight flex items-center gap-3">
          <Activity className="w-7 h-7 text-cs-red drop-shadow-[0_0_8px_rgba(239,68,68,0.6)]" />
          Market Divergence Terminal
        </h1>
        <span className="text-xs text-cs-muted font-mono tracking-widest uppercase">
          Live &bull; {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
        </span>
      </div>

      {/* ── KPI Row ────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {/* Card 1 — Active Edges */}
        <div
          className="cs-card p-6 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up"
          style={{ animationDelay: '0ms' }}
        >
          <p className="cs-stat-label flex items-center gap-1.5">
            <Zap className="w-3.5 h-3.5 text-cs-red" />
            Active Edges
          </p>
          <div className="flex items-end justify-between mt-2">
            <span className="cs-stat text-4xl">12</span>
            <MiniSparkline />
          </div>
        </div>

        {/* Card 2 — Win Rate (30d) */}
        <div
          className="cs-card p-6 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up"
          style={{ animationDelay: '100ms' }}
        >
          <p className="cs-stat-label flex items-center gap-1.5">
            <TrendingUp className="w-3.5 h-3.5 text-cs-red" />
            Win Rate (30d)
          </p>
          <span className="cs-stat text-4xl text-gradient-red mt-2 block">67.2%</span>
          {/* thin progress bar */}
          <div className="mt-3 h-1.5 w-full rounded-full bg-cs-dark overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cs-red to-cs-red-bright shadow-glow-red-sm"
              style={{ width: '67.2%' }}
            />
          </div>
        </div>

        {/* Card 3 — Bankroll P&L */}
        <div
          className="cs-card p-6 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up"
          style={{ animationDelay: '200ms' }}
        >
          <p className="cs-stat-label flex items-center gap-1.5">
            <Activity className="w-3.5 h-3.5 text-cs-red" />
            Bankroll P&amp;L
          </p>
          <span className="cs-stat text-4xl mt-2 block" style={{ color: '#22c55e' }}>
            +$2,340
          </span>
          <p className="text-xs text-cs-muted mt-1">since Jun 1</p>
        </div>
      </div>

      {/* ── Middle Section ─────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left — EV Discrepancies Table */}
        <div className="lg:col-span-2 cs-card p-0 overflow-hidden animate-slide-up" style={{ animationDelay: '300ms' }}>
          <div className="px-6 pt-6 pb-4 border-b border-cs-border">
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <CircleDot className="w-4 h-4 text-cs-red-bright animate-pulse-slow" />
              Live EV Discrepancies
            </h2>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm text-left">
              <thead>
                <tr className="border-b border-cs-border text-cs-muted text-xs uppercase tracking-wider">
                  <th className="px-6 py-3 font-semibold">Player</th>
                  <th className="px-6 py-3 font-semibold">Market</th>
                  <th className="px-6 py-3 font-semibold">Book Line</th>
                  <th className="px-6 py-3 font-semibold">True Line</th>
                  <th className="px-6 py-3 font-semibold text-right">Edge%</th>
                  <th className="px-6 py-3 font-semibold text-center">Signal</th>
                </tr>
              </thead>
              <tbody>
                {MOCK_EDGES.map((row, i) => (
                  <tr
                    key={row.player}
                    className={`border-b border-cs-border/40 transition-colors hover:bg-cs-red/[0.04] ${
                      i % 2 === 0 ? 'bg-cs-dark/50' : 'bg-transparent'
                    }`}
                  >
                    <td className="px-6 py-4 font-medium text-white whitespace-nowrap">{row.player}</td>
                    <td className="px-6 py-4 text-cs-muted">{row.market}</td>
                    <td className="px-6 py-4 text-cs-muted font-mono text-xs">{row.bookLine}</td>
                    <td className="px-6 py-4 text-white font-mono text-xs">{row.trueLine}</td>
                    <td className="px-6 py-4 text-right">
                      <span
                        className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold"
                        style={{ color: '#22c55e', backgroundColor: 'rgba(34,197,94,0.12)' }}
                      >
                        +{row.edge}%
                      </span>
                    </td>
                    <td className="px-6 py-4 text-center">
                      <span
                        className={`cs-badge text-xs font-bold tracking-wide ${
                          row.signal === 'STRONG'
                            ? 'bg-cs-red/15 text-cs-red-bright shadow-glow-red-sm'
                            : 'bg-amber-500/10 text-amber-400'
                        }`}
                      >
                        {row.signal}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* New Line Velocity Anomalies (Agent 17) */}
        <div className="lg:col-span-2 cs-card p-6 space-y-4 animate-slide-up" style={{ animationDelay: '350ms' }}>
          <div className="border-b border-cs-border/40 pb-3 flex items-center justify-between">
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-cs-red" />
              Agent 17: Line Movement Velocity Feed
            </h2>
            <span className="text-[10px] bg-cs-red/20 text-cs-red-bright px-2 py-0.5 rounded font-mono font-bold">
              REAL-TIME VOLATILITY
            </span>
          </div>

          <div className="space-y-3">
            {velocityAlerts.length === 0 ? (
              <p className="text-xs text-cs-muted font-mono text-center py-4">No velocity anomalies detected in this cycle.</p>
            ) : (
              velocityAlerts.map((item, idx) => (
                <div key={idx} className="bg-cs-dark/30 border border-cs-border/30 rounded-xl p-3.5 flex flex-col md:flex-row justify-between md:items-center gap-3 hover:border-cs-border/60 transition-colors">
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-bold text-white text-sm">{item.player}</span>
                      <span className="text-[10px] text-cs-muted bg-cs-dark px-1.5 py-0.2 rounded font-mono">{item.stat}</span>
                    </div>
                    <div className="text-xs text-cs-muted mt-1">{item.reason}</div>
                  </div>
                  
                  <div className="flex items-center gap-4 text-right">
                    <div>
                      <div className="text-[10px] text-cs-muted uppercase font-mono">Shift Velocity</div>
                      <div className={`text-xs font-mono font-bold flex items-center gap-0.5 justify-end ${item.direction === 'UP' ? 'text-emerald-400' : 'text-cs-red-bright'}`}>
                        {item.direction === 'UP' ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                        {item.delta} lines / {item.odds_delta} odds
                      </div>
                    </div>
                    <div>
                      <div className="text-[10px] text-cs-muted uppercase font-mono">Time Window</div>
                      <div className="text-xs font-mono text-white font-bold">{item.duration_seconds}s</div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Right — Alert Stream */}
        <div className="lg:col-span-1 cs-card p-0 flex flex-col animate-slide-up" style={{ animationDelay: '400ms' }}>
          <div className="px-6 pt-6 pb-4 border-b border-cs-border flex items-center justify-between">
            <h2 className="text-lg font-bold text-white flex items-center gap-2">
              <Zap className="w-4 h-4 text-cs-red-bright" />
              Alert Stream
            </h2>
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cs-red opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-cs-red-bright" />
            </span>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-3 max-h-[420px] scrollbar-thin scrollbar-thumb-cs-border scrollbar-track-transparent">
            {messages.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-full py-12 text-center">
                <RadarPulse />
                <p className="text-sm text-cs-muted font-medium animate-pulse-slow">
                  Scanning markets…
                </p>
                <p className="text-xs text-cs-muted/50 mt-1">Waiting for live signals</p>
              </div>
            ) : (
              messages.map((m, i) => (
                <div
                  key={i}
                  className="bg-cs-dark/60 border border-cs-border/50 rounded-lg p-3.5 hover:border-cs-red/30 transition-colors animate-fade-in"
                >
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-cs-red font-bold text-sm tracking-wide">{m.channel}</span>
                    <span className="text-[10px] text-cs-muted font-mono">
                      {new Date().toLocaleTimeString()}
                    </span>
                  </div>
                  <pre className="text-cs-muted text-xs leading-relaxed overflow-hidden text-ellipsis whitespace-pre-wrap break-all font-mono">
                    {JSON.stringify(m.message, null, 2)}
                  </pre>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
