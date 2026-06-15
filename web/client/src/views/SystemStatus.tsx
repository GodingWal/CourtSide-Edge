import { useEffect, useState } from 'react';
import { Server, Activity, AlertTriangle, Shield, Cpu, BarChart3 } from 'lucide-react';
import { API_BASE } from '../lib/config';

/* ── Types ── */

interface AgentStatus {
  id: string;
  name: string;
  status: 'online' | 'offline';
  port: number | null;
}

interface MetaAnalysis {
  confidence?: {
    overall_score?: number;
    mode?: string;
    projection_trust?: number;
    market_trust?: number;
    context_trust?: number;
    execution_trust?: number;
  };
}

interface RiskReport {
  report?: {
    risk_level?: string;
    breaches?: Array<{
      type: string;
      entity?: string;
      exposure?: number;
    }>;
    utilization?: number;
    open_bets?: number;
  };
}

interface BacktestReport {
  report?: {
    win_rate?: number;
    pnl?: number;
    roi?: number;
    total_bets?: number;
  };
}

interface RetrainingAdvisory {
  evaluation?: {
    severity?: string;
    recommended_action?: string;
    triggers?: string[];
  };
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

function modeBadge(mode: string): string {
  switch (mode) {
    case 'hot_streak':
      return 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30';
    case 'cold_streak':
      return 'bg-amber-500/15 text-amber-400 border-amber-500/30';
    case 'halt':
      return 'bg-red-500/15 text-red-400 border-red-500/30';
    default:
      return 'bg-cs-border/20 text-cs-muted border-cs-border/40';
  }
}

function EmptyState({ text }: { text: string }) {
  return <p className="text-xs text-cs-muted font-mono text-center py-6">{text}</p>;
}

/* ═══════════════════════════════════════════════════════════════════ */

export default function SystemStatus() {
  const [agents, setAgents] = useState<AgentStatus[]>([]);
  const [meta, setMeta] = useState<MetaAnalysis[]>([]);
  const [risk, setRisk] = useState<RiskReport[]>([]);
  const [backtest, setBacktest] = useState<BacktestReport[]>([]);
  const [retraining, setRetraining] = useState<RetrainingAdvisory[]>([]);

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
        const [metaRes, riskRes, backtestRes, retrainRes] = await Promise.all([
          fetch(`${API_BASE}/meta/analysis`),
          fetch(`${API_BASE}/risk/reports`),
          fetch(`${API_BASE}/backtest/reports`),
          fetch(`${API_BASE}/retraining/advisories`),
        ]);
        if (metaRes.ok) setMeta(await metaRes.json());
        if (riskRes.ok) setRisk(await riskRes.json());
        if (backtestRes.ok) setBacktest(await backtestRes.json());
        if (retrainRes.ok) setRetraining(await retrainRes.json());
      } catch (err) {
        console.error('Failed to fetch system data:', err);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 30000);
    return () => clearInterval(interval);
  }, []);

  const onlineCount = agents.filter((a) => a.status === 'online').length;
  const latestMeta = meta[0]?.confidence;
  const latestRisk = risk[0]?.report;
  const latestBacktest = backtest[0]?.report;
  const latestRetraining = retraining[0]?.evaluation;

  return (
    <div className="min-h-screen bg-cs-black p-4 md:p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-6">
        <div className="w-10 h-10 rounded-xl bg-cs-red/10 border border-cs-red/20 flex items-center justify-center shadow-glow-red-sm">
          <Server className="w-5 h-5 text-cs-red" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">System Status</h1>
          <p className="text-sm text-cs-muted">
            {onlineCount}/{agents.length} agents online
            {latestMeta && (
              <span className="ml-2 text-[10px] font-mono">
                · Meta mode: <span className="uppercase text-cs-red-bright">{latestMeta.mode}</span>
              </span>
            )}
          </p>
        </div>
      </div>

      {/* Top KPI Row */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-5 mb-6">
        <div className="cs-card p-5">
          <div className="flex items-center gap-2 mb-2">
            <Cpu className="w-4 h-4 text-cs-red" />
            <span className="text-[10px] text-cs-muted uppercase tracking-wider">Meta Confidence</span>
          </div>
          <span className="text-2xl font-black text-white">
            {latestMeta ? `${(latestMeta.overall_score ?? 0).toFixed(0)}%` : '—'}
          </span>
          {latestMeta && (
            <span className={`ml-2 text-[10px] font-bold px-2 py-0.5 rounded border ${modeBadge(latestMeta.mode ?? 'normal')}`}>
              {latestMeta.mode?.toUpperCase()}
            </span>
          )}
        </div>

        <div className="cs-card p-5">
          <div className="flex items-center gap-2 mb-2">
            <Shield className="w-4 h-4 text-cs-red" />
            <span className="text-[10px] text-cs-muted uppercase tracking-wider">Risk Level</span>
          </div>
          <span className={`text-2xl font-black ${latestRisk?.risk_level === 'HIGH' ? 'text-red-400' : 'text-emerald-400'}`}>
            {latestRisk?.risk_level ?? '—'}
          </span>
          {latestRisk && (
            <p className="text-[10px] text-cs-muted mt-1">
              {latestRisk.open_bets} open bets · {(latestRisk.utilization ?? 0).toFixed(1)}% utilized
            </p>
          )}
        </div>

        <div className="cs-card p-5">
          <div className="flex items-center gap-2 mb-2">
            <BarChart3 className="w-4 h-4 text-cs-red" />
            <span className="text-[10px] text-cs-muted uppercase tracking-wider">30d Backtest</span>
          </div>
          <span className="text-2xl font-black text-white">
            {latestBacktest ? `${(latestBacktest.win_rate ?? 0).toFixed(1)}%` : '—'}
          </span>
          {latestBacktest && (
            <p className="text-[10px] text-cs-muted mt-1">
              {latestBacktest.total_bets} bets · ${(latestBacktest.pnl ?? 0).toFixed(0)} PnL
            </p>
          )}
        </div>

        <div className="cs-card p-5">
          <div className="flex items-center gap-2 mb-2">
            <Activity className="w-4 h-4 text-cs-red" />
            <span className="text-[10px] text-cs-muted uppercase tracking-wider">Retraining</span>
          </div>
          <span className={`text-2xl font-black ${latestRetraining?.severity === 'HIGH' ? 'text-red-400' : latestRetraining?.severity === 'MEDIUM' ? 'text-amber-400' : 'text-emerald-400'}`}>
            {latestRetraining?.severity ?? '—'}
          </span>
          {latestRetraining && (
            <p className="text-[10px] text-cs-muted mt-1">
              {latestRetraining.recommended_action} · {latestRetraining.triggers?.length ?? 0} triggers
            </p>
          )}
        </div>
      </div>

      {/* Agent Grid */}
      <div className="cs-card p-0 overflow-hidden">
        <div className="px-6 pt-6 pb-4 border-b border-cs-border flex items-center justify-between">
          <h2 className="text-lg font-bold text-white flex items-center gap-2">
            <Server className="w-4 h-4 text-cs-red-bright" />
            Agent Mesh
          </h2>
          <span className="text-[10px] font-mono text-cs-muted">
            {onlineCount} online · {agents.length - onlineCount} offline
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm text-left">
            <thead>
              <tr className="border-b border-cs-border text-cs-muted text-xs uppercase tracking-wider">
                <th className="px-6 py-3 font-semibold">ID</th>
                <th className="px-6 py-3 font-semibold">Name</th>
                <th className="px-6 py-3 font-semibold text-center">Status</th>
                <th className="px-6 py-3 font-semibold text-right">Health</th>
              </tr>
            </thead>
            <tbody>
              {agents.length === 0 ? (
                <tr>
                  <td colSpan={4} className="px-6 py-10 text-center text-cs-muted font-mono text-xs">
                    No agent health data available. Redis may be offline.
                  </td>
                </tr>
              ) : (
                agents.map((agent, i) => (
                  <tr
                    key={agent.id}
                    className={`border-b border-cs-border/40 transition-colors hover:bg-cs-red/[0.04] ${
                      i % 2 === 0 ? 'bg-cs-dark/50' : 'bg-transparent'
                    }`}
                  >
                    <td className="px-6 py-3 font-mono text-cs-muted text-xs">{agent.id}</td>
                    <td className="px-6 py-3 font-medium text-white">{agent.name}</td>
                    <td className="px-6 py-3 text-center">
                      <span className="inline-flex items-center gap-1.5">
                        <span
                          className={`w-2 h-2 rounded-full ${statusColor(agent.status)} ${statusGlow(agent.status)}`}
                        />
                        <span
                          className={`text-xs font-bold uppercase ${
                            agent.status === 'online' ? 'text-emerald-400' : 'text-red-400'
                          }`}
                        >
                          {agent.status}
                        </span>
                      </span>
                    </td>
                    <td className="px-6 py-3 text-right">
                      <span className="text-xs font-mono text-cs-muted">
                        {agent.status === 'online' ? 'heartbeat ok' : 'no heartbeat'}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Risk Breaches */}
      {latestRisk && latestRisk.breaches && latestRisk.breaches.length > 0 && (
        <div className="mt-6 cs-card p-6 border-red-500/20">
          <div className="flex items-center gap-2 mb-4">
            <AlertTriangle className="w-4 h-4 text-red-400" />
            <h2 className="text-sm font-bold text-red-400 uppercase tracking-wider">Active Risk Breaches</h2>
          </div>
          <div className="space-y-2">
            {latestRisk.breaches.map((breach, i) => (
              <div key={i} className="bg-red-500/10 border border-red-500/20 rounded-lg p-3 flex items-center justify-between">
                <span className="text-sm font-medium text-white">{breach.type}</span>
                <span className="text-xs font-mono text-red-400">
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
