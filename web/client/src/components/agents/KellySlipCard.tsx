import React from 'react';
import { motion } from 'framer-motion';
import { TrendingUp, ShieldAlert, DollarSign, Percent, BarChart3 } from 'lucide-react';
import type { AgentResult } from '../../types/agent';

interface Props {
  result: AgentResult;
}

export const KellySlipCard: React.FC<Props> = ({ result }) => {
  const isBet = result.action === 'BET';

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
      className={`cs-card !border-2 ${
        isBet
          ? '!border-cs-emerald/40 !bg-cs-emerald/[0.03] shadow-glow-emerald'
          : '!border-red-500/40 !bg-red-500/[0.03] shadow-glow-red'
      }`}
    >
      {/* Header */}
      <div className="flex items-center justify-between p-5 pb-0">
        <div className="flex items-center gap-3">
          {isBet ? (
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-cs-emerald/10 border border-cs-emerald/20">
              <TrendingUp className="h-4.5 w-4.5 text-cs-emerald" />
            </div>
          ) : (
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-red-500/10 border border-red-500/20">
              <ShieldAlert className="h-4.5 w-4.5 text-red-400" />
            </div>
          )}
          <div>
            <h3 className="text-sm font-bold text-white uppercase tracking-widest">
              Risk Management Directive
            </h3>
            <p className="text-[10px] text-cs-muted mt-0.5">
              {result.player} — Line: {result.line}, Odds: {result.odds > 0 ? '+' : ''}{result.odds}
            </p>
          </div>
        </div>

        <span
          className={`px-3 py-1.5 rounded-lg text-xs font-black tracking-widest uppercase ${
            isBet
              ? 'bg-cs-emerald/15 text-cs-emerald border border-cs-emerald/30'
              : 'bg-red-500/15 text-red-400 border border-red-500/30'
          }`}
        >
          {result.action}
        </span>
      </div>

      {/* Body */}
      <div className="p-5">
        {isBet ? (
          <div className="space-y-3">
            {/* Metrics Grid */}
            <div className="grid grid-cols-3 gap-3">
              <div className="bg-cs-dark/60 rounded-xl p-3 border border-cs-border/30 text-center">
                <div className="flex items-center justify-center gap-1.5 mb-1.5">
                  <Percent className="h-3 w-3 text-cs-emerald" />
                  <span className="cs-stat-label">Kelly Fraction</span>
                </div>
                <span className="text-xl font-mono font-bold text-cs-emerald">
                  {(result.fraction * 100).toFixed(2)}%
                </span>
              </div>
              <div className="bg-cs-dark/60 rounded-xl p-3 border border-cs-border/30 text-center">
                <div className="flex items-center justify-center gap-1.5 mb-1.5">
                  <BarChart3 className="h-3 w-3 text-cs-emerald" />
                  <span className="cs-stat-label">Expected Value</span>
                </div>
                <span className="text-xl font-mono font-bold text-cs-emerald">
                  +{result.expected_value_pct.toFixed(2)}%
                </span>
              </div>
              <div className="bg-cs-dark/60 rounded-xl p-3 border border-cs-border/30 text-center">
                <div className="flex items-center justify-center gap-1.5 mb-1.5">
                  <ShieldAlert className="h-3 w-3 text-cs-amber" />
                  <span className="cs-stat-label">Confidence</span>
                </div>
                <span className="text-xl font-mono font-bold text-cs-amber">
                  {(result.confidence * 100).toFixed(0)}%
                </span>
              </div>
            </div>

            {/* Wager Allocation */}
            <div className="mt-1 p-4 bg-cs-dark rounded-xl flex items-center justify-between border border-cs-emerald/20">
              <div className="flex items-center gap-2.5">
                <DollarSign className="h-5 w-5 text-cs-emerald" />
                <span className="text-xs font-bold text-cs-muted uppercase tracking-widest">
                  Suggested Allocation
                </span>
              </div>
              <span className="text-3xl font-mono font-bold text-white tracking-tight">
                ${result.wager_amount.toFixed(2)}
              </span>
            </div>

            {/* Reasoning */}
            {result.reason && (
              <p className="text-xs text-cs-muted leading-relaxed px-1">
                <span className="text-cs-emerald font-semibold">Rationale:</span>{' '}
                {result.reason}
              </p>
            )}
          </div>
        ) : (
          /* PASS state */
          <div className="space-y-3">
            <div className="p-4 bg-red-500/5 rounded-xl border border-red-500/15">
              <p className="text-sm text-red-400 leading-relaxed">
                {result.reason}
              </p>
            </div>
            <p className="text-[10px] text-cs-muted uppercase tracking-widest text-center">
              Negative expected value — no allocation recommended
            </p>
          </div>
        )}
      </div>
    </motion.div>
  );
};
