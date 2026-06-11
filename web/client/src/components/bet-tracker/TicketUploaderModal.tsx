import { Check, X, Loader2, FileImage, Plus, Trash2 } from 'lucide-react';
import type { DraftLeg } from './types';

interface TicketUploaderModalProps {
  uploadStep: 'select' | 'processing' | 'confirm';
  uploadFile: File | null;
  submittingWager: boolean;
  confirmIsParlay: number;
  confirmOdds: string;
  setConfirmOdds: (v: string) => void;
  confirmStake: string;
  setConfirmStake: (v: string) => void;
  confirmNotes: string;
  setConfirmNotes: (v: string) => void;
  confirmPlayer: string;
  setConfirmPlayer: (v: string) => void;
  confirmStat: string;
  setConfirmStat: (v: string) => void;
  confirmLine: string;
  setConfirmLine: (v: string) => void;
  confirmOverUnder: 'OVER' | 'UNDER';
  setConfirmOverUnder: (v: 'OVER' | 'UNDER') => void;
  confirmOpponent: string;
  setConfirmOpponent: (v: string) => void;
  confirmLegs: DraftLeg[];
  setConfirmLegs: (legs: DraftLeg[]) => void;
  onSubmit: (e: React.FormEvent) => void;
  onClose: () => void;
}

