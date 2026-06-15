import { useEffect, useState } from 'react';
import {
  Server,
  Cpu,
  Shield,
  BarChart3,
  Activity,
  BrainCircuit,
  Eye,
  Sparkles,
  AlertTriangle,
} from 'lucide-react';
import { API_BASE } from '../lib/config';
import ConfidenceRing from '../components/ConfidenceRing';
import TrustGauge from '../components/TrustGauge';

/* ── Types ── */

interface AgentStatus {
  id: string;
  name: string;
  status: 'online' | 'offline';
  port: number | null;
}

interface MetaConfidence {
  overall_score?: number;
  mode?: string;
  projection_trust?: number;
  market_trust?: number;
  context_trust?: number;
  execution_trust?: number;
  reason?: string;
  timestamp?: string;
}

interface RiskReport {
  risk_level?: string;
  breaches?: Array<{ type: string; entity?: string; exposure?: number }>;
  utilization?: number;
  open_bets?: number;
}

interface BacktestReport {
  period_days?: number;
  win_rate?: number;
  pnl?: number;
  roi?: number;
  total_bets?: number;
}

/* ── Helpers ── */

function statusColor(status: string): string {
  return status === 'online' ? 'bg-emerald-400' : 'bg-red-400';
}

function statusGlow(status: string): string {
  return status === 'online'
    ? 'shadow-[0_0_6px_rgba(34,197,94,0.6)]'
    : 'shadow-[0_0_6px_rgba(239,68,68,0.6)]';
}

function modeBadgeClass(mode: string): string {
  switch (mode) {
    case 'hot_streak': return 'cs-badge-success';
    case 'cold_streak': return 'cs-badge-warning';
    case 'halt': return 'cs-badge-danger';
    default: return 'cs-badge-info';
  }
}

function modeBorderColor(mode: string): string {
  switch (mode) {
    case 'hot_streak': return 'border-emerald-500/30';
    case 'cold_streak': return 'border-amber-500/30';
    case 'halt': return 'border-red-500/50';
    default: return 'border-blue-500/30';
  }
}

const trustColor = (v: number = 0) => {
  if (v >= 0.75) return 'emerald' as const;
  if (v >= 0.55) return 'blue' as const;
  if (v >= 0.35) return 'amber' as const;
  return 'red' as const;
};

/* ═══════════════════════════════════════════════════════════════════ */

