import { useState, useEffect } from 'react';
import { PieChart as PieChartIcon, TrendingUp, TrendingDown, Shield, Sparkles, AlertCircle } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { API_BASE } from '../lib/config';

// Daily average CLV computed from real settled bets (clv_pct + settled_at).
function buildClvSeries(betRows: any[]): { date: string; clv: number }[] {
  const byDay: Record<string, number[]> = {};
  betRows.forEach((b) => {
    if (b.clv_pct === null || b.clv_pct === undefined || !b.settled_at) return;
    const d = new Date(b.settled_at);
    const key = `${d.getMonth() + 1}/${d.getDate()}`;
    (byDay[key] = byDay[key] || []).push(b.clv_pct);
  });
  return Object.entries(byDay)
    .map(([date, vals]) => ({
      date,
      clv: +(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(2),
    }))
    .slice(-30);
}

function fmtMoney(v: number): string {
  const sign = v < 0 ? '-' : v > 0 ? '+' : '';
  return `${sign}$${Math.abs(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function DrawdownGauge({ percentage = 34 }: { percentage?: number }) {
  const rotation = (percentage / 100) * 180;

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-44 h-24 overflow-hidden">
        {/* Background arc */}
        <div
          className="absolute inset-0 w-44 h-44 rounded-full border-[10px] border-cs-border/30"
          style={{ clipPath: 'inset(0 0 50% 0)' }}
        />
        {/* Colored arc */}
        <div
          className="absolute inset-0 w-44 h-44 rounded-full border-[10px] border-transparent"
          style={{
            borderTopColor: percentage > 70 ? '#ef4444' : percentage > 40 ? '#eab308' : '#22c55e',
            borderRightColor: rotation > 90 ? (percentage > 70 ? '#ef4444' : percentage > 40 ? '#eab308' : '#22c55e') : 'transparent',
            transform: `rotate(${rotation > 180 ? 180 : 0}deg)`,
            clipPath: 'inset(0 0 50% 0)',
          }}
        />
        {/* Needle */}
        <div
          className="absolute bottom-0 left-1/2 w-0.5 h-20 bg-white origin-bottom rounded-full shadow-lg"
          style={{ transform: `translateX(-50%) rotate(${rotation - 90}deg)` }}
        />
        {/* Center dot */}
        <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2 w-3 h-3 rounded-full bg-white shadow-glow-red-sm" />
      </div>
      <div className="mt-3 text-center">
        <div className="text-2xl font-black text-white">{percentage}%</div>
        <div className="text-xs text-cs-muted uppercase tracking-wider">Current Drawdown</div>
      </div>
    </div>
  );
}

export default function BankrollDiagnostics() {
  const [hedges, setHedges] = useState<any[]>([]);
  const [limits, setLimits] = useState<any[]>([]);
  const [recentHedges, setRecentHedges] = useState<any[]>([]);
  const [bankrollHistory, setBankrollHistory] = useState<any[]>([]);
  const [clvSummary, setClvSummary] = useState<any | null>(null);
  const [betStats, setBetStats] = useState<any | null>(null);
  const [clvData, setClvData] = useState<{ date: string; clv: number }[]>([]);

  // Real portfolio data: bankroll history, CLV summary, bet stats.
  useEffect(() => {
    const fetchPortfolio = async () => {
      try {
        const [histRes, clvRes, statsRes, betsRes] = await Promise.all([
          fetch(`${API_BASE}/bankroll/history`),
          fetch(`${API_BASE}/clv/summary`),
          fetch(`${API_BASE}/bets/stats`),
          fetch(`${API_BASE}/bets`),
        ]);
        if (histRes.ok) setBankrollHistory(await histRes.json());
        if (clvRes.ok) setClvSummary(await clvRes.json());
        if (statsRes.ok) setBetStats(await statsRes.json());
        if (betsRes.ok) setClvData(buildClvSeries(await betsRes.json()));
      } catch (err) {
        console.error('Failed to fetch portfolio data:', err);
      }
    };
    fetchPortfolio();
    const interval = setInterval(fetchPortfolio, 15000);
    return () => clearInterval(interval);
  }, []);

  // Derived metrics — entirely from real data; show em-dash when unknown.
  const sortedHist = [...bankrollHistory].sort((a, b) => a.timestamp - b.timestamp);
  const currentBalance = sortedHist.length ? sortedHist[sortedHist.length - 1].balance : null;
  let peak = 0;
  let maxDrawdownDollars = 0;
  sortedHist.forEach((h) => {
    peak = Math.max(peak, h.balance);
    maxDrawdownDollars = Math.max(maxDrawdownDollars, peak - h.balance);
  });
  const currentDrawdownPct =
    sortedHist.length && peak > 0
      ? Math.max(0, Math.round(((peak - currentBalance!) / peak) * 100))
      : 0;
  const isHealthy = currentDrawdownPct < 25;

  const topStats = [
    {
      label: 'Total Bankroll',
      value: currentBalance !== null ? `$${currentBalance.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—',
      icon: PieChartIcon,
      color: 'text-white',
      accent: 'border-cs-border/50',
    },
    {
      label: 'Net P&L',
      value: betStats ? fmtMoney(betStats.total_profit ?? 0) : '—',
      icon: TrendingUp,
      color: (betStats?.total_profit ?? 0) >= 0 ? 'text-emerald-400' : 'text-red-400',
      accent: 'border-emerald-500/20',
    },
    {
      label: 'Avg CLV',
      value: clvSummary && clvSummary.total_tracked > 0 ? `${clvSummary.avg_clv}%` : '—',
      icon: TrendingUp,
      color: 'text-cs-red-bright',
      accent: 'border-cs-red/20',
    },
    {
      label: 'Max Drawdown',
      value: sortedHist.length ? fmtMoney(-maxDrawdownDollars) : '—',
      icon: TrendingDown,
      color: 'text-red-400',
      accent: 'border-red-500/20',
    },
  ];

  const riskMetrics = [
    { label: 'Win Rate', value: betStats ? `${(betStats.win_rate ?? 0).toFixed(1)}%` : '—' },
    { label: 'Settled Bets', value: betStats ? `${(betStats.wins ?? 0) + (betStats.losses ?? 0)}` : '—' },
    { label: '+CLV Bets', value: clvSummary && clvSummary.total_tracked > 0 ? `${clvSummary.positive_clv_pct}%` : '—' },
  ];

  useEffect(() => {
    const fetchHedges = async () => {
      try {
        const res = await fetch(`${API_BASE}/hedges`);
        if (res.ok) {
          const data = await res.json();
          setHedges(data);
        }
      } catch (err) {
        console.error("Failed to fetch hedges:", err);
      }
    };
    fetchHedges();
    const interval = setInterval(fetchHedges, 8000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const fetchLimits = async () => {
      try {
        const res = await fetch(`${API_BASE}/liquidity/limits`);
        if (res.ok) {
          const data = await res.json();
          setLimits(data);
        }
      } catch (err) {
        console.error("Failed to fetch limits:", err);
      }
    };
    const fetchRecentHedges = async () => {
      try {
        const res = await fetch(`${API_BASE}/bets`);
        if (res.ok) {
          const data = await res.json();
          const hedgeBets = data.filter((b: any) => b.is_hedge === 1);
          setRecentHedges(hedgeBets);
        }
      } catch (err) {
        console.error("Failed to fetch recent hedges:", err);
      }
    };
    fetchLimits();
    fetchRecentHedges();
    const intervalLimits = setInterval(fetchLimits, 10000);
    const intervalHedges = setInterval(fetchRecentHedges, 8000);
    return () => {
      clearInterval(intervalLimits);
      clearInterval(intervalHedges);
    };
  }, []);

  return (
    <div className="min-h-screen bg-cs-black p-4 md:p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="w-10 h-10 rounded-xl bg-cs-red/10 border border-cs-red/20 flex items-center justify-center shadow-glow-red-sm">
          <PieChartIcon className="w-5 h-5 text-cs-red" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Bankroll Diagnostics</h1>
          <p className="text-sm text-cs-muted">Real-time portfolio health & risk analytics</p>
        </div>
      </div>

      {/* Top Stat Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {topStats.map(({ label, value, icon: Icon, color, accent }) => (
          <div
            key={label}
            className={`cs-card p-5 border ${accent} animate-slide-up`}
          >
            <div className="flex items-center justify-between mb-3">
              <span className="cs-stat-label">{label}</span>
              <Icon className="w-4 h-4 text-cs-muted" />
            </div>
            <div className={`text-2xl font-black ${color}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Main Area — 2 Columns */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* CLV Over Time */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '80ms' }}>
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-cs-red" />
              <h2 className="text-sm font-semibold text-white uppercase tracking-wider">CLV Over Time</h2>
            </div>
            <span className="cs-badge">30 Days</span>
          </div>

          <div className="h-72">
            {clvData.length === 0 ? (
              <div className="h-full flex items-center justify-center text-cs-muted font-mono text-xs">
                No settled bets with closing-line data yet.
              </div>
            ) : (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={clvData}>
                <defs>
                  <linearGradient id="clvGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#dc2626" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#dc2626" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="date"
                  stroke="#444"
                  tick={{ fill: '#666', fontSize: 10 }}
                  axisLine={{ stroke: '#333' }}
                  interval={4}
                />
                <YAxis
                  stroke="#444"
                  tick={{ fill: '#666', fontSize: 11 }}
                  axisLine={{ stroke: '#333' }}
                  domain={[0, 10]}
                  tickFormatter={(v: number) => `${v}%`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#111111',
                    border: '1px solid #333',
                    borderRadius: '12px',
                    color: '#fff',
                    fontSize: '12px',
                  }}
                  formatter={(value: any) => [`${value}%`, 'CLV']}
                />
                <Area
                  type="monotone"
                  dataKey="clv"
                  stroke="#dc2626"
                  strokeWidth={2}
                  fill="url(#clvGradient)"
                  dot={false}
                  activeDot={{ r: 4, fill: '#dc2626', stroke: '#fff', strokeWidth: 2 }}
                />
              </AreaChart>
            </ResponsiveContainer>
            )}
          </div>
        </div>

        {/* Risk Metrics */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '160ms' }}>
          <div className="flex items-center gap-2 mb-6">
            <Shield className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Risk Metrics</h2>
          </div>

          <div className="flex flex-col items-center">
            {/* Drawdown Gauge */}
            <DrawdownGauge percentage={currentDrawdownPct} />

            {/* Metric Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 w-full mt-8">
              {riskMetrics.map(({ label, value }) => (
                <div
                  key={label}
                  className="bg-cs-black/60 border border-cs-border/50 rounded-xl p-4 text-center"
                >
                  <div className="cs-stat">{value}</div>
                  <div className="cs-stat-label">{label}</div>
                </div>
              ))}
            </div>

            {/* System Status Badge */}
            <div className="mt-8">
              {isHealthy ? (
                <div className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-emerald-500/10 border border-emerald-500/30">
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-400" />
                  </span>
                  <span className="text-xs font-bold text-emerald-400 uppercase tracking-widest">
                    System Healthy
                  </span>
                </div>
              ) : (
                <div className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-red-500/10 border border-red-500/30">
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-400" />
                  </span>
                  <span className="text-xs font-bold text-red-400 uppercase tracking-widest">
                    Halt
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Hedging & Liquidity Console Grid ─────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
        {/* Dynamic Hedging & Arbitrage Console */}
        <div className="lg:col-span-2 cs-card p-6 animate-slide-up" style={{ animationDelay: '240ms' }}>
          <div className="border-b border-cs-border/40 pb-4 flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <Sparkles className="w-5 h-5 text-cs-red" />
              <div>
                <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Agent 16: Dynamic Hedging & Arbitrage Oracle</h2>
                <p className="text-[10px] text-cs-muted mt-0.5 font-mono">MITIGATE VARIANCE & LOCK IN EV PROFIT</p>
              </div>
            </div>
            <span className="text-[9px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/30 px-2.5 py-0.5 rounded font-mono font-bold">
              LOCKS GENERATED
            </span>
          </div>

          <div className="overflow-x-auto mt-4">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-cs-border/30 text-cs-muted uppercase font-mono text-[9px] tracking-wider">
                  <th className="pb-3 px-3">Player / Matchup</th>
                  <th className="pb-3 px-3 text-center">Original Wager</th>
                  <th className="pb-3 px-3 text-center">Live Book Line</th>
                  <th className="pb-3 px-3 text-right">Lock-in Profit</th>
                  <th className="pb-3 px-3">Hedge Strategy / Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-cs-border/20">
                {hedges.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="py-8 text-center text-cs-muted font-mono text-[11px]">
                      No active arbitrage or hedging opportunities detected. Scanning live feeds...
                    </td>
                  </tr>
                ) : (
                  hedges.map((hedge, idx) => (
                    <tr key={hedge.id || idx} className="hover:bg-cs-dark/20 transition-colors">
                      <td className="py-4 px-3">
                        <div className="font-bold text-white text-sm">{hedge.hedged_player}</div>
                        <div className="text-[10px] text-cs-muted font-mono">Bet ID: #{hedge.bet_id}</div>
                      </td>
                      <td className="py-4 px-3 text-center font-mono">
                        <div className="text-white font-bold">{hedge.original_line}</div>
                        <div className="text-[10px] text-cs-muted">{hedge.original_odds > 0 ? `+${hedge.original_odds}` : hedge.original_odds}</div>
                      </td>
                      <td className="py-4 px-3 text-center font-mono">
                        <div className="text-emerald-400 font-bold">{hedge.live_line}</div>
                        <div className="text-[10px] text-cs-muted">{hedge.live_odds > 0 ? `+${hedge.live_odds}` : hedge.live_odds}</div>
                      </td>
                      <td className="py-4 px-3 text-right font-mono text-emerald-400 font-bold text-sm">
                        +${hedge.potential_profit.toFixed(2)}
                      </td>
                      <td className="py-4 px-3 max-w-sm">
                        <div className="text-white flex items-start gap-1.5 leading-relaxed bg-cs-black/40 border border-cs-border/40 p-2.5 rounded-lg text-[11px] font-mono">
                          <AlertCircle className="w-3.5 h-3.5 text-cs-red-bright shrink-0 mt-0.5" />
                          <span>{hedge.hedge_instructions}</span>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right side widgets: Limits Grid and Recent Automated Hedge Executions */}
        <div className="lg:col-span-1 space-y-6 flex flex-col">
          {/* Sportsbook Limits & Liquidity */}
          <div className="cs-card p-6 flex-1 animate-slide-up" style={{ animationDelay: '300ms' }}>
            <div className="border-b border-cs-border/40 pb-4 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Shield className="w-5 h-5 text-cs-red" />
                <div>
                  <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Agent 18: Sportsbook Limits</h2>
                  <p className="text-[10px] text-cs-muted mt-0.5 font-mono">LIQUIDITY & BET LIMIT MONITOR</p>
                </div>
              </div>
            </div>

            <div className="space-y-3 mt-4">
              {limits.length === 0 ? (
                <p className="text-xs text-cs-muted font-mono text-center py-6">Loading sportsbook limits...</p>
              ) : (
                limits.map((lim, idx) => (
                  <div key={idx} className="flex items-center justify-between bg-cs-black/40 border border-cs-border/30 rounded-xl p-3 hover:border-cs-border/60 transition-colors">
                    <div>
                      <div className="font-bold text-white text-sm">{lim.book}</div>
                      <span className="text-[9px] text-cs-muted bg-cs-dark px-1.5 py-0.2 rounded font-mono font-bold tracking-wider">{lim.type}</span>
                    </div>
                    <div className="text-right">
                      <div className="text-[9px] text-cs-muted font-mono">MAX LIMIT</div>
                      <div className="text-sm font-black text-white font-mono">${lim.limit}</div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Agent 20: Recent Automated Hedge Executions */}
          <div className="cs-card p-6 flex-1 animate-slide-up" style={{ animationDelay: '360ms' }}>
            <div className="border-b border-cs-border/40 pb-4 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <Sparkles className="w-5 h-5 text-cs-red" />
                <div>
                  <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Agent 20: Hedge Executions</h2>
                  <p className="text-[10px] text-cs-muted mt-0.5 font-mono">AUTOMATED ARBITRAGE ENTRIES</p>
                </div>
              </div>
            </div>

            <div className="space-y-3 mt-4 max-h-[200px] overflow-y-auto scrollbar-thin scrollbar-thumb-cs-border">
              {recentHedges.length === 0 ? (
                <p className="text-xs text-cs-muted font-mono text-center py-6">No automated hedge executions recorded.</p>
              ) : (
                recentHedges.map((hedge, idx) => (
                  <div key={idx} className="bg-cs-black/40 border border-cs-border/30 rounded-xl p-3 hover:border-cs-border/60 transition-colors">
                    <div className="flex justify-between items-start">
                      <div>
                        <div className="font-bold text-white text-sm">{hedge.player}</div>
                        <div className="text-[10px] text-cs-muted font-mono">{hedge.stat} &bull; {hedge.over_under} {hedge.line}</div>
                      </div>
                      <span className="text-[9px] font-mono font-bold px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/30">
                        EXECUTED
                      </span>
                    </div>
                    <div className="flex justify-between items-center mt-3 border-t border-cs-border/20 pt-2 font-mono">
                      <div>
                        <span className="text-[9px] text-cs-muted block">STAKE</span>
                        <span className="text-xs text-white font-bold">${hedge.stake}</span>
                      </div>
                      <div className="text-right">
                        <span className="text-[9px] text-cs-muted block">ODDS</span>
                        <span className="text-xs text-white font-bold">{hedge.book_odds > 0 ? `+${hedge.book_odds}` : hedge.book_odds}</span>
                      </div>
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