export default function TicketUploaderModal({
  uploadStep,
  uploadFile,
  submittingWager,
  confirmIsParlay,
  confirmOdds,
  setConfirmOdds,
  confirmStake,
  setConfirmStake,
  confirmNotes,
  setConfirmNotes,
  confirmPlayer,
  setConfirmPlayer,
  confirmStat,
  setConfirmStat,
  confirmLine,
  setConfirmLine,
  confirmOverUnder,
  setConfirmOverUnder,
  confirmOpponent,
  setConfirmOpponent,
  confirmLegs,
  setConfirmLegs,
  onSubmit,
  onClose
}: TicketUploaderModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="cs-card w-full max-w-lg p-6 relative border-cs-red/40 animate-fade-in text-left">
        <button
          onClick={onClose}
          className="absolute right-4 top-4 text-cs-muted hover:text-white cursor-pointer"
        >
          <X className="w-5 h-5" />
        </button>

        {uploadStep === 'processing' && (
          <div className="py-8 flex flex-col items-center justify-center text-center space-y-4">
            <Loader2 className="w-12 h-12 text-cs-red animate-spin" />
            <div>
              <h3 className="text-base font-bold text-white">Analyzing Ticket Screenshot</h3>
              <p className="text-xs text-cs-muted mt-1 max-w-xs">
                Executing AI OCR parsing model on <span className="text-white font-semibold font-mono">{uploadFile?.name}</span>...
              </p>
            </div>
          </div>
        )}

        {uploadStep === 'confirm' && (
          <div>
            <h3 className="text-base font-bold text-white mb-4 flex items-center gap-2">
              <FileImage className="w-5 h-5 text-cs-red" /> Confirm OCR Bet Details
            </h3>

            <form onSubmit={onSubmit} className="space-y-4">
              <div className="bg-emerald-500/10 border border-emerald-500/20 rounded-xl p-3 flex items-start gap-2.5 mb-4">
                <Check className="w-4 h-4 text-emerald-400 shrink-0 mt-0.5" />
                <span className="text-[11px] text-emerald-400">
                  Successfully read <span className="font-semibold font-mono text-white">{uploadFile?.name}</span>. Review extracted properties and adjust if necessary.
                </span>
              </div>

              {confirmIsParlay === 1 ? (
                // Parlay Edit Form
                <div className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <label className="cs-label">Combined Odds</label>
                      <input
                        type="number"
                        value={confirmOdds}
                        onChange={(e) => setConfirmOdds(e.target.value)}
                        className="cs-input font-mono"
                        required
                      />
                    </div>
                    <div>
                      <label className="cs-label">Total Stake ($)</label>
                      <input
                        type="number"
                        step="0.01"
                        value={confirmStake}
                        onChange={(e) => setConfirmStake(e.target.value)}
                        className="cs-input font-mono"
                        required
                      />
                    </div>
                  </div>

                  <div>
                    <label className="cs-label">Legs Breakdown</label>
                    <div className="space-y-2">
                      {confirmLegs.map((leg, idx) => (
                        <div key={idx} className="bg-cs-black border border-cs-border/40 rounded-xl p-3 space-y-2 relative">
                          <button
                            type="button"
                            onClick={() => setConfirmLegs(confirmLegs.filter((_, i) => i !== idx))}
                            className="absolute right-2 top-2.5 text-cs-muted hover:text-cs-red-bright cursor-pointer"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                          <div className="grid grid-cols-3 gap-2">
                            <div>
                              <label className="text-[9px] text-cs-muted block">Player</label>
                              <input
                                type="text"
                                value={leg.player}
                                onChange={(e) => {
                                  const updated = [...confirmLegs];
                                  updated[idx].player = e.target.value;
                                  setConfirmLegs(updated);
                                }}
                                className="cs-input text-[11px] py-1 px-2"
                              />
                            </div>
                            <div>
                              <label className="text-[9px] text-cs-muted block">Stat</label>
                              <input
                                type="text"
                                value={leg.stat}
                                onChange={(e) => {
                                  const updated = [...confirmLegs];
                                  updated[idx].stat = e.target.value;
                                  setConfirmLegs(updated);
                                }}
                                className="cs-input text-[11px] py-1 px-2"
                              />
                            </div>
                            <div>
                              <label className="text-[9px] text-cs-muted block">Line</label>
                              <input
                                type="number"
                                step="0.5"
                                value={leg.line}
                                onChange={(e) => {
                                  const updated = [...confirmLegs];
                                  updated[idx].line = parseFloat(e.target.value);
                                  setConfirmLegs(updated);
                                }}
                                className="cs-input text-[11px] py-1 px-2"
                              />
                            </div>
                          </div>
                        </div>
                      ))}
                      <button
                        type="button"
                        onClick={() => setConfirmLegs([...confirmLegs, { player: '', stat: 'PTS', line: 15.5, over_under: 'OVER', book_odds: -110, opposing_team: '' }])}
                        className="flex items-center gap-1 text-[10px] text-cs-red hover:text-cs-red-bright font-bold"
                      >
                        <Plus className="w-3.5 h-3.5" /> Add Custom Leg
                      </button>
                    </div>
                  </div>
                </div>
              ) : (
                // Straight Bet Edit Form
                <div className="space-y-4">
                  <div>
                    <label className="cs-label">Player Name</label>
                    <input
                      type="text"
                      value={confirmPlayer}
                      onChange={(e) => setConfirmPlayer(e.target.value)}
                      className="cs-input"
                      required
                    />
                  </div>

                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="cs-label">Stat</label>
                      <input
                        type="text"
                        value={confirmStat}
                        onChange={(e) => setConfirmStat(e.target.value)}
                        className="cs-input"
                        required
                      />
                    </div>
                    <div>
                      <label className="cs-label">Line</label>
                      <input
                        type="number"
                        step="0.5"
                        value={confirmLine}
                        onChange={(e) => setConfirmLine(e.target.value)}
                        className="cs-input font-mono"
                        required
                      />
                    </div>
                    <div>
                      <label className="cs-label">Side</label>
                      <select
                        value={confirmOverUnder}
                        onChange={(e) => setConfirmOverUnder(e.target.value as 'OVER' | 'UNDER')}
                        className="cs-input bg-cs-black"
                      >
                        <option value="OVER">OVER</option>
                        <option value="UNDER">UNDER</option>
                      </select>
                    </div>
                  </div>

                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="cs-label">Book Odds</label>
                      <input
                        type="number"
                        value={confirmOdds}
                        onChange={(e) => setConfirmOdds(e.target.value)}
                        className="cs-input font-mono"
                        required
                      />
                    </div>
                    <div>
                      <label className="cs-label">Stake ($)</label>
                      <input
                        type="number"
                        step="0.01"
                        value={confirmStake}
                        onChange={(e) => setConfirmStake(e.target.value)}
                        className="cs-input font-mono"
                        required
                      />
                    </div>
                    <div>
                      <label className="cs-label">Opposing Team</label>
                      <input
                        type="text"
                        value={confirmOpponent}
                        onChange={(e) => setConfirmOpponent(e.target.value)}
                        className="cs-input"
                      />
                    </div>
                  </div>
                </div>
              )}

              <div>
                <label className="cs-label">Capture Notes</label>
                <input
                  type="text"
                  value={confirmNotes}
                  onChange={(e) => setConfirmNotes(e.target.value)}
                  className="cs-input text-xs"
                />
              </div>

              <div className="grid grid-cols-2 gap-3 pt-3">
                <button
                  type="button"
                  onClick={onClose}
                  className="py-2.5 rounded-xl border border-cs-border hover:bg-cs-dark/30 text-xs font-bold text-center text-cs-muted hover:text-white transition-all cursor-pointer"
                >
                  Discard Ticket
                </button>
                <button
                  type="submit"
                  disabled={submittingWager}
                  className="py-2.5 cs-btn-primary text-xs font-bold text-center cursor-pointer flex items-center justify-center gap-1.5"
                >
                  {submittingWager && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  Confirm & Log Wager
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
