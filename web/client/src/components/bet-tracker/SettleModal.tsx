import { Check, X } from 'lucide-react';
import type { Bet } from './types';
import { formatOdds } from './types';

interface SettleModalProps {
  settlingBet: Bet;
  settleResult: 'WIN' | 'LOSS' | 'PUSH';
  setSettleResult: (result: 'WIN' | 'LOSS' | 'PUSH') => void;
  actualValue: string;
  setActualValue: (value: string) => void;
  onSubmit: (e: React.FormEvent) => void;
  onClose: () => void;
}

export default function SettleModal({
  settlingBet,
  settleResult,
  setSettleResult,
  actualValue,
  setActualValue,
  onSubmit,
  onClose
}: SettleModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="cs-card w-full max-w-sm p-6 relative border-cs-red/40 animate-fade-in text-left">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-cs-muted hover:text-white cursor-pointer"
        >
          <X className="w-5 h-5" />
        </button>

        <h3 className="text-base font-bold text-white mb-4 flex items-center gap-2">
          <Check className="w-5 h-5 text-emerald-400" /> Settle Pending Bet
        </h3>

        <div className="mb-4 text-xs font-medium space-y-1 bg-cs-black/60 p-3 rounded-lg border border-cs-border/30">
          <div className="text-cs-muted">BET DETAIL:</div>
          <div className="text-white font-bold">
            {settlingBet.is_parlay === 1 ? 'Multi-Leg Parlay' : settlingBet.player}
          </div>
          {settlingBet.is_parlay !== 1 && (
            <div className="text-white">{settlingBet.over_under} {settlingBet.line} {settlingBet.stat}</div>
          )}
          <div className="text-cs-muted font-mono">Stake: ${settlingBet.stake.toFixed(2)} @ {formatOdds(settlingBet.book_odds)}</div>
        </div>

        <form onSubmit={onSubmit} className="space-y-4">
          <div>
            <label className="cs-label">Outcome Result</label>
            <select
              value={settleResult}
              onChange={(e) => setSettleResult(e.target.value as 'WIN' | 'LOSS' | 'PUSH')}
              className="cs-input bg-cs-black"
            >
              <option value="WIN">WIN</option>
              <option value="LOSS">LOSS</option>
              <option value="PUSH">PUSH</option>
            </select>
          </div>

          {settlingBet.is_parlay !== 1 && (
            <div>
              <label className="cs-label">Actual Stat Value</label>
              <input
                type="number"
                step="0.5"
                placeholder="e.g. 24"
                value={actualValue}
                onChange={(e) => setActualValue(e.target.value)}
                className="cs-input font-mono"
              />
            </div>
          )}

          <div className="grid grid-cols-2 gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="py-2.5 rounded-xl border border-cs-border hover:bg-cs-dark/30 text-xs font-bold text-center text-cs-muted hover:text-white transition-all cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="py-2.5 cs-btn-primary text-xs font-bold text-center cursor-pointer"
            >
              Confirm Settlement
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
