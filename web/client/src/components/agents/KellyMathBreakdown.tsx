import React from 'react';
import type { AgentResult } from '../../types/agent';
import { Calculator } from 'lucide-react';

interface Props {
  result: AgentResult;
}

export const KellyMathBreakdown: React.FC<Props> = ({ result }) => {
  // Try to parse the reasoning string from the risk manager. 
  // It looks like: "Adjusted prob 0.47 against odds -110. News impact: 0.15"
  // If we can't parse it reliably, we'll fall back to standard display.
  
  const reasoning = result.reason || '';
  let baseProb = result.confidence * 100;
  let impactScore = 0;
  let finalProb = result.confidence * 100;
  
  const impactMatch = reasoning.match(/News impact: (0\.\d+)/);
  if (impactMatch) {
    impactScore = parseFloat(impactMatch[1]) * 100;
    // Base prob was final + impact
    baseProb = finalProb + impactScore;
  }

  return (
    <div className="cs-card p-4 bg-gradient-to-br from-cs-dark to-[#052e16]/30 border-cs-emerald/20">
      <div className="flex items-center gap-2 mb-4">
        <Calculator className="h-4 w-4 text-cs-emerald" />
        <span className="text-xs font-semibold text-white/70 uppercase tracking-wider">
          Kelly Math Breakdown
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Step 1: Base Quant */}
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-cs-black/40 border border-cs-border/30">
          <span className="text-[10px] text-cs-muted font-mono uppercase">1. Quant Baseline</span>
          <span className="text-xl font-bold text-blue-400">{baseProb.toFixed(1)}%</span>
          <span className="text-[9px] text-cs-muted">Raw Win Probability</span>
        </div>

        {/* Step 2: Sentinel Impact */}
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-cs-black/40 border border-cs-border/30 relative">
          <div className="absolute -left-3 top-1/2 -translate-y-1/2 hidden md:flex items-center justify-center h-5 w-5 rounded-full bg-cs-dark border border-cs-border text-cs-muted text-xs font-bold">
            -
          </div>
          <span className="text-[10px] text-cs-muted font-mono uppercase">2. Sentiment Penalty</span>
          <span className="text-xl font-bold text-amber-400">{impactScore.toFixed(1)}%</span>
          <span className="text-[9px] text-cs-muted">Injury/Roster Override</span>
        </div>

        {/* Step 3: Final Edge */}
        <div className="flex flex-col gap-1 p-3 rounded-lg bg-emerald-500/10 border border-emerald-500/30 relative">
          <div className="absolute -left-3 top-1/2 -translate-y-1/2 hidden md:flex items-center justify-center h-5 w-5 rounded-full bg-cs-dark border border-cs-border text-cs-muted text-xs font-bold">
            =
          </div>
          <span className="text-[10px] text-emerald-400 font-mono uppercase">3. Final Edge</span>
          <span className="text-xl font-bold text-emerald-300">{finalProb.toFixed(1)}%</span>
          <span className="text-[9px] text-emerald-400/60">Calculated into Kelly %</span>
        </div>
      </div>
      
      {/* Exact Formula output */}
      <div className="mt-4 pt-4 border-t border-cs-emerald/10 flex items-center justify-between">
        <span className="text-[10px] text-cs-muted font-mono">Formula: ((bp - q) / b) * 0.25</span>
        <span className="text-xs font-mono font-bold text-white">
          Wager Size: <span className="text-cs-emerald-bright bg-cs-emerald/10 px-2 py-0.5 rounded">{(result.fraction * 100).toFixed(2)}%</span>
        </span>
      </div>
    </div>
  );
};
