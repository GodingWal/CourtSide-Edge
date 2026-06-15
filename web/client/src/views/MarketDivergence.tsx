import { useEffect, useState } from 'react';
import {
  Activity,
  TrendingUp,
  Zap,
  CircleDot,
  ArrowUpRight,
  ArrowDownRight,
  Sparkles,
  BarChart3,
  Target,
} from 'lucide-react';
import { API_BASE } from '../lib/config';
import AlertFeed from '../components/AlertFeed';

/* ── Types ── */

interface StreamMessage {
  channel: string;
  message: Record<string, unknown>;
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
  over_under?: string;
}

/* ── Edge strength color helper ── */

function edgeColorClass(edge: number = 0): string {
  if (edge >= 8) return 'text-emerald-400 bg-emerald-500/12 border-emerald-500/20';
  if (edge >= 5) return 'text-amber-400 bg-amber-500/10 border-amber-500/20';
  return 'text-cs-muted bg-cs-dark border-cs-border/40';
}

function edgeGlowClass(edge: number = 0): string {
  if (edge >= 8) return 'shadow-glow-success';
  if (edge >= 5) return 'shadow-glow-warning';
  return '';
}

/* ═══════════════════════════════════════════════════════════════════ */

export default function MarketDivergence() {
  const [messages, setMessages] = useState<StreamMessage[]>([]);
  const [velocityAlerts, setVelocityAlerts] = useState<VelocityAlert[]>([]);
  const [sharpConsensus, setSharpConsensus] = useState<SharpMove[]>([]);
  const [edges, setEdges] = useState<MarketEdge[]>([]);
  const [betStats, setBetStats] = useState<{
    avg_edge: number;
    avg_clv?: number;
    win_rate: number;
    wins: number;
    losses: number;
    total_profit: number;
  } | null>(null);

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
        if (res.ok) setVelocityAlerts(await res.json());
      } catch (err) {
        console.error('Failed to fetch velocity:', err);
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
        if (res.ok) setSharpConsensus(await res.json());
      } catch (err) {
        console.error('Failed to fetch sharp consensus:', err);
      }
    };
    fetchSharp();
    const interval = setInterval(fetchSharp, 5000);
    return () => clearInterval(interval);
  }, []);

  const edgeValue = (e: MarketEdge) => e.divergence_score ?? e.edge ?? 0;
  const winCount = betStats ? betStats.wins + betStats.losses : 0;

  return (
    <div className="p-4 md:p-8 space-y-5 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
      {/* ── Title row ──────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-cs-red/10 border border-cs-red/20 flex items-center justify-center shadow-glow-red-sm">
            <Target className="w-4.5 h-4.5 text-cs-red" />
          </div>
          <div>
            <h1 className="text-xl md:text-2xl font-extrabold text-white tracking-tight">
              Market Divergence
            </h1>
            <p className="text-[11px] text-cs-muted font-mono mt-0.5">
              Agent 11 · Edge Detection · {edges.length} active · {new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="relative flex h-2.5 w-2.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cs-success opacity-75" />
            <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-cs-success" />
          </span>
          <span className="text-[10px] text-cs-muted font-mono uppercase tracking-wider hidden sm:inline">Live</span>
        </div>
      </div>

      {/* ── KPI Row ────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Active Edges */}
        <div className="cs-card p-5 group hover:border-cs-red/40 transition-all duration-300">
          <div className="flex items-center justify-between">
            <span className="cs-stat-label flex items-center gap-1.5">
              <Zap className="w-3.5 h-3.5 text-cs-red" />
              Active Edges
            </span>
            <span className="cs-badge">Agent 11</span>
          </div>
          <span className="cs-stat text-4xl mt-3 block">{edges.length}</span>
          <div className="mt-3 flex items-center gap-2">
            <span className="text-[10px] text-cs-muted font-mono">
              {edges.filter(e => (e.confidence ?? 0) >= 0.85).length} strong
            </span>
            <span className="text-cs-muted/30">·</span>
            <span className="text-[10px] text-cs-muted font-mono">
              {edges.filter(e => (e.confidence ?? 0) < 0.85).length} moderate
            </span>
          </div>
        </div>

        {/* Win Rate */}
        <div className="cs-card p-5 group hover:border-cs-success/40 transition-all duration-300">
          <div className="flex items-center justify-between">
            <span className="cs-stat-label flex items-center gap-1.5">
              <TrendingUp className="w-3.5 h-3.5 text-cs-success" />
              Win Rate (30d)
            </span>
            <span className="cs-badge-success">{winCount} bets</span>
          </div>
          <span className="cs-stat text-4xl mt-3 block text-gradient-success">
            {winCount > 0 ? `${betStats?.win_rate.toFixed(1)}%` : '—'}
          </span>
          {/* Progress bar */}
          <div className="mt-3 h-1.5 w-full rounded-full bg-cs-dark overflow-hidden">
            <div
              className="h-full rounded-full bg-gradient-to-r from-emerald-600 to-emerald-400 transition-all duration-700"
              style={{ width: `${Math.min(100, betStats?.win_rate ?? 0)}%` }}
            />
          </div>
        </div>

        {/* Bankroll P&L */}
        <div className="cs-card p-5 group hover:border-cs-info/40 transition-all duration-300">
          <div className="flex items-center justify-between">
            <span className="cs-stat-label flex items-center gap-1.5">
              <Activity className="w-3.5 h-3.5 text-cs-info" />
              Bankroll P&L
            </span>
            <span className="cs-badge-info">All settled</span>
          </div>
          <span
            className="cs-stat text-4xl mt-3 block"
            style={{ color: (betStats?.total_profit ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}
          >
            {betStats
              ? `${betStats.total_profit >= 0 ? '+' : '-'}$${Math.abs(betStats.total_profit).toLocaleString(undefined, { maximumFractionDigits: 0 })}`
              : '—'}
          </span>
          {betStats?.avg_clv !== undefined && (
            <p className="text-[10px] text-cs-muted font-mono mt-3">
              Avg CLV: {betStats.avg_clv.toFixed(1)}%
            </p>
          )}
        </div>
      </div>

      {/* ── Main Content: 2 columns ────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
        {/* LEFT COLUMN (3/5) — Tables */}
        <div className="lg:col-span-3 space-y-5">
          {/* Live EV Discrepancies Table */}
          <div className="cs-card p-0 overflow-hidden">
            <div className="px-5 pt-5 pb-3 border-b border-cs-border flex items-center justify-between">
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                <CircleDot className="w-4 h-4 text-cs-red-bright animate-pulse-slow" />
                Live EV Discrepancies
              </h2>
              <span className="text-[9px] font-mono text-cs-muted uppercase tracking-wider">
                {edges.length} rows
              </span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full text-sm text-left">
                <thead>
                  <tr className="border-b border-cs-border text-cs-muted text-[10px] uppercase tracking-wider">
                    <th className="px-5 py-3 font-semibold">Player</th>
                    <th className="px-5 py-3 font-semibold">Market</th>
                    <th className="px-5 py-3 font-semibold text-right">Book</th>
                    <th className="px-5 py-3 font-semibold text-right">True</th>
                    <th className="px-5 py-3 font-semibold text-right">Edge%</th>
                    <th className="px-5 py-3 font-semibold text-center">Conf</th>
                  </tr>
                </thead>
                <tbody>
                  {edges.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-5 py-12 text-center text-cs-muted font-mono text-xs">
                        <div className="flex flex-col items-center gap-2">
                          <BarChart3 className="w-6 h-6 text-cs-muted/30" />
                          <span>No live edges detected yet.</span>
                          <span className="text-cs-muted/50">Agent 11 publishes here when market divergence is found.</span>
                        </div>
                      </td>
                    </tr>
                  ) : (
                    edges.map((row, i) => {
                      const ev = edgeValue(row);
                      return (
                        <tr
                          key={row.trace_id ?? i}
                          className={`border-b border-cs-border/40 transition-colors hover:bg-white/[0.02] ${
                            i % 2 === 0 ? 'bg-cs-dark/30' : 'bg-transparent'
                          }`}
                        >
                          <td className="px-5 py-3.5 font-semibold text-white whitespace-nowrap">
                            {row.player ?? '—'}
                            {row.over_under && (
                              <span className="ml-1.5 text-[9px] font-mono text-cs-muted">{row.over_under}</span>
                            )}
                          </td>
                          <td className="px-5 py-3.5 text-cs-muted text-xs">
                            {row.stat ?? row.market_classification ?? '—'}
                            {row.book && (
                              <span className="block text-[9px] font-mono text-cs-muted/50 mt-0.5">{row.book}</span>
                            )}
                          </td>
                          <td className="px-5 py-3.5 text-cs-muted font-mono text-xs text-right">
                            {row.line !== undefined ? `${row.line}` : '—'}
                            {row.odds !== null && row.odds !== undefined && (
                              <span className="text-cs-muted/50"> @ {row.odds}</span>
                            )}
                          </td>
                          <td className="px-5 py-3.5 text-white font-mono text-xs text-right">
                            {row.true_line ?? '—'}
                          </td>
                          <td className="px-5 py-3.5 text-right">
                            <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold border ${edgeColorClass(ev)} ${edgeGlowClass(ev)}`}>
                              {ev > 0 ? '+' : ''}{ev.toFixed(1)}%
                            </span>
                          </td>
                          <td className="px-5 py-3.5 text-center">
                            <div className="flex items-center justify-center gap-1">
                              <div className="w-8 h-1 rounded-full bg-cs-dark overflow-hidden">
                                <div
                                  className={`h-full rounded-full ${(row.confidence ?? 0) >= 0.85 ? 'bg-emerald-500' : (row.confidence ?? 0) >= 0.6 ? 'bg-amber-500' : 'bg-cs-muted'}`}
                                  style={{ width: `${Math.round((row.confidence ?? 0) * 100)}%` }}
                                />
                              </div>
                              <span className="text-[9px] font-mono text-cs-muted w-5 text-left">
                                {Math.round((row.confidence ?? 0) * 100)}
                              </span>
                            </div>
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Line Velocity Anomalies */}
          <div className="cs-card p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-bold text-white flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-cs-warning" />
                Line Movement Velocity
              </h2>
              <span className="cs-badge-warning text-[9px]">Agent 17</span>
            </div>

            <div className="space-y-2">
              {velocityAlerts.length === 0 ? (
                <div className="text-center py-6">
                  <Activity className="w-5 h-5 text-cs-muted/30 mx-auto mb-2" />
                  <p className="text-xs text-cs-muted font-mono">No velocity anomalies detected.</p>
                </div>
              ) : (
                velocityAlerts.slice(0, 5).map((item, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between bg-cs-dark/40 border border-cs-border/30 rounded-xl px-4 py-3 hover:border-cs-border/60 transition-colors"
                  >
                    <div className="flex items-center gap-3 min-w-0">
                      <span className={`shrink-0 text-xs font-bold px-1.5 py-0.5 rounded font-mono ${
                        item.direction === 'UP' ? 'text-emerald-400 bg-emerald-500/10' : 'text-cs-danger bg-cs-danger-dim'
                      }`}>
                        {item.direction === 'UP' ? <ArrowUpRight className="w-3 h-3 inline" /> : <ArrowDownRight className="w-3 h-3 inline" />}
                      </span>
                      <div className="min-w-0">
                        <span className="text-sm font-semibold text-white">{item.player}</span>
                        <span className="text-[10px] text-cs-muted font-mono ml-2">{item.stat}</span>
                        <p className="text-[10px] text-cs-muted/70 truncate">{item.reason}</p>
                      </div>
                    </div>
                    <div className="text-right shrink-0 ml-3">
                      <div className="text-xs font-mono font-bold text-white">{item.delta}</div>
                      <div className="text-[9px] font-mono text-cs-muted">{item.duration_seconds}s</div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* RIGHT COLUMN (2/5) — Alerts + Sharp */}
        <div className="lg:col-span-2 space-y-5">
          {/* Alert Stream */}
          <div className="cs-card p-0 overflow-hidden flex flex-col">
            <div className="px-5 pt-5 pb-0 border-b border-cs-border flex items-center justify-between">
              <h2 className="text-base font-bold text-white flex items-center gap-2">
                <Zap className="w-4 h-4 text-cs-red-bright" />
                Alert Stream
              </h2>
              <span className="relative flex h-2.5 w-2.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-cs-success opacity-75" />
                <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-cs-success" />
              </span>
            </div>
            <AlertFeed messages={messages} maxHeight="max-h-[420px]" />
          </div>

          {/* Sharp Consensus */}
          <div className="cs-card p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-sm font-bold text-white flex items-center gap-2">
                <TrendingUp className="w-4 h-4 text-cs-success" />
                Sharp Consensus
              </h2>
              <span className="cs-badge-success text-[9px]">Agent 19</span>
            </div>

            <div className="space-y-2">
              {sharpConsensus.length === 0 ? (
                <div className="text-center py-6">
                  <TrendingUp className="w-5 h-5 text-cs-muted/30 mx-auto mb-2" />
                  <p className="text-xs text-cs-muted font-mono">No sharp moves detected.</p>
                </div>
              ) : (
                sharpConsensus.slice(0, 5).map((item, idx) => (
                  <div
                    key={idx}
                    className="flex items-center justify-between bg-cs-dark/40 border border-cs-border/30 rounded-xl px-4 py-3 hover:border-cs-border/60 transition-colors"
                  >
                    <div>
                      <span className="text-sm font-semibold text-white">{item.player}</span>
                      <span className="text-[10px] text-cs-muted font-mono ml-2">{item.stat}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[9px] font-mono font-bold text-cs-muted bg-cs-dark px-1.5 py-0.5 rounded">
                        {item.book}
                      </span>
                      <span className={`text-xs font-mono font-bold flex items-center gap-0.5 ${
                        item.direction === 'UP' ? 'text-emerald-400' : 'text-cs-danger'
                      }`}>
                        {item.direction === 'UP' ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                        {item.move}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