export default function SystemStatus() {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [meta, setMeta] = useState<MetaConfidence | null>(null);
  const [risk, setRisk] = useState<RiskReport | null>(null);
  const [backtest, setBacktest] = useState<BacktestReport | null>(null);

  useEffect(() => {
    const fetchAgents = async () => {
      try {
        const res = await fetch(`${API_BASE}/agents/health`);
        if (res.ok) setAgents(await res.json());
      } catch (err) {
        console.error('Failed to fetch agent health:', err);
      }
    };
    fetchAgents();
    const interval = setInterval(fetchAgents, 10000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [metaRes, riskRes, backtestRes] = await Promise.all([
          fetch(`${API_BASE}/meta/analysis`),
          fetch(`${API_BASE}/risk/reports`),
          fetch(`${API_BASE}/backtest/reports`),
        ]);
        if (metaRes.ok) {
          const data = await metaRes.json();
          setMeta(Array.isArray(data) ? data[0]?.confidence ?? data[0] : data?.confidence ?? data);
        }
        if (riskRes.ok) {
          const data = await riskRes.json();
          setRisk(Array.isArray(data) ? data[0]?.report ?? data[0] : data?.report ?? data);
        }
        if (backtestRes.ok) {
          const data = await backtestRes.json();
          setBacktest(Array.isArray(data) ? data[0]?.report ?? data[0] : data?.report ?? data);
        }
      } catch (err) {
        console.error('Failed to fetch system data:', err);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  const onlineCount = agents.filter((a) => a.status === 'online').length;
  const mode = meta?.mode ?? 'normal';

  return (
    <div className="p-4 md:p-8 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in space-y-6">
      {/* ── Header ── */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-cs-red/10 border border-cs-red/20 flex items-center justify-center shadow-glow-red-sm">
          <Server className="w-5 h-5 text-cs-red" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">System Status</h1>
          <p className="text-sm text-cs-muted">
            {onlineCount}/{agents.length} agents online
          </p>
        </div>
      </div>

      {/* ── Top Row: Meta Analysis + KPIs ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {/* Meta-Agent Deep Dive */}
        <div className={`cs-card p-6 ${modeBorderColor(mode)} border-l-2`}>
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <BrainCircuit className="w-4 h-4 text-cs-red" />
              <h2 className="text-sm font-bold text-white uppercase tracking-wider">Agent 28 — Prefrontal Cortex</h2>
            </div>
            <span className={modeBadgeClass(mode)}>{mode.toUpperCase()}</span>
          </div>

          <div className="flex items-center gap-5">
            <ConfidenceRing value={meta?.overall_score ?? 0} size={64} strokeWidth={5} mode={mode} />
            <div className="flex-1 space-y-2">
              <TrustGauge label="Proj" value={meta?.projection_trust ?? 0} color={trustColor(meta?.projection_trust)} />
              <TrustGauge label="Mkt" value={meta?.market_trust ?? 0} color={trustColor(meta?.market_trust)} />
              <TrustGauge label="Ctx" value={meta?.context_trust ?? 0} color={trustColor(meta?.context_trust)} />
              <TrustGauge label="Exec" value={meta?.execution_trust ?? 0} color={trustColor(meta?.execution_trust)} />
            </div>
          </div>

          {meta?.reason && (
            <p className="text-[11px] text-cs-muted mt-4 leading-relaxed">{meta.reason}</p>
          )}
          {meta?.timestamp && (
            <p className="text-[9px] text-cs-muted/50 font-mono mt-2">
              Updated: {new Date(meta.timestamp).toLocaleTimeString()}
            </p>
          )}
        </div>

        {/* KPI Cards */}
        <div className="lg:col-span-2 grid grid-cols-1 sm:grid-cols-3 gap-4">
          {/* Meta Confidence */}
          <div className="cs-card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Cpu className="w-4 h-4 text-cs-red" />
              <span className="text-[10px] text-cs-muted uppercase tracking-wider">Confidence</span>
            </div>
            <span className="text-2xl font-black text-white">
              {meta ? `${(meta.overall_score ?? 0).toFixed(0)}%` : '—'}
            </span>
            <div className="mt-2 h-1 w-full rounded-full bg-cs-dark overflow-hidden">
              <div
                className="h-full rounded-full bg-gradient-to-r from-cs-red to-cs-red-bright transition-all duration-700"
                style={{ width: `${Math.round((meta?.overall_score ?? 0) * 100)}%` }}
              />
            </div>
          </div>

          {/* Risk Level */}
          <div className="cs-card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Shield className="w-4 h-4 text-cs-red" />
              <span className="text-[10px] text-cs-muted uppercase tracking-wider">Risk Level</span>
            </div>
            <span className={`text-2xl font-black ${risk?.risk_level === 'HIGH' ? 'text-cs-danger' : 'text-cs-success'}`}>
              {risk?.risk_level ?? '—'}
            </span>
            <p className="text-[10px] text-cs-muted mt-2 font-mono">
              {risk ? `${risk.open_bets} open · ${(risk.utilization ?? 0).toFixed(1)}% util` : 'No data'}
            </p>
          </div>

          {/* Backtest */}
          <div className="cs-card p-5">
            <div className="flex items-center gap-2 mb-3">
              <BarChart3 className="w-4 h-4 text-cs-red" />
              <span className="text-[10px] text-cs-muted uppercase tracking-wider">Backtest (30d)</span>
            </div>
            <span className="text-2xl font-black text-white">
              {backtest ? `${(backtest.win_rate ?? 0).toFixed(1)}%` : '—'}
            </span>
            <p className="text-[10px] text-cs-muted mt-2 font-mono">
              {backtest ? `${backtest.total_bets} bets · $${(backtest.pnl ?? 0).toFixed(0)} PnL` : 'No data'}
            </p>
          </div>

          {/* Additional system cards in a 2-col sub-grid */}
          <div className="cs-card p-5 sm:col-span-2">
            <div className="flex items-center gap-2 mb-3">
              <Eye className="w-4 h-4 text-cs-info" />
              <span className="text-[10px] text-cs-muted uppercase tracking-wider">System Explainability</span>
            </div>
            <p className="text-xs text-cs-muted leading-relaxed">
              Every pick passes through a 4-stage validation mesh: Projection → Validation Gate (Agent 24)
              → Claim Verifier (Agent 25) → Publisher (Agent 26). Rejections are triaged by Agent 27.
              The Meta-Agent (Agent 28) synthesizes all trust signals to determine system mode.
            </p>
          </div>

          <div className="cs-card p-5">
            <div className="flex items-center gap-2 mb-3">
              <Sparkles className="w-4 h-4 text-cs-warning" />
              <span className="text-[10px] text-cs-muted uppercase tracking-wider">Retraining</span>
            </div>
            <span className="text-2xl font-black text-cs-success">AUTO</span>
            <p className="text-[10px] text-cs-muted mt-2 font-mono">
              Scheduled daily at 06:00 UTC
            </p>
          </div>
        </div>
      </div>

      {/* ── Agent Mesh Grid ── */}
      <div className="cs-card p-0 overflow-hidden">
        <div className="px-6 pt-6 pb-4 border-b border-cs-border flex items-center justify-between">
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <Server className="w-4 h-4 text-cs-red-bright" />
            Agent Mesh
          </h2>
          <div className="flex items-center gap-3">
            <span className="flex items-center gap-1.5 text-[10px] font-mono text-cs-success">
              <span className="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_6px_rgba(34,197,94,0.6)]" />
              {onlineCount}
            </span>
            <span className="flex items-center gap-1.5 text-[10px] font-mono text-cs-muted">
              <span className="w-2 h-2 rounded-full bg-red-400" />
              {agents.length - onlineCount}
            </span>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="border-b border-cs-border text-cs-muted text-[10px] uppercase tracking-wider">
                <th className="px-6 py-3 font-semibold">ID</th>
                <th className="px-6 py-3 font-semibold">Name</th>
                <th className="px-6 py-3 font-semibold text-center">Status</th>
                <th className="px-6 py-3 font-semibold text-right">Port</th>
              </tr>
            </thead>
            <tbody>
              {agents.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-10 text-center text-cs-muted font-mono text-xs">
                    <Activity className="w-5 h-5 text-cs-muted/30 mx-auto mb-2" />
                    No agent health data. Redis may be offline.
                  </td>
                </tr>
              ) : (
                agents.map((agent, i) => (
                  <tr
                    key={agent.id}
                    className={`border-b border-cs-border/40 transition-colors hover:bg-white/[0.02] ${
                      i % 2 === 0 ? 'bg-cs-dark/30' : 'bg-transparent'
                    }`}
                  >
                    <td className="px-6 py-3 font-mono text-cs-muted text-xs">{agent.id}</td>
                    <td className="px-6 py-3 font-medium text-white">{agent.name}</td>
                    <td className="px-6 py-3 text-center">
                      <span className="inline-flex items-center gap-1.5">
                        <span className={`w-2 h-2 rounded-full ${statusColor(agent.status)} ${statusGlow(agent.status)}`} />
                        <span className={`text-xs font-bold uppercase ${agent.status === 'online' ? 'text-emerald-400' : 'text-red-400'}`}>
                          {agent.status}
                        </span>
                      </span>
                    </td>
                    <td className="px-6 py-3 text-right font-mono text-xs text-cs-muted">
                      {agent.port ?? '—'}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Risk Breaches ── */}
      {risk?.breaches && risk.breaches.length > 0 && (
        <div className="cs-card p-6 border-red-500/20 animate-slide-up">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle className="w-4 h-4 text-cs-danger" />
            <h2 className="text-sm font-bold text-cs-danger uppercase tracking-wider">Active Risk Breaches</h2>
          </div>
          <div className="space-y-2">
            {risk.breaches.map((breach, i) => (
              <div key={i} className="bg-cs-danger-dim border border-red-500/20 rounded-lg p-3 flex items-center justify-between">
                <span className="text-sm font-medium text-white">{breach.type}</span>
                <span className="text-xs font-mono text-cs-danger">
                  {breach.entity} {breach.exposure ? `· ${(breach.exposure * 100).toFixed(1)}%` : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
