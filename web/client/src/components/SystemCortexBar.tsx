/*
  SystemCortexBar — Persistent sticky bar showing Meta-Agent (Agent 28) state.
  Always visible below the header. Color-coded by mode.
  Collapses on scroll for mobile, stays expanded on desktop.
*/

import { useState, useEffect } from 'react';
import {
  BrainCircuit,
  TrendingUp,
  TrendingDown,
  Minus,
  OctagonAlert,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import { API_BASE } from '../lib/config';
import ConfidenceRing from './ConfidenceRing';
import TrustGauge from './TrustGauge';

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

const modeConfig: Record<string, {
  label: string;
  badgeClass: string;
  icon: React.ElementType;
  borderColor: string;
  glowColor: string;
}> = {
  hot_streak: {
    label: 'HOT STREAK',
    badgeClass: 'cs-badge-success',
    icon: TrendingUp,
    borderColor: 'border-emerald-500/30',
    glowColor: 'shadow-glow-success',
  },
  normal: {
    label: 'NORMAL',
    badgeClass: 'cs-badge-info',
    icon: Minus,
    borderColor: 'border-blue-500/30',
    glowColor: 'shadow-glow-info',
  },
  cold_streak: {
    label: 'COLD STREAK',
    badgeClass: 'cs-badge-warning',
    icon: TrendingDown,
    borderColor: 'border-amber-500/30',
    glowColor: 'shadow-glow-warning',
  },
  halt: {
    label: 'HALT',
    badgeClass: 'cs-badge-danger',
    icon: OctagonAlert,
    borderColor: 'border-red-500/50',
    glowColor: 'shadow-glow-danger',
  },
};

export default function SystemCortexBar() {
  const [meta, setMeta] = useState<MetaConfidence | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  const [hidden, setHidden] = useState(false);

  /* Fetch meta-analysis from Agent 28 */
  useEffect(() => {
    const fetchMeta = async () => {
      try {
        const res = await fetch(`${API_BASE}/meta/analysis`);
        if (res.ok) {
          const data = await res.json();
          setMeta(Array.isArray(data) ? data[0]?.confidence ?? data[0] : data?.confidence ?? data);
        }
      } catch {
        // graceful — bar shows "disconnected" state
      }
    };
    fetchMeta();
    const interval = setInterval(fetchMeta, 15000);
    return () => clearInterval(interval);
  }, []);

  /* Auto-collapse on scroll for mobile */
  useEffect(() => {
    const onScroll = () => {
      if (window.innerWidth < 768) {
        setHidden(window.scrollY > 100);
      } else {
        setHidden(false);
      }
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  const mode = meta?.mode ?? 'normal';
  const cfg = modeConfig[mode] ?? modeConfig.normal;
  const overall = meta?.overall_score ?? 0;
  const ModeIcon = cfg.icon;

  /* Determine trust bar colors based on thresholds */
  const trustColor = (v: number = 0) => {
    if (v >= 0.75) return 'emerald' as const;
    if (v >= 0.55) return 'blue' as const;
    if (v >= 0.35) return 'amber' as const;
    return 'red' as const;
  };

  return (
    <>
      {/* Mobile: floating mode pill when scrolled */}
      {hidden && (
        <button
          onClick={() => setHidden(false)}
          className={`fixed top-16 right-3 z-40 flex items-center gap-1.5 px-3 py-1.5 rounded-full border ${cfg.borderColor} ${cfg.badgeClass} shadow-lg md:hidden`}
        >
          <ModeIcon className="w-3 h-3" />
          <span className="text-[10px] font-bold uppercase">{cfg.label}</span>
          <ConfidenceRing value={overall} size={20} strokeWidth={3} mode={mode} />
        </button>
      )}

      <div
        className={`sticky top-14 z-30 transition-all duration-300 ${hidden ? 'opacity-0 pointer-events-none -translate-y-2' : 'opacity-100 translate-y-0'}`}
      >
        <div
          className={`border-b bg-cs-black/90 backdrop-blur-xl ${cfg.borderColor} ${cfg.glowColor}`}
        >
          <div className="max-w-[1440px] mx-auto px-4 md:px-8">
            {/* Main row */}
            <div className="flex items-center gap-3 md:gap-5 py-2">
              {/* Mode badge */}
              <div className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg ${cfg.badgeClass} shrink-0`}>
                <ModeIcon className="w-3.5 h-3.5" />
                <span className="text-[10px] font-black uppercase tracking-wider hidden sm:inline">{cfg.label}</span>
                <span className="text-[10px] font-black uppercase tracking-wider sm:hidden">{cfg.label.slice(0, 4)}</span>
              </div>

              {/* Confidence ring */}
              <ConfidenceRing value={overall} size={36} strokeWidth={3.5} mode={mode} />

              {/* Trust gauges — desktop only */}
              <div className="hidden md:flex items-center gap-4 flex-1 min-w-0">
                <div className="flex-1 max-w-[140px]">
                  <TrustGauge label="Proj" value={meta?.projection_trust ?? 0} color={trustColor(meta?.projection_trust)} />
                </div>
                <div className="flex-1 max-w-[140px]">
                  <TrustGauge label="Mkt" value={meta?.market_trust ?? 0} color={trustColor(meta?.market_trust)} />
                </div>
                <div className="flex-1 max-w-[140px]">
                  <TrustGauge label="Ctx" value={meta?.context_trust ?? 0} color={trustColor(meta?.context_trust)} />
                </div>
                <div className="flex-1 max-w-[140px]">
                  <TrustGauge label="Exec" value={meta?.execution_trust ?? 0} color={trustColor(meta?.execution_trust)} />
                </div>
              </div>

              {/* Reason text */}
              {meta?.reason && (
                <p className="hidden lg:block text-[10px] text-cs-muted truncate flex-1 max-w-xs">
                  {meta.reason}
                </p>
              )}

              {/* Spacer */}
              <div className="flex-1" />

              {/* Meta label */}
              <div className="hidden sm:flex items-center gap-1.5 shrink-0">
                <BrainCircuit className="w-3 h-3 text-cs-muted" />
                <span className="text-[9px] text-cs-muted font-mono uppercase tracking-wider">Agent 28</span>
              </div>

              {/* Collapse toggle */}
              <button
                onClick={() => setCollapsed(!collapsed)}
                className="shrink-0 text-cs-muted hover:text-white transition-colors md:hidden"
              >
                {collapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
              </button>
            </div>

            {/* Mobile expanded: trust gauges */}
            {!collapsed && (
              <div className="md:hidden pb-2 space-y-1">
                <TrustGauge label="Proj" value={meta?.projection_trust ?? 0} color={trustColor(meta?.projection_trust)} />
                <TrustGauge label="Mkt" value={meta?.market_trust ?? 0} color={trustColor(meta?.market_trust)} />
                <TrustGauge label="Ctx" value={meta?.context_trust ?? 0} color={trustColor(meta?.context_trust)} />
                <TrustGauge label="Exec" value={meta?.execution_trust ?? 0} color={trustColor(meta?.execution_trust)} />
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
