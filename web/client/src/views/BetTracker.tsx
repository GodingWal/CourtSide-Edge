import { useState, useEffect, useRef } from 'react';
import {
  Receipt,
  Check,
  X,
  Clock,
  Upload,
  Sparkles,
  ChevronDown,
  ChevronUp,
  Loader2,
  FileImage,
  Plus,
  Trash2
} from 'lucide-react';
import { SkeletonCard, SkeletonTable } from '../components/Skeleton';
import { useToast } from '../components/ToastProvider';

interface Bet {
  id: number;
  parent_id: number | null;
  is_parlay: number | null;
  player: string | null;
  stat: string | null;
  line: number | null;
  over_under: 'OVER' | 'UNDER' | null;
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

  // Expanded parlays tracking
  const [expandedParlays, setExpandedParlays] = useState<Record<number, boolean>>({});

  // Settle modal states
  const [settlingBet, setSettlingBet] = useState<Bet | null>(null);
  const [settleResult, setSettleResult] = useState<'WIN' | 'LOSS' | 'PUSH'>('WIN');
  const [actualValue, setActualValue] = useState('');

  // Uploader states
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadStep, setUploadStep] = useState<'select' | 'processing' | 'confirm'>('select');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Extracted bet states (OCR confirmation form)
  const [confirmIsParlay, setConfirmIsParlay] = useState(0);
  const [confirmOdds, setConfirmOdds] = useState('260');
  const [confirmStake, setConfirmStake] = useState('100');
  const [confirmNotes, setConfirmNotes] = useState('Extracted via OCR');
  // Single leg form fields
  const [confirmPlayer, setConfirmPlayer] = useState('');
  const [confirmStat, setConfirmStat] = useState('PTS');
  const [confirmLine, setConfirmLine] = useState('');
  const [confirmOverUnder, setConfirmOverUnder] = useState<'OVER' | 'UNDER'>('OVER');
  const [confirmOpponent, setConfirmOpponent] = useState('');
  // Parlay legs form fields
  const [confirmLegs, setConfirmLegs] = useState<any[]>([]);

  // Generator states (Agent 13)
  const [generatorOpen, setGeneratorOpen] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generatedParlay, setGeneratedParlay] = useState<{
    legs: any[];
    parlay_odds: number;
    summary: string;
  } | null>(null);
  const [generatorStake, setGeneratorStake] = useState('100');
  const [submittingWager, setSubmittingWager] = useState(false);

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

      // Expand all active parlays by default so users see their current legs
      const newExpanded: Record<number, boolean> = {};
      betsData.forEach((b: Bet) => {
        if (b.is_parlay === 1 && b.result === null) {
          newExpanded[b.id] = true;
        }
      });
      setExpandedParlays(prev => ({ ...newExpanded, ...prev }));
    } catch (err) {
      console.error('Failed to load bet terminal data:', err);
      toast({
        title: 'Network Sync Offline',
        description: 'Failed to synchronize with backend database. Rendered cache state.',
        variant: 'danger'
      });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  const toggleExpandParlay = (id: number) => {
    setExpandedParlays(prev => ({
      ...prev,
      [id]: !prev[id]
    }));
  };

  // ── Ticket Uploader Handlers ────────────────────────────────────────────────
  const triggerFileSelect = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0];
      setUploadFile(file);
      setUploadStep('processing');
      setUploadOpen(true);
      startOcrMock();
    }
  };

  const startOcrMock = async () => {
    try {
      const res = await fetch(`${API_BASE}/bets/upload`, { method: 'POST' });
      if (!res.ok) throw new Error('OCR failed');
      const data = await res.json();

      setConfirmIsParlay(data.is_parlay);
      setConfirmOdds(data.book_odds.toString());
      setConfirmStake(data.stake.toString());
      setConfirmNotes(data.notes || 'Extracted via OCR');

      if (data.is_parlay === 1) {
        setConfirmLegs(data.legs || []);
      } else {
        setConfirmPlayer(data.player || '');
        setConfirmStat(data.stat || 'PTS');
        setConfirmLine(data.line ? data.line.toString() : '');
        setConfirmOverUnder(data.over_under || 'OVER');
        setConfirmOpponent(data.opposing_team || '');
      }

      setUploadStep('confirm');
    } catch (err) {
      toast({
        title: 'OCR Capture Failure',
        description: 'Failed to extract text from the screenshot.',
        variant: 'danger'
      });
      setUploadOpen(false);
    }
  };

  const handleLogExtractedBet = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmittingWager(true);

    try {
      let body: any = {};
      if (confirmIsParlay === 1) {
        body = {
          is_parlay: 1,
          book_odds: parseInt(confirmOdds, 10),
          stake: parseFloat(confirmStake),
          notes: confirmNotes,
          legs: confirmLegs
        };
      } else {
        body = {
          is_parlay: 0,
          player: confirmPlayer,
          stat: confirmStat,
          line: parseFloat(confirmLine),
          over_under: confirmOverUnder,
          book_odds: parseInt(confirmOdds, 10),
          stake: parseFloat(confirmStake),
          opposing_team: confirmOpponent || null,
          notes: confirmNotes || null
        };
      }

      const res = await fetch(`${API_BASE}/bets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });

      if (!res.ok) throw new Error('Wager upload failed');

      toast({
        title: 'Ticket Saved',
        description: confirmIsParlay === 1 ? 'Successfully logged multi-leg parlay.' : `Successfully logged wager on ${confirmPlayer}.`,
        variant: 'success'
      });

      setUploadOpen(false);
      setUploadFile(null);
      fetchData();
    } catch (err) {
      toast({
        title: 'Database Fault',
        description: 'Failed to write OCR bet to SQLite ledger.',
        variant: 'danger'
      });
    } finally {
      setSubmittingWager(false);
    }
  };

  // ── Agent 13 Parlay Generator Handlers ─────────────────────────────────────
  const triggerGenerateParlay = async () => {
    setGeneratorOpen(true);
    setGenerating(true);
    setGeneratedParlay(null);

    try {
      const res = await fetch(`${API_BASE}/parlay/generate`, { method: 'POST' });
      if (!res.ok) throw new Error('Generation failed');
      const data = await res.json();
      setGeneratedParlay(data);
    } catch (err) {
      toast({
        title: 'Matchup Model Timeout',
        description: 'Agent 13 failed to retrieve positive EV edges.',
        variant: 'danger'
      });
      setGeneratorOpen(false);
    } finally {
      setGenerating(false);
    }
  };

  const handleLogGeneratedParlay = async () => {
    if (!generatedParlay) return;
    setSubmittingWager(true);

    try {
      const body = {
        is_parlay: 1,
        book_odds: generatedParlay.parlay_odds,
        stake: parseFloat(generatorStake),
        notes: `Agent 13: ${generatedParlay.summary}`,
        legs: generatedParlay.legs
      };

      const res = await fetch(`${API_BASE}/bets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });

      if (!res.ok) throw new Error('Wager log failed');

      toast({
        title: 'Agent Parlay Logged',
        description: `Logged 2-leg parlay wager of $${generatorStake}.`,
        variant: 'success'
      });

      setGeneratorOpen(false);
      fetchData();
    } catch (err) {
      toast({
        title: 'Database Fault',
        description: 'Failed to write generated parlay to SQLite ledger.',
        variant: 'danger'
      });
    } finally {
      setSubmittingWager(false);
    }
  };

  // ── Settlement Handlers ─────────────────────────────────────────────────────
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
        description: `Wager has been settled as a ${settleResult}.`,
        variant: 'success'
      });

      setSettlingBet(null);
      setActualValue('');
      setSettleResult('WIN');
      fetchData();
    } catch (err) {
      toast({
        title: 'Sync Failure',
        description: 'Failed to settle wager in database.',
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

  // Helper arrays for mapping wagers
  const rootBets = bets.filter(b => b.parent_id === null || b.parent_id === undefined);
  const getLegs = (parentId: number) => bets.filter(b => b.parent_id === parentId);

  if (loading) {
    return (
      <div className="p-6 md:p-8 space-y-6 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
        <div className="flex items-center gap-3">
          <Receipt className="w-7 h-7 text-cs-red" />
          <h1 className="text-3xl font-extrabold text-white">Bet Terminal</h1>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-5">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
        <SkeletonTable />
      </div>
    );
  }

  return (
    <div className="p-6 md:p-8 space-y-6 max-w-[1440px] mx-auto w-full min-h-screen animate-fade-in">
      {/* Hidden File Input for OCR Uploader */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileChange}
        accept="image/*"
        className="hidden"
      />

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-extrabold text-white tracking-tight flex items-center gap-3">
            <Receipt className="w-7 h-7 text-cs-red drop-shadow-[0_0_8px_rgba(239,68,68,0.6)]" />
            Bet Terminal
          </h1>
          <p className="text-xs text-cs-muted mt-1 font-mono uppercase tracking-wider">
            Execution Console &bull; {bets.length} Nodes Loaded
          </p>
        </div>

        {/* Core Controls */}
        <div className="flex items-center gap-3">
          <button
            onClick={triggerFileSelect}
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-cs-border hover:border-cs-red/50 hover:bg-cs-red/10 text-xs font-bold text-white transition-all cursor-pointer"
          >
            <Upload className="w-4 h-4 text-cs-red" />
            Upload Bet Ticket
          </button>
          <button
            onClick={triggerGenerateParlay}
            className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-gradient-to-r from-cs-red to-cs-red-bright hover:brightness-110 shadow-glow-red-sm text-xs font-bold text-white transition-all cursor-pointer"
          >
            <Sparkles className="w-4 h-4" />
            Generate Parlay
          </button>
        </div>
      </div>

      {/* KPI Stats Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="cs-card p-5 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '0ms' }}>
          <p className="cs-stat-label">Total Bets Logged</p>
          <span className="cs-stat text-3xl mt-1.5 block">{stats?.total_bets || 0}</span>
        </div>

        <div className="cs-card p-5 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '100ms' }}>
          <p className="cs-stat-label">Win Rate</p>
          <span className="cs-stat text-3xl mt-1.5 block text-gradient-red">
            {stats?.win_rate ? `${stats.win_rate.toFixed(1)}%` : '0.0%'}
          </span>
        </div>

        <div className="cs-card p-5 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '200ms' }}>
          <p className="cs-stat-label">Net Profit/Loss</p>
          <span className={`cs-stat text-3xl mt-1.5 block ${stats && stats.total_profit >= 0 ? 'text-emerald-400' : 'text-cs-red-bright'}`}>
            {stats ? (stats.total_profit >= 0 ? `+$${stats.total_profit.toFixed(2)}` : `-$${Math.abs(stats.total_profit).toFixed(2)}`) : '$0.00'}
          </span>
        </div>

        <div className="cs-card p-5 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '300ms' }}>
          <p className="cs-stat-label">Average Edge</p>
          <span className="cs-stat text-3xl mt-1.5 block">
            {stats?.avg_edge ? `+${(stats.avg_edge * 100).toFixed(1)}%` : '0.0%'}
          </span>
        </div>
      </div>

      {/* Compressed Bet Ledger */}
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
            <tbody className="divide-y divide-cs-border/20">
              {rootBets.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-6 py-12 text-center text-cs-muted font-mono">
                    No wagers tracked in database. Upload a screenshot to begin.
                  </td>
                </tr>
              ) : (
                rootBets.map((bet) => {
                  const isParlay = bet.is_parlay === 1;
                  const legs = isParlay ? getLegs(bet.id) : [];
                  const isExpanded = expandedParlays[bet.id] || false;
                  
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
                                className="p-1 hover:bg-cs-dark rounded transition-colors text-cs-red"
                              >
                                {isExpanded ? <ChevronUp className="w-4.5 h-4.5" /> : <ChevronDown className="w-4.5 h-4.5" />}
                              </button>
                              <div>
                                <div className="text-white font-bold flex items-center gap-1.5">
                                  Multi-Leg Parlay
                                  <span className="text-[9px] bg-cs-red/20 text-cs-red-bright px-1.5 py-0.2 rounded font-mono">
                                    {legs.length} Legs
                                  </span>
                                </div>
                                <div className="text-[10px] text-cs-muted font-normal max-w-xs truncate">
                                  {bet.notes || 'Aggregated EV Parlay'}
                                </div>
                              </div>
                            </div>
                          ) : (
                            <div>
                              <div className="text-white font-semibold">{bet.player}</div>
                              {bet.opposing_team && <div className="text-[10px] text-cs-muted font-normal">vs {bet.opposing_team}</div>}
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
                              onClick={() => {
                                setSettlingBet(bet);
                                setSettleResult('WIN');
                              }}
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
                    </tbody>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Settle Bet Modal ────────────────────────────────────────────────── */}
      {settlingBet && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="cs-card w-full max-w-sm p-6 relative border-cs-red/40 animate-fade-in text-left">
            <button
              onClick={() => setSettlingBet(null)}
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

            <form onSubmit={handleSettleSubmit} className="space-y-4">
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
                  onClick={() => setSettlingBet(null)}
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
      )}

      {/* ── Ticket Uploader Modal (OCR) ─────────────────────────────────────── */}
      {uploadOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="cs-card w-full max-w-lg p-6 relative border-cs-red/40 animate-fade-in text-left">
            <button
              onClick={() => {
                setUploadOpen(false);
                setUploadFile(null);
              }}
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

                <form onSubmit={handleLogExtractedBet} className="space-y-4">
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
                            onChange={(e) => setConfirmOverUnder(e.target.value as any)}
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
                      onClick={() => {
                        setUploadOpen(false);
                        setUploadFile(null);
                      }}
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
      )}

      {/* ── Agent 13 Parlay Generator Modal ─────────────────────────────────── */}
      {generatorOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
          <div className="cs-card w-full max-w-lg p-6 relative border-cs-red/40 animate-fade-in text-left">
            <button
              onClick={() => setGeneratorOpen(false)}
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
                      onClick={() => setGeneratorOpen(false)}
                      className="py-2.5 rounded-xl border border-cs-border hover:bg-cs-dark/30 text-xs font-bold text-center text-cs-muted hover:text-white transition-all cursor-pointer"
                    >
                      Discard Parlay
                    </button>
                    <button
                      onClick={handleLogGeneratedParlay}
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
      )}
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
