import { X, Sparkles, Loader2 } from 'lucide-react';
import type { GeneratedParlay } from './types';
import { formatOdds } from './types';

interface ParlayGeneratorModalProps {
  generating: boolean;
  generatedParlay: GeneratedParlay | null;
  generatorStake: string;
  setGeneratorStake: (v: string) => void;
  submittingWager: boolean;
  onLogParlay: () => void;
  onClose: () => void;
}

export default function ParlayGeneratorModal({
  generating,
  generatedParlay,
  generatorStake,
  setGeneratorStake,
  submittingWager,
  onLogParlay,
  onClose
}: ParlayGeneratorModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="cs-card w-full max-w-lg p-6 relative border-cs-red/40 animate-fade-in text-left">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-cs-muted hover:text-white cursor-pointer"
        >
          <X className="w-5 h-5" />
        </button>

        {generating && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <Loader2 className="w-12 h-12 text-cs-red animate-spin" />
            <div>
              <h3 className="text-base font-bold text-white">Aggregating WNBA Projection Divergences</h3>
              <p className="text-xs text-cs-muted mt-1 max-w-xs">
                Agent 13 running correlation matrix checks and building high-EV parlay via Nemotron Matchup engine...
              </p>
            </div>
          </div>
        )}

        {!generating && generatedParlay && (
          <div>
            <h3 className="text-base font-bold text-white mb-4 flex items-center gap-2">
              <Sparkles className="w-5 h-5 text-cs-red" /> Agent 13 Matchup Oracle Synthesis
            </h3>

            <div className="space-y-4">
              {/* Legs display */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {generatedParlay.legs.map((leg, idx) => (
                  <div key={idx} className="bg-cs-black border border-cs-border/45 rounded-2xl p-4 space-y-2 hover:border-cs-red/40 transition-colors">
                    <div className="flex justify-between items-start">
                      <div>
                        <span className="text-[10px] text-cs-muted uppercase tracking-wider block">Leg {idx + 1}</span>
                        <span className="text-white font-bold text-sm">{leg.player}</span>
                        <span className="text-[10px] text-cs-muted block">vs {leg.opposing_team}</span>
                      </div>
                      <span className="text-xs font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        EV +{leg.edge_pct}%
                      </span>
                    </div>
                    <div className="flex items-center justify-between pt-1 border-t border-cs-border/20">
                      <span className="text-[10px] text-cs-muted">Market</span>
                      <span className="text-white font-mono text-xs font-bold">
                        {leg.over_under} {leg.line} {leg.stat}
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] text-cs-muted">Odds</span>
                      <span className="text-white font-mono text-xs font-bold">
                        {formatOdds(leg.book_odds)}
                      </span>
                    </div>
                  </div>
                ))}
              </div>

              {/* Parlay Summary */}
              <div className="bg-cs-dark/45 border border-cs-border/50 rounded-2xl p-4 space-y-2">
                <div className="text-[10px] text-cs-red uppercase tracking-widest font-black flex items-center gap-1.5">
                  <Cpu className="w-3.5 h-3.5 text-cs-red shrink-0" />
                  Nemotron Qualitative Breakdown
                </div>
                <p className="text-[11px] text-white leading-relaxed italic">
                  "{generatedParlay.summary}"
                </p>
              </div>

              {/* Combined metrics and stake input */}
              <div className="grid grid-cols-2 gap-4 items-center bg-cs-black/60 border border-cs-border/30 rounded-2xl p-4">
                <div>
                  <span className="text-[10px] text-cs-muted block">COMBINED ODDS</span>
                  <span className="text-2xl font-mono text-white font-black">
                    {formatOdds(generatedParlay.parlay_odds)}
                  </span>
                </div>
                <div>
                  <label className="cs-label !mb-1">Stake Amount ($)</label>
                  <input
                    type="number"
                    value={generatorStake}
                    onChange={(e) => setGeneratorStake(e.target.value)}
                    className="cs-input font-mono !py-1.5"
                    required
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3 pt-3">
                <button
                  type="button"
                  onClick={onClose}
                  className="py-2.5 rounded-xl border border-cs-border hover:bg-cs-dark/30 text-xs font-bold text-center text-cs-muted hover:text-white transition-all cursor-pointer"
                >
                  Discard Parlay
                </button>
                <button
                  onClick={onLogParlay}
                  disabled={submittingWager}
                  className="py-2.5 cs-btn-primary text-xs font-bold text-center cursor-pointer flex items-center justify-center gap-1.5"
                >
                  {submittingWager && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  Log Parlay Wager
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// Small mock Cpu icon helper since it wasn't imported from lucide
function Cpu(props: any) {
  return (
    <svg
      {...props}
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect width="16" height="16" x="4" y="4" rx="2" />
      <rect width="6" height="6" x="9" y="9" rx="1" />
      <path d="M9 1v3" />
      <path d="M15 1v3" />
      <path d="M9 20v3" />
      <path d="M15 20v3" />
      <path d="M20 9h3" />
      <path d="M20 15h3" />
      <path d="M1 9h3" />
      <path d="M1 15h3" />
    </svg>
  );
}
