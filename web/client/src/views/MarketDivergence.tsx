import { useEffect, useState } from 'react';
import { Activity, TrendingUp, Zap, CircleDot, ArrowUpRight, ArrowDownRight, Sparkles, BarChart3, Shield, Cpu, Eye } from 'lucide-react';
import { API_BASE } from '../lib/config';


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

interface StreamMessage {
  channel: string;
  message: unknown;
}

interface VelocityAlert {
  player?: string;
  stat?: string;
  direction?: string;
  delta?: string;
  odds_delta?: string;
  duration_seconds?: number;
  reason?: string;
  timestamp?: number;
}

interface SharpMove {
  player?: string;
  stat?: string;
  book?: string;
  move?: string;
  direction?: string;
  timestamp?: number;
}

interface MarketEdge {
  trace_id?: string;
  player?: string | null;
  stat?: string | null;
  market_classification?: string;
  book?: string | null;
  line?: number;
  odds?: number | null;
  true_line?: number | null;
  divergence_score?: number;
  edge?: number;
  confidence?: number;
}

/* ═══════════════════════════════════════════════════════════════════ */
export default function MarketDivergence() {
  const [messages, setMessages] = useState<StreamMessage[]>([]);
  const [velocityAlerts, setVelocityAlerts] = useState<VelocityAlert[]>([]);
  const [sharpConsensus, setSharpConsensus] = useState<SharpMove[]>([]);
  const [edges, setEdges] = useState<MarketEdge[]>([]);
  const [betStats, setBetStats] = useState<{ avg_edge: number; avg_clv?: number; win_rate: number; wins: number; losses: number; total_profit: number } | null>(null);
  const [metaAnalysis, setMetaAnalysis] = useState<any[]>([]);
  const [backtestReports, setBacktestReports] = useState<any[]>([]);
  const [riskReports, setRiskReports] = useState<any[]>([]);
  const [explanations, setExplanations] = useState<any[]>([]);

  /* real edges + bet stats */
  useEffect(() => {
    const fetchEdges = async () => {
      try {
        const [edgesRes, statsRes] = await Promise.all([
          fetch(`${API_BASE}/edges/recent`),
          fetch(`${API_BASE}/bets/stats`),
        ]);
        if (edgesRes.ok) setEdges(await edgesRes.json());
        if (statsRes.ok) setBetStats(await statsRes.json());
      } catch (err) {
        console.error('Failed to fetch edges/stats:', err);
      }
    };
    fetchEdges();
    const interval = setInterval(fetchEdges, 10000);
    return () => clearInterval(interval);
  }, []);

  /* SSE listener */
  useEffect(() => {
    const sse = new EventSource(`${API_BASE}/stream/alerts`);
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
        const res = await fetch(`${API_BASE}/velocity/alerts`);
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

  /* Fetch sharp consensus alerts */
  useEffect(() => {
    const fetchSharp = async () => {
      try {
        const res = await fetch(`${API_BASE}/sharp/consensus`);
        if (res.ok) {
          const data = await res.json();
          setSharpConsensus(data);
        }
      } catch (err) {
        console.error("Failed to fetch sharp consensus:", err);
      }
    };
    fetchSharp();
    const interval = setInterval(fetchSharp, 5000);
    return () => clearInterval(interval);
  }, []);

  /* Fetch meta analysis (Agent 28) */
  useEffect(() => {
    const fetchMeta = async () => {
      try {
        const res = await fetch(`${API_BASE}/meta/analysis`);
        if (res.ok) setMetaAnalysis(await res.json());
      } catch (err) {
        console.error("Failed to fetch meta analysis:", err);
      }
    };
    fetchMeta();
    const interval = setInterval(fetchMeta, 30000);
    return () => clearInterval(interval);
  }, []);

  /* Fetch backtest reports (Agent 29) */
  useEffect(() => {
    const fetchBacktest = async () => {
      try {
        const res = await fetch(`${API_BASE}/backtest/reports`);
        if (res.ok) setBacktestReports(await res.json());
      } catch (err) {
        console.error("Failed to fetch backtest reports:", err);
      }
    };
    fetchBacktest();
    const interval = setInterval(fetchBacktest, 60000);
    return () => clearInterval(interval);
  }, []);

  /* Fetch risk reports (Agent 31) */
  useEffect(() => {
    const fetchRisk = async () => {
      try {
        const res = await fetch(`${API_BASE}/risk/reports`);
        if (res.ok) setRiskReports(await res.json());
      } catch (err) {
        console.error("Failed to fetch risk reports:", err);
      }
    };
    fetchRisk();
    const interval = setInterval(fetchRisk, 30000);
    return () => clearInterval(interval);
  }, []);

  /* Fetch explanations (Agent 32) */
  useEffect(() => {
    const fetchExplanations = async () => {
      try {
        const res = await fetch(`${API_BASE}/explanations`);
        if (res.ok) setExplanations(await res.json());
      } catch (err) {
        console.error("Failed to fetch explanations:", err);
      }
    };
    fetchExplanations();
    const interval = setInterval(fetchExplanations, 30000);
    return () => clearInterval(interval);
  }, []);

  /* ── render ─────────────────────────────────────────────────────── */
  return (
    <div className="p-4 md:p-8 space-y-6 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl md:text-3xl font-extrabold text-white tracking-tight flex items-center gap-3">
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
            <span className="cs-stat text-4xl">{edges.length}</span>
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
          <span className="cs-stat text-4xl text-gradient-red mt-2 block">
            {betStats && (betStats.wins + betStats.losses) > 0 ? `${betStats.win_rate.toFixed(1)}%` : '—'}
          </span>
          {/* thin progress bar */}
          <div className="mt-3 h-1.5 w-full rounded-full bg-cs-dark overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-cs-red to-cs-red-bright shadow-glow-red-sm"
              style={{ width: `${Math.min(100, betStats?.win_rate ?? 0)}%` }}
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
          <span
            className="cs-stat text-4xl mt-2 block"
            style={{ color: (betStats?.total_profit ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}
          >
            {betStats ? `${betStats.total_profit >= 0 ? '+' : '-'}$${Math.abs(betStats.total_profit).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—'}
          </span>
          <p className="text-xs text-cs-muted mt-1">all settled bets</p>
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
                {edges.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-6 py-10 text-center text-cs-muted font-mono text-xs">
                      No live edges detected yet. Agent 11 publishes here when real market divergence is found.
                    </td>
                  </tr>
                ) : (
                  edges.map((row, i) => (
                    <tr
                      key={row.trace_id ?? i}
                      className={`border-b border-cs-border/40 transition-colors hover:bg-cs-red/[0.04] ${
                        i % 2 === 0 ? 'bg-cs-dark/50' : 'bg-transparent'
                      }`}
                    >
                      <td className="px-6 py-4 font-medium text-white whitespace-nowrap">{row.player ?? '—'}</td>
                      <td className="px-6 py-4 text-cs-muted">
                        {row.stat ?? row.market_classification ?? '—'}
                        {row.book && <span className="block text-[10px] font-mono text-cs-muted/70">{row.book}</span>}
                      </td>
                      <td className="px-6 py-4 text-cs-muted font-mono text-xs">{row.line !== undefined ? `${row.line} ${row.odds ?? ''}` : '—'}</td>
                      <td className="px-6 py-4 text-white font-mono text-xs">{row.true_line ?? '—'}</td>
                      <td className="px-6 py-4 text-right">
                        <span
                          className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold"
                          style={{ color: '#22c55e', backgroundColor: 'rgba(34,197,94,0.12)' }}
                        >
                          +{row.divergence_score ?? row.edge ?? 0}%
                        </span>
                      </td>
                      <td className="px-6 py-4 text-center">
                        <span
                          className={`cs-badge text-xs font-bold tracking-wide ${
                            (row.confidence ?? 0) >= 0.85
                              ? 'bg-cs-red/15 text-cs-red-bright shadow-glow-red-sm'
                              : 'bg-amber-500/10 text-amber-400'
                          }`}
                        >
                          {(row.confidence ?? 0) >= 0.85 ? 'STRONG' : 'MODERATE'}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
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

        {/* Agent 19: Sharp Line Movement Alert Feed */}
        <div className="lg:col-span-1 cs-card p-6 flex flex-col space-y-4 animate-slide-up" style={{ animationDelay: '450ms' }}>
          <div className="border-b border-cs-border/40 pb-3 flex items-center justify-between">
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-cs-red" />
              Agent 19: Sharp Consensus Feed
            </h2>
            <span className="text-[10px] bg-cs-red/20 text-cs-red-bright px-2 py-0.5 rounded font-mono font-bold">
              SHARP MOVES
            </span>
          </div>

          <div className="flex-1 overflow-y-auto space-y-3 max-h-[350px] scrollbar-thin scrollbar-thumb-cs-border scrollbar-track-transparent">
            {sharpConsensus.length === 0 ? (
              <p className="text-xs text-cs-muted font-mono text-center py-4">No sharp line moves detected.</p>
            ) : (
              sharpConsensus.map((item, idx) => (
                <div key={idx} className="bg-cs-dark/30 border border-cs-border/30 rounded-xl p-3.5 hover:border-cs-border/60 transition-colors">
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-white text-sm">{item.player}</span>
                    <span className="text-[10px] text-cs-muted bg-cs-dark px-1.5 py-0.2 rounded font-mono">{item.stat}</span>
                  </div>
                  <div className="flex items-center justify-between mt-2">
                    <div className="text-xs text-cs-muted font-mono flex items-center gap-1.5">
                      <span className="text-cs-muted bg-cs-dark/40 px-1 py-0.5 rounded font-bold">{item.book}</span>
                      <span>{item.move}</span>
                    </div>
                    <span className={`text-xs font-mono font-bold flex items-center gap-0.5 ${item.direction === 'UP' ? 'text-emerald-400' : 'text-cs-red-bright'}`}>
                      {item.direction === 'UP' ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                      {item.direction}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      {/* ── Bottom Section: New Agents 28-32 ───────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Agent 28: Meta-Analysis */}
        <div className="cs-card p-6 space-y-4 animate-slide-up" style={{ animationDelay: '500ms' }}>
          <div className="border-b border-cs-border/40 pb-3 flex items-center justify-between">
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              <Cpu className="w-4 h-4 text-cs-red" />
              Agent 28: Meta-Analysis
            </h2>
            <span className="text-[10px] bg-cs-red/20 text-cs-red-bright px-2 py-0.5 rounded font-mono font-bold">
              META
            </span>
          </div>
          {metaAnalysis.length === 0 ? (
            <p className="text-xs text-cs-muted font-mono text-center py-4">No meta-analysis available yet.</p>
          ) : (
            <div className="space-y-3">
              {metaAnalysis.slice(0, 3).map((item, idx) => (
                <div key={idx} className="bg-cs-dark/30 border border-cs-border/30 rounded-xl p-3.5">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-bold text-white">Score: {(item.confidence?.overall_score ?? 0).toFixed(1)}%</span>
                    <span className="text-[10px] text-cs-muted bg-cs-dark px-1.5 py-0.2 rounded font-mono uppercase">{item.confidence?.mode ?? '—'}</span>
                  </div>
                  <div className="grid grid-cols-4 gap-2 mt-2 text-center">
                    <div><div className="text-xs font-mono text-emerald-400">{(item.confidence?.projection_trust ?? 0).toFixed(0)}</div><div className="text-[9px] text-cs-muted">PROJ</div></div>
                    <div><div className="text-xs font-mono text-emerald-400">{(item.confidence?.market_trust ?? 0).toFixed(0)}</div><div className="text-[9px] text-cs-muted">MKT</div></div>
                    <div><div className="text-xs font-mono text-emerald-400">{(item.confidence?.context_trust ?? 0).toFixed(0)}</div><div className="text-[9px] text-cs-muted">CTX</div></div>
                    <div><div className="text-xs font-mono text-emerald-400">{(item.confidence?.execution_trust ?? 0).toFixed(0)}</div><div className="text-[9px] text-cs-muted">EXEC</div></div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Agent 29: Backtest */}
        <div className="cs-card p-6 space-y-4 animate-slide-up" style={{ animationDelay: '550ms' }}>
          <div className="border-b border-cs-border/40 pb-3 flex items-center justify-between">
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-cs-red" />
              Agent 29: Backtest (30d)
            </h2>
            <span className="text-[10px] bg-cs-red/20 text-cs-red-bright px-2 py-0.5 rounded font-mono font-bold">
              VALIDATION
            </span>
          </div>
          {backtestReports.length === 0 ? (
            <p className="text-xs text-cs-muted font-mono text-center py-4">No backtest reports yet.</p>
          ) : (
            <div className="space-y-3">
              {backtestReports.slice(0, 3).map((item, idx) => (
                <div key={idx} className="bg-cs-dark/30 border border-cs-border/30 rounded-xl p-3.5">
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-bold text-white">{item.report?.period_days ?? 30}d Performance</span>
                    <span className={`text-xs font-mono font-bold ${(item.report?.win_rate ?? 0) >= 50 ? 'text-emerald-400' : 'text-cs-red-bright'}`}>
                      {(item.report?.win_rate ?? 0).toFixed(1)}% WR
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 mt-2 text-center">
                    <div><div className="text-xs font-mono text-white">{item.report?.total_bets ?? 0}</div><div className="text-[9px] text-cs-muted">BETS</div></div>
                    <div><div className="text-xs font-mono text-white">${(item.report?.pnl ?? 0).toFixed(0)}</div><div className="text-[9px] text-cs-muted">PnL</div></div>
                    <div><div className="text-xs font-mono text-white">{(item.report?.roi ?? 0).toFixed(1)}%</div><div className="text-[9px] text-cs-muted">ROI</div></div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Agent 31: Portfolio Risk */}
        <div className="cs-card p-6 space-y-4 animate-slide-up" style={{ animationDelay: '600ms' }}>
          <div className="border-b border-cs-border/40 pb-3 flex items-center justify-between">
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              <Shield className="w-4 h-4 text-cs-red" />
              Agent 31: Portfolio Risk
            </h2>
            <span className="text-[10px] bg-cs-red/20 text-cs-red-bright px-2 py-0.5 rounded font-mono font-bold">
              RISK
            </span>
          </div>
          {riskReports.length === 0 ? (
            <p className="text-xs text-cs-muted font-mono text-center py-4">No risk reports yet.</p>
          ) : (
            <div className="space-y-3">
              {riskReports.slice(0, 3).map((item, idx) => (
                <div key={idx} className={`bg-cs-dark/30 border rounded-xl p-3.5 ${(item.report?.risk_level ?? 'NORMAL') === 'HIGH' ? 'border-red-500/40' : 'border-cs-border/30'}`}>
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-bold text-white">Risk Level</span>
                    <span className={`text-xs font-mono font-bold ${(item.report?.risk_level ?? 'NORMAL') === 'HIGH' ? 'text-red-400' : 'text-emerald-400'}`}>
                      {item.report?.risk_level ?? 'NORMAL'}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 mt-2 text-center">
                    <div><div className="text-xs font-mono text-white">{(item.report?.utilization ?? 0).toFixed(1)}%</div><div className="text-[9px] text-cs-muted">UTIL</div></div>
                    <div><div className="text-xs font-mono text-white">{item.report?.open_bets ?? 0}</div><div className="text-[9px] text-cs-muted">OPEN</div></div>
                    <div><div className="text-xs font-mono text-white">{item.report?.breaches?.length ?? 0}</div><div className="text-[9px] text-cs-muted">BREACHES</div></div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Agent 32: Explainability */}
        <div className="cs-card p-6 space-y-4 animate-slide-up" style={{ animationDelay: '650ms' }}>
          <div className="border-b border-cs-border/40 pb-3 flex items-center justify-between">
            <h2 className="text-base font-bold text-white flex items-center gap-2">
              <Eye className="w-4 h-4 text-cs-red" />
              Agent 32: Explainability
            </h2>
            <span className="text-[10px] bg-cs-red/20 text-cs-red-bright px-2 py-0.5 rounded font-mono font-bold">
              XAI
            </span>
          </div>
          {explanations.length === 0 ? (
            <p className="text-xs text-cs-muted font-mono text-center py-4">No explanations generated yet.</p>
          ) : (
            <div className="space-y-3 max-h-[300px] overflow-y-auto scrollbar-thin scrollbar-thumb-cs-border scrollbar-track-transparent">
              {explanations.slice(0, 5).map((item, idx) => (
                <div key={idx} className="bg-cs-dark/30 border border-cs-border/30 rounded-xl p-3.5">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-bold text-white">{item.player_name ?? item.pick_id ?? '—'}</span>
                    <span className="text-[10px] text-cs-muted bg-cs-dark px-1.5 py-0.2 rounded font-mono">{item.explanation_type ?? 'PICK'}</span>
                  </div>
                  <p className="text-xs text-cs-muted leading-relaxed line-clamp-3">{item.explanation ?? 'No explanation text.'}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
