import { Check, X, Clock, ChevronDown, ChevronUp } from 'lucide-react';
import type { Bet, BetStats } from './types';
import { formatOdds, formatDate } from './types';

interface BetsTableProps {
  stats: BetStats | null;
  rootBets: Bet[];
  getLegs: (parentId: number) => Bet[];
  getHedges: (parentId: number) => Bet[];
  expandedParlays: Record<number, boolean>;
  expandedHedges: Record<number, boolean>;
  toggleExpandParlay: (id: number) => void;
  toggleExpandHedges: (id: number) => void;
  onSettle: (bet: Bet) => void;
}

export default function BetsTable({
  stats,
  rootBets,
  getLegs,
  getHedges,
  expandedParlays,
  expandedHedges,
  toggleExpandParlay,
  toggleExpandHedges,
  onSettle
}: BetsTableProps) {
  return (
    <div className="cs-card p-0 overflow-hidden w-full">
      <div className="px-6 py-4 border-b border-cs-border/40 flex flex-col sm:flex-row justify-between sm:items-center gap-2">
        <h2 className="text-sm font-semibold tracking-wider uppercase text-white">Wager Ledger</h2>
        <div className="flex items-center gap-4 text-xs font-mono">
          <span className="text-emerald-400">Wins: {stats?.wins || 0}</span>
          <span className="text-cs-red-bright">Losses: {stats?.losses || 0}</span>
          <span className="text-amber-500">Pushes: {stats?.pushes || 0}</span>
          <span className="text-cs-muted">Pending: {stats?.pending || 0}</span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs font-medium">
          <thead>
            <tr className="border-b border-cs-border/30 bg-cs-black/40 text-cs-muted uppercase tracking-wider text-[10px]">
              <th className="px-5 py-3 w-[10%]">Date</th>
              <th className="px-5 py-3 w-[25%]">Bet Type / Player</th>
              <th className="px-5 py-3 w-[25%]">Market / Legs</th>
              <th className="px-5 py-3 text-center w-[10%]">Odds</th>
              <th className="px-5 py-3 text-right w-[10%]">Stake</th>
              <th className="px-5 py-3 text-center w-[10%]">Result</th>
              <th className="px-5 py-3 text-right w-[10%]">P&L</th>
              <th className="px-5 py-3 text-center w-[10%]">Action</th>
            </tr>
          </thead>
          {rootBets.length === 0 ? (
            <tbody className="divide-y divide-cs-border/20">
              <tr>
                <td colSpan={8} className="px-6 py-12 text-center text-cs-muted font-mono">
                  No wagers tracked in database. Upload a screenshot to begin.
                </td>
              </tr>
            </tbody>
          ) : (
            rootBets.map((bet) => {
                const isParlay = bet.is_parlay === 1;
                const legs = isParlay ? getLegs(bet.id) : [];
                const isExpanded = expandedParlays[bet.id] || false;

                const hedges = getHedges(bet.id);
                const hasHedges = hedges.length > 0;
                const isHedgesExpanded = expandedHedges[bet.id] || false;

                const pl = bet.profit_loss;
                const isPlPositive = pl !== null && pl > 0;

                return (
                  <tbody key={bet.id} className="border-none">
                    {/* Main Wager Row */}
                    <tr className={`hover:bg-cs-dark/20 transition-colors duration-150 ${isParlay ? 'bg-cs-dark/5 font-semibold' : 'odd:bg-cs-dark/10'}`}>
                      <td className="px-5 py-3.5 font-mono text-cs-muted">{formatDate(bet.placed_at)}</td>

                      <td className="px-5 py-3.5">
                        {isParlay ? (
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => toggleExpandParlay(bet.id)}
                              className="p-1 hover:bg-cs-dark rounded transition-colors text-cs-red cursor-pointer"
                            >
                              {isExpanded ? <ChevronUp className="w-4.5 h-4.5" /> : <ChevronDown className="w-4.5 h-4.5" />}
                            </button>
                            {hasHedges && (
                              <button
                                type="button"
                                onClick={() => toggleExpandHedges(bet.id)}
                                className="p-1 hover:bg-cs-dark rounded transition-colors text-emerald-400 cursor-pointer"
                                title="Toggle Hedges"
                              >
                                {isHedgesExpanded ? <ChevronUp className="w-4.5 h-4.5" /> : <ChevronDown className="w-4.5 h-4.5" />}
                              </button>
                            )}
                            <div>
                              <div className="text-white font-bold flex items-center gap-1.5">
                                Multi-Leg Parlay
                                <span className="text-[9px] bg-cs-red/20 text-cs-red-bright px-1.5 py-0.2 rounded font-mono">
                                  {legs.length} Legs
                                </span>
                                {hasHedges && (
                                  <span className="text-[9px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.2 rounded font-mono font-bold">
                                    Hedged
                                  </span>
                                )}
                              </div>
                              <div className="text-[10px] text-cs-muted font-normal max-w-xs truncate">
                                {bet.notes || 'Aggregated EV Parlay'}
                              </div>
                            </div>
                          </div>
                        ) : (
                          <div className="flex items-center gap-2">
                            {hasHedges && (
                              <button
                                type="button"
                                onClick={() => toggleExpandHedges(bet.id)}
                                className="p-1 hover:bg-cs-dark rounded transition-colors text-emerald-400 cursor-pointer"
                                title="Toggle Hedges"
                              >
                                {isHedgesExpanded ? <ChevronUp className="w-4.5 h-4.5" /> : <ChevronDown className="w-4.5 h-4.5" />}
                              </button>
                            )}
                            <div>
                              <div className="text-white font-semibold flex items-center gap-1.5">
                                {bet.player}
                                {hasHedges && (
                                  <span className="text-[9px] bg-emerald-500/20 text-emerald-400 px-1.5 py-0.2 rounded font-mono font-bold">
                                    Hedged
                                  </span>
                                )}
                              </div>
                              {bet.opposing_team && <div className="text-[10px] text-cs-muted font-normal">vs {bet.opposing_team}</div>}
                            </div>
                          </div>
                        )}
                      </td>

                      <td className="px-5 py-3.5">
                        {isParlay ? (
                          <span className="text-cs-muted text-[11px] font-mono italic">
                            {legs.map(l => l.player).join(' + ')}
                          </span>
                        ) : (
                          <div className="flex items-center gap-1.5">
                            <span className={`text-[10px] px-1.5 py-0.5 rounded font-black ${bet.over_under === 'OVER' ? 'bg-cs-red/20 text-cs-red-bright' : 'bg-cs-muted/20 text-cs-muted'}`}>
                              {bet.over_under}
                            </span>
                            <span className="text-white font-mono">{bet.line} {bet.stat}</span>
                          </div>
                        )}
                      </td>

                      <td className="px-5 py-3.5 text-center font-mono">{formatOdds(bet.book_odds)}</td>
                      <td className="px-5 py-3.5 text-right font-mono text-white">${bet.stake.toFixed(2)}</td>

                      <td className="px-5 py-3.5 text-center">
                        {bet.result === 'WIN' && (
                          <span className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            <Check className="w-2.5 h-2.5" /> WIN
                          </span>
                        )}
                        {bet.result === 'LOSS' && (
                          <span className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-cs-red/10 text-cs-red-bright border border-cs-red/20">
                            <X className="w-2.5 h-2.5" /> LOSS
                          </span>
                        )}
                        {bet.result === 'PUSH' && (
                          <span className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/10 text-amber-400 border border-amber-500/20">
                            PUSH
                          </span>
                        )}
                        {bet.result === null && (
                          <span className="inline-flex items-center gap-0.5 px-2 py-0.5 rounded-full text-[10px] font-bold bg-cs-dark text-cs-muted border border-cs-border/30">
                            <Clock className="w-2.5 h-2.5" /> PENDING
                          </span>
                        )}
                      </td>

                      <td className={`px-5 py-3.5 text-right font-mono ${isPlPositive ? 'text-emerald-400' : (pl !== null && pl < 0 ? 'text-cs-red-bright' : 'text-cs-muted')}`}>
                        {pl !== null ? (isPlPositive ? `+$${pl.toFixed(2)}` : pl < 0 ? `-$${Math.abs(pl).toFixed(2)}` : '$0.00') : '—'}
                      </td>

                      <td className="px-5 py-3.5 text-center">
                        {bet.result === null ? (
                          <button
                            onClick={() => onSettle(bet)}
                            className="px-2.5 py-1 rounded bg-cs-red hover:bg-cs-red-bright hover:shadow-glow-red-sm text-white text-[10px] font-bold transition-all cursor-pointer"
                          >
                            Settle
                          </button>
                        ) : (
                          <span className="text-[10px] text-cs-muted font-mono">{bet.settled_at ? formatDate(bet.settled_at) : '—'}</span>
                        )}
                      </td>
                    </tr>

                    {/* Collapsible Parlay Sub-table */}
                    {isParlay && isExpanded && (
                      <tr className="bg-cs-black/60 border-b border-cs-border/20">
                        <td colSpan={8} className="px-6 py-4">
                          <div className="pl-6 border-l-2 border-cs-red space-y-3">
                            <div className="text-[10px] font-mono text-cs-muted uppercase tracking-wider">Parlay Leg Breakdown</div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                              {legs.map((leg, idx) => (
                                <div key={leg.id || idx} className="bg-cs-dark/30 border border-cs-border/30 rounded-xl p-3 flex justify-between items-center hover:border-cs-border/60 transition-colors">
                                  <div>
                                    <div className="font-semibold text-white text-xs">{leg.player}</div>
                                    <div className="text-[10px] text-cs-muted">vs {leg.opposing_team || 'OPP'}</div>
                                    <div className="flex items-center gap-1.5 mt-2">
                                      <span className={`text-[9px] px-1.5 py-0.2 rounded font-black ${leg.over_under === 'OVER' ? 'bg-cs-red/20 text-cs-red-bright' : 'bg-cs-muted/20 text-cs-muted'}`}>
                                        {leg.over_under}
                                      </span>
                                      <span className="text-white font-mono text-xs">{leg.line} {leg.stat}</span>
                                    </div>
                                  </div>
                                  <div className="text-right">
                                    <div className="text-xs font-mono text-white">{formatOdds(leg.book_odds)}</div>
                                    {leg.edge_pct && (
                                      <div className="text-[10px] text-emerald-400 font-mono">Edge: +{leg.edge_pct.toFixed(1)}%</div>
                                    )}
                                    {leg.result && (
                                      <span className={`inline-block mt-1.5 text-[9px] font-bold px-1.5 py-0.2 rounded ${leg.result === 'WIN' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-cs-red/10 text-cs-red-bright'}`}>
                                        {leg.result}
                                      </span>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}

                    {/* Collapsible Hedges Sub-table */}
                    {hasHedges && isHedgesExpanded && (
                      <tr className="bg-cs-black/60 border-b border-cs-border/20">
                        <td colSpan={8} className="px-6 py-4">
                          <div className="pl-6 border-l-2 border-emerald-500 space-y-3">
                            <div className="text-[10px] font-mono text-emerald-400 uppercase tracking-wider font-bold">Automated Hedges (Agent 20)</div>
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                              {hedges.map((hedge, idx) => (
                                <div key={hedge.id || idx} className="bg-cs-dark/30 border border-emerald-500/20 rounded-xl p-3 flex justify-between items-center hover:border-emerald-500/40 transition-colors">
                                  <div>
                                    <div className="font-semibold text-white text-xs">{hedge.player}</div>
                                    {hedge.opposing_team && <div className="text-[10px] text-cs-muted">vs {hedge.opposing_team}</div>}
                                    <div className="flex items-center gap-1.5 mt-2">
                                      <span className={`text-[9px] px-1.5 py-0.2 rounded font-black ${hedge.over_under === 'OVER' ? 'bg-cs-red/20 text-cs-red-bright' : 'bg-cs-muted/20 text-cs-muted'}`}>
                                        {hedge.over_under}
                                      </span>
                                      <span className="text-white font-mono text-xs">{hedge.line} {hedge.stat}</span>
                                    </div>
                                    {hedge.notes && <div className="text-[9px] text-cs-muted mt-1 font-mono">{hedge.notes}</div>}
                                  </div>
                                  <div className="text-right">
                                    <div className="text-xs font-mono text-white">{formatOdds(hedge.book_odds)}</div>
                                    <div className="text-xs text-white font-bold font-mono mt-1">Stake: ${hedge.stake.toFixed(2)}</div>
                                    {hedge.result && (
                                      <span className={`inline-block mt-1.5 text-[9px] font-bold px-1.5 py-0.2 rounded ${hedge.result === 'WIN' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-cs-red/10 text-cs-red-bright'}`}>
                                        {hedge.result}
                                      </span>
                                    )}
                                  </div>
                                </div>
                              ))}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </tbody>
                );
              })
            )}
        </table>
      </div>
    </div>
  );
}
