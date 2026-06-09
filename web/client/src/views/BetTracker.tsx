import { useState, useEffect } from 'react';
import { Receipt, Plus, Check, X, Clock } from 'lucide-react';
import { Skeleton, SkeletonCard, SkeletonTable } from '../components/Skeleton';
import { useToast } from '../components/ToastProvider';

interface Bet {
  id: number;
  player: string;
  stat: string;
  line: number;
  over_under: 'OVER' | 'UNDER';
  book_odds: number;
  true_odds: number | null;
  edge_pct: number | null;
  stake: number;
  result: 'WIN' | 'LOSS' | 'PUSH' | null;
  actual_value: number | null;
  profit_loss: number | null;
  placed_at: number;
  settled_at: number | null;
  opposing_team: string | null;
  notes: string | null;
}

interface BetStats {
  total_bets: number;
  wins: number;
  losses: number;
  pushes: number;
  pending: number;
  total_profit: number;
  win_rate: number;
  avg_edge: number;
  avg_clv?: number;
}

export default function BetTracker() {
  const { toast } = useToast();
  const [bets, setBets] = useState<Bet[]>([]);
  const [stats, setStats] = useState<BetStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  // Form states
  const [player, setPlayer] = useState('');
  const [stat, setStat] = useState('Points');
  const [line, setLine] = useState('');
  const [overUnder, setOverUnder] = useState<'OVER' | 'UNDER'>('OVER');
  const [bookOdds, setBookOdds] = useState('-110');
  const [trueOdds, setTrueOdds] = useState('');
  const [edgePct, setEdgePct] = useState('');
  const [stake, setStake] = useState('');
  const [opposingTeam, setOpposingTeam] = useState('');
  const [notes, setNotes] = useState('');

  // Settle modal states
  const [settlingBet, setSettlingBet] = useState<Bet | null>(null);
  const [settleResult, setSettleResult] = useState<'WIN' | 'LOSS' | 'PUSH'>('WIN');
  const [actualValue, setActualValue] = useState('');

  const API_BASE = 'http://localhost:3000/api';

  const fetchData = async () => {
    try {
      const [betsRes, statsRes] = await Promise.all([
        fetch(`${API_BASE}/bets`),
        fetch(`${API_BASE}/bets/stats`)
      ]);

      if (!betsRes.ok || !statsRes.ok) throw new Error('API request failed');

      const betsData = await betsRes.json();
      const statsData = await statsRes.json();

      setBets(betsData);
      setStats(statsData);
    } catch (err) {
      console.error('Failed to load bet tracker data:', err);
      toast({
        title: 'Error Fetching Data',
        description: 'Could not connect to the backend server. Using offline state.',
        variant: 'danger'
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const handlePlaceBet = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!player || !line || !stake) {
      toast({
        title: 'Missing Fields',
        description: 'Please fill out Player, Line, and Stake fields.',
        variant: 'warning'
      });
      return;
    }

    setSubmitting(true);
    try {
      const body = {
        player,
        stat,
        line: parseFloat(line),
        over_under: overUnder,
        book_odds: parseInt(bookOdds, 10),
        true_odds: trueOdds ? parseFloat(trueOdds) : null,
        edge_pct: edgePct ? parseFloat(edgePct) : null,
        stake: parseFloat(stake),
        opposing_team: opposingTeam || null,
        notes: notes || null
      };

      const res = await fetch(`${API_BASE}/bets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });

      if (!res.ok) throw new Error('Failed to create bet');

      toast({
        title: 'Bet Placed',
        description: `Successfully tracked bet on ${player} ${stat}.`,
        variant: 'success'
      });

      // Reset form
      setPlayer('');
      setLine('');
      setBookOdds('-110');
      setTrueOdds('');
      setEdgePct('');
      setStake('');
      setOpposingTeam('');
      setNotes('');

      // Refresh list & stats
      fetchData();
    } catch (err) {
      toast({
        title: 'Submission Failed',
        description: 'Unable to save the bet to database.',
        variant: 'danger'
      });
    } finally {
      setSubmitting(false);
    }
  };

  const handleSettleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!settlingBet) return;

    try {
      const res = await fetch(`${API_BASE}/bets/${settlingBet.id}/settle`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          result: settleResult,
          actual_value: actualValue ? parseFloat(actualValue) : null
        })
      });

      if (!res.ok) throw new Error('Settle request failed');

      toast({
        title: 'Bet Settled',
        description: `Bet on ${settlingBet.player} has been settled as a ${settleResult}.`,
        variant: 'success'
      });

      setSettlingBet(null);
      setActualValue('');
      setSettleResult('WIN');
      fetchData();
    } catch (err) {
      toast({
        title: 'Settle Failed',
        description: 'Failed to update bet status.',
        variant: 'danger'
      });
    }
  };

  const formatOdds = (odds: number) => {
    return odds > 0 ? `+${odds}` : odds.toString();
  };

  const formatDate = (timestamp: number) => {
    return new Date(timestamp).toLocaleDateString('en-US', {
      month: '2-digit',
      day: '2-digit',
      year: '2-digit'
    });
  };

  if (loading) {
    return (
      <div className="p-8 space-y-6 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
        <div className="flex items-center gap-3">
          <Receipt className="w-7 h-7 text-cs-red" />
          <h1 className="text-3xl font-extrabold text-white">Bet Tracker</h1>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-5">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 space-y-6">
            <SkeletonTable />
          </div>
          <div>
            <SkeletonCard className="h-[500px]" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8 space-y-6 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-extrabold text-white tracking-tight flex items-center gap-3">
          <Receipt className="w-7 h-7 text-cs-red drop-shadow-[0_0_8px_rgba(239,68,68,0.6)]" />
          Bet Tracker Terminal
        </h1>
        <span className="text-xs text-cs-muted font-mono tracking-widest uppercase">
          Bets Archive &bull; {bets.length} Recorded
        </span>
      </div>

      {/* KPI Stats Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-5">
        {/* Total Bets */}
        <div className="cs-card p-6 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '0ms' }}>
          <p className="cs-stat-label">Total Bets Placed</p>
          <span className="cs-stat text-4xl mt-2 block">{stats?.total_bets || 0}</span>
        </div>

        {/* Win Rate */}
        <div className="cs-card p-6 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '100ms' }}>
          <p className="cs-stat-label">Win Rate</p>
          <span className="cs-stat text-4xl mt-2 block text-gradient-red">
            {stats?.win_rate ? `${stats.win_rate.toFixed(1)}%` : '0.0%'}
          </span>
        </div>

        {/* Net Profit */}
        <div className="cs-card p-6 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '200ms' }}>
          <p className="cs-stat-label">Net P&L</p>
          <span className={`cs-stat text-4xl mt-2 block ${stats && stats.total_profit >= 0 ? 'text-emerald-400' : 'text-cs-red-bright'}`}>
            {stats ? (stats.total_profit >= 0 ? `+$${stats.total_profit.toFixed(2)}` : `-$${Math.abs(stats.total_profit).toFixed(2)}`) : '$0.00'}
          </span>
        </div>

        {/* Avg Edge */}
        <div className="cs-card p-6 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '300ms' }}>
          <p className="cs-stat-label">Average Edge</p>
          <span className="cs-stat text-4xl mt-2 block">
            {stats?.avg_edge ? `+${(stats.avg_edge * 100).toFixed(1)}%` : '0.0%'}
          </span>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 items-start">
        {/* Left: Bet History Table */}
        <div className="lg:col-span-2 cs-card p-0 overflow-hidden">
          <div className="px-6 py-4 border-b border-cs-border/40 flex justify-between items-center">
            <h2 className="text-sm font-semibold tracking-wider uppercase text-white">Bet Ledger</h2>
            <div className="flex items-center gap-4 text-xs font-mono">
              <span className="text-emerald-400">W: {stats?.wins || 0}</span>
              <span className="text-cs-red-bright">L: {stats?.losses || 0}</span>
              <span className="text-amber-500">P: {stats?.pushes || 0}</span>
              <span className="text-cs-muted">Pending: {stats?.pending || 0}</span>
            </div>
          </div>
          
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs font-medium">
              <thead>
                <tr className="border-b border-cs-border/30 bg-cs-black/40 text-cs-muted uppercase tracking-wider text-[10px]">
                  <th className="px-5 py-3">Date</th>
                  <th className="px-5 py-3">Player</th>
                  <th className="px-5 py-3">Market</th>
                  <th className="px-5 py-3 text-center">Odds</th>
                  <th className="px-5 py-3 text-right">Stake</th>
                  <th className="px-5 py-3 text-center">Result</th>
                  <th className="px-5 py-3 text-right">P&L</th>
                  <th className="px-5 py-3 text-center">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-cs-border/20">
                {bets.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="px-6 py-12 text-center text-cs-muted">
                      No bets tracked yet. Use the form to place your first bet.
                    </td>
                  </tr>
                ) : (
                  bets.map((bet) => {
                    const pl = bet.profit_loss;
                    const isPlPositive = pl !== null && pl > 0;
                    return (
                      <tr key={bet.id} className="hover:bg-cs-dark/25 transition-colors duration-150 odd:bg-cs-dark/10">
                        <td className="px-5 py-3.5 font-mono text-cs-muted">{formatDate(bet.placed_at)}</td>
                        <td className="px-5 py-3.5 font-semibold text-white">
                          <div>{bet.player}</div>
                          {bet.opposing_team && <div className="text-[10px] text-cs-muted font-normal">vs {bet.opposing_team}</div>}
                        </td>
                        <td className="px-5 py-3.5">
                          <div className="flex items-center gap-1.5">
                            <span className={`text-[10px] px-1.5 py-0.5 rounded font-black ${bet.over_under === 'OVER' ? 'bg-cs-red/20 text-cs-red-bright' : 'bg-cs-muted/20 text-cs-muted'}`}>
                              {bet.over_under}
                            </span>
                            <span className="text-white font-mono">{bet.line} {bet.stat}</span>
                          </div>
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
                              onClick={() => {
                                setSettlingBet(bet);
                                setSettleResult('WIN');
                              }}
                              className="px-2 py-1 rounded bg-cs-red hover:bg-cs-red-bright text-white text-[10px] font-bold transition-all"
                            >
                              Settle
                            </button>
                          ) : (
                            <span className="text-[10px] text-cs-muted font-mono">{bet.settled_at ? formatDate(bet.settled_at) : '—'}</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Right: Place New Bet Form */}
        <div className="cs-card p-6">
          <h2 className="text-sm font-semibold tracking-wider uppercase text-white mb-5 flex items-center gap-2">
            <Plus className="w-4 h-4 text-cs-red" /> Place New Bet
          </h2>

          <form onSubmit={handlePlaceBet} className="space-y-4 text-left">
            {/* Player */}
            <div>
              <label className="cs-label">Player Name *</label>
              <input
                type="text"
                placeholder="e.g. A'ja Wilson"
                value={player}
                onChange={(e) => setPlayer(e.target.value)}
                className="cs-input"
                required
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* Stat */}
              <div>
                <label className="cs-label">Stat Type</label>
                <select
                  value={stat}
                  onChange={(e) => setStat(e.target.value)}
                  className="cs-input bg-cs-black py-2.5"
                >
                  <option>Points</option>
                  <option>Rebounds</option>
                  <option>Assists</option>
                  <option>Threes</option>
                  <option>PRA</option>
                </select>
              </div>

              {/* Line */}
              <div>
                <label className="cs-label">Line *</label>
                <input
                  type="number"
                  step="0.5"
                  placeholder="e.g. 22.5"
                  value={line}
                  onChange={(e) => setLine(e.target.value)}
                  className="cs-input"
                  required
                />
              </div>
            </div>

            {/* Side toggle (OVER / UNDER) */}
            <div>
              <label className="cs-label">Side</label>
              <div className="grid grid-cols-2 gap-2 p-1 rounded-xl bg-cs-black border border-cs-border/40">
                <button
                  type="button"
                  onClick={() => setOverUnder('OVER')}
                  className={`py-2 text-xs font-bold rounded-lg transition-all ${overUnder === 'OVER' ? 'bg-cs-red text-white shadow-glow-red-sm' : 'text-cs-muted hover:text-white'}`}
                >
                  OVER
                </button>
                <button
                  type="button"
                  onClick={() => setOverUnder('UNDER')}
                  className={`py-2 text-xs font-bold rounded-lg transition-all ${overUnder === 'UNDER' ? 'bg-cs-red text-white shadow-glow-red-sm' : 'text-cs-muted hover:text-white'}`}
                >
                  UNDER
                </button>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* Book Odds */}
              <div>
                <label className="cs-label">Book Odds *</label>
                <input
                  type="number"
                  placeholder="-110"
                  value={bookOdds}
                  onChange={(e) => setBookOdds(e.target.value)}
                  className="cs-input font-mono"
                  required
                />
              </div>

              {/* Stake */}
              <div>
                <label className="cs-label">Stake ($) *</label>
                <input
                  type="number"
                  step="0.01"
                  placeholder="e.g. 100"
                  value={stake}
                  onChange={(e) => setStake(e.target.value)}
                  className="cs-input font-mono"
                  required
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              {/* True Odds */}
              <div>
                <label className="cs-label">True Odds (Opt)</label>
                <input
                  type="number"
                  step="0.01"
                  placeholder="e.g. -125"
                  value={trueOdds}
                  onChange={(e) => setTrueOdds(e.target.value)}
                  className="cs-input font-mono"
                />
              </div>

              {/* Edge % */}
              <div>
                <label className="cs-label">Edge % (Opt)</label>
                <input
                  type="number"
                  step="0.01"
                  placeholder="e.g. 0.08"
                  value={edgePct}
                  onChange={(e) => setEdgePct(e.target.value)}
                  className="cs-input font-mono"
                />
              </div>
            </div>

            {/* Opposing Team */}
            <div>
              <label className="cs-label">Opponent Team</label>
              <input
                type="text"
                placeholder="e.g. NYL"
                value={opposingTeam}
                onChange={(e) => setOpposingTeam(e.target.value)}
                className="cs-input"
              />
            </div>

            {/* Notes */}
            <div>
              <label className="cs-label">Internal Analysis Notes</label>
              <textarea
                placeholder="e.g. Model edge based on matchup history..."
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                className="cs-input resize-none text-xs"
              />
            </div>

            <button
              type="submit"
              disabled={submitting}
              className="w-full cs-btn-primary flex items-center justify-center gap-2 mt-4"
            >
              {submitting ? 'Registering...' : (
                <>
                  <Plus className="w-4 h-4" /> Save to Ledger
                </>
              )}
            </button>
          </form>
        </div>
      </div>

      {/* Settle Bet Modal overlay */}
      {settlingBet && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="cs-card w-full max-w-sm p-6 relative border-cs-red/40 animate-fade-in text-left">
            <button
              onClick={() => setSettlingBet(null)}
              className="absolute right-4 top-4 text-cs-muted hover:text-white"
            >
              <X className="w-5 h-5" />
            </button>

            <h3 className="text-base font-bold text-white mb-4 flex items-center gap-2">
              <Check className="w-5 h-5 text-emerald-400" /> Settle Pending Bet
            </h3>

            <div className="mb-4 text-xs font-medium space-y-1 bg-cs-black/60 p-3 rounded-lg border border-cs-border/30">
              <div className="text-cs-muted">BET DETAIL:</div>
              <div className="text-white font-bold">{settlingBet.player} ({settlingBet.opposing_team || 'WNBA'})</div>
              <div className="text-white">{settlingBet.over_under} {settlingBet.line} {settlingBet.stat}</div>
              <div className="text-cs-muted font-mono">Stake: ${settlingBet.stake.toFixed(2)} @ {formatOdds(settlingBet.book_odds)}</div>
            </div>

            <form onSubmit={handleSettleSubmit} className="space-y-4">
              {/* Result Select */}
              <div>
                <label className="cs-label">Outcome Result</label>
                <select
                  value={settleResult}
                  onChange={(e) => setSettleResult(e.target.value as any)}
                  className="cs-input bg-cs-black"
                >
                  <option value="WIN">WIN</option>
                  <option value="LOSS">LOSS</option>
                  <option value="PUSH">PUSH</option>
                </select>
              </div>

              {/* Actual Outcome Value */}
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

              <div className="grid grid-cols-2 gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setSettlingBet(null)}
                  className="py-2.5 rounded-xl border border-cs-border hover:bg-cs-dark/30 text-xs font-bold text-center text-cs-muted hover:text-white transition-all"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="py-2.5 cs-btn-primary text-xs font-bold text-center"
                >
                  Confirm Settlement
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
