import React, { useState } from 'react';
import { Search, Loader2 } from 'lucide-react';

interface Props {
  onSearch: (player: string, line: number, odds: number, bankroll: number) => void;
  isProcessing: boolean;
}

export const PropSearchPanel: React.FC<Props> = ({ onSearch, isProcessing }) => {
  const [player, setPlayer] = useState('');
  const [line, setLine] = useState('');
  const [odds, setOdds] = useState('');
  const [bankroll, setBankroll] = useState('1000');

  const canSubmit = player.trim() && line && odds && bankroll && !isProcessing;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    onSearch(player.trim(), parseFloat(line), parseInt(odds, 10), parseFloat(bankroll));
  };

  return (
    <form onSubmit={handleSubmit} className="cs-card p-5 sm:p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cs-emerald/10 border border-cs-emerald/20">
            <Search className="h-4 w-4 text-cs-emerald" />
          </div>
          <div>
            <h2 className="text-sm font-bold text-gray-100 tracking-wide uppercase">
              Deploy Agents
            </h2>
            <p className="text-[10px] text-cs-muted mt-0.5">
              Enter a prop to analyze with the multi-agent pipeline
            </p>
          </div>
        </div>

        {isProcessing && (
          <div className="flex items-center gap-2">
            <Loader2 className="h-3.5 w-3.5 text-cs-emerald animate-spin" />
            <span className="text-[11px] text-cs-emerald font-medium tracking-wide uppercase">
              Processing
            </span>
          </div>
        )}
      </div>

      {/* Input Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div>
          <label htmlFor="agent-player" className="cs-label">Player Name</label>
          <input
            id="agent-player"
            placeholder="e.g. A'ja Wilson"
            className="cs-input !focus:border-cs-emerald/50 !focus:shadow-glow-emerald-sm !focus:ring-cs-emerald/20"
            value={player}
            onChange={(e) => setPlayer(e.target.value)}
            disabled={isProcessing}
          />
        </div>
        <div>
          <label htmlFor="agent-line" className="cs-label">Prop Line</label>
          <input
            id="agent-line"
            placeholder="e.g. 24.5"
            type="number"
            step="0.5"
            className="cs-input !focus:border-cs-emerald/50 !focus:shadow-glow-emerald-sm !focus:ring-cs-emerald/20"
            value={line}
            onChange={(e) => setLine(e.target.value)}
            disabled={isProcessing}
          />
        </div>
        <div>
          <label htmlFor="agent-odds" className="cs-label">Book Odds</label>
          <input
            id="agent-odds"
            placeholder="e.g. -110"
            type="number"
            className="cs-input !focus:border-cs-emerald/50 !focus:shadow-glow-emerald-sm !focus:ring-cs-emerald/20"
            value={odds}
            onChange={(e) => setOdds(e.target.value)}
            disabled={isProcessing}
          />
        </div>
        <div>
          <label htmlFor="agent-bankroll" className="cs-label">Bankroll ($)</label>
          <input
            id="agent-bankroll"
            placeholder="e.g. 1000"
            type="number"
            min="1"
            className="cs-input !focus:border-cs-emerald/50 !focus:shadow-glow-emerald-sm !focus:ring-cs-emerald/20"
            value={bankroll}
            onChange={(e) => setBankroll(e.target.value)}
            disabled={isProcessing}
          />
        </div>
      </div>

      {/* Submit */}
      <button
        type="submit"
        disabled={!canSubmit}
        className="cs-btn-emerald mt-4 w-full flex items-center justify-center gap-2 tracking-widest text-sm uppercase"
      >
        {isProcessing ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Agents Analyzing…
          </>
        ) : (
          <>
            <Search className="h-4 w-4" />
            Run Quantitative Analysis
          </>
        )}
      </button>
    </form>
  );
};
