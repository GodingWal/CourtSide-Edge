import { useState, useEffect, useRef } from 'react';
import { Receipt, Upload, Sparkles } from 'lucide-react';
import { SkeletonCard, SkeletonTable } from '../components/Skeleton';
import { useToast } from '../components/ToastProvider';
import { API_BASE } from '../lib/config';
import type { Bet, BetStats, GeneratedParlay } from '../components/bet-tracker/types';
import BetStatsRow from '../components/bet-tracker/BetStatsRow';
import BetsTable from '../components/bet-tracker/BetsTable';
import SettleModal from '../components/bet-tracker/SettleModal';
import TicketUploaderModal from '../components/bet-tracker/TicketUploaderModal';
import ParlayGeneratorModal from '../components/bet-tracker/ParlayGeneratorModal';

export default function BetTracker() {
  const { toast } = useToast();
  const [bets, setBets] = useState<Bet[]>([]);
  const [stats, setStats] = useState<BetStats | null>(null);
  const [loading, setLoading] = useState(true);

  // Expanded parlays tracking
  const [expandedParlays, setExpandedParlays] = useState<Record<number, boolean>>({});
  const [expandedHedges, setExpandedHedges] = useState<Record<number, boolean>>({});

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
  const [generatedParlay, setGeneratedParlay] = useState<GeneratedParlay | null>(null);
  const [generatorStake, setGeneratorStake] = useState('100');
  const [submittingWager, setSubmittingWager] = useState(false);

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

  const toggleExpandHedges = (id: number) => {
    setExpandedHedges(prev => ({
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

  // Helper arrays for mapping wagers
  const rootBets = bets.filter(b => (b.parent_id === null || b.parent_id === undefined) && b.is_hedge !== 1);
  const getLegs = (parentId: number) => bets.filter(b => b.parent_id === parentId && b.is_hedge !== 1);
  const getHedges = (parentId: number) => bets.filter(b => b.parent_id === parentId && b.is_hedge === 1);

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
      <BetStatsRow stats={stats} />

      {/* Compressed Bet Ledger */}
      <BetsTable
        stats={stats}
        rootBets={rootBets}
        getLegs={getLegs}
        getHedges={getHedges}
        expandedParlays={expandedParlays}
        expandedHedges={expandedHedges}
        toggleExpandParlay={toggleExpandParlay}
        toggleExpandHedges={toggleExpandHedges}
        onSettle={(bet) => {
          setSettlingBet(bet);
          setSettleResult('WIN');
        }}
      />

      {/* ── Settle Bet Modal ────────────────────────────────────────────────── */}
      {settlingBet && (
        <SettleModal
          settlingBet={settlingBet}
          settleResult={settleResult}
          setSettleResult={setSettleResult}
          actualValue={actualValue}
          setActualValue={setActualValue}
          onSubmit={handleSettleSubmit}
          onClose={() => setSettlingBet(null)}
        />
      )}

      {/* ── Ticket Uploader Modal (OCR) ─────────────────────────────────────── */}
      {uploadOpen && (
        <TicketUploaderModal
          uploadStep={uploadStep}
          uploadFile={uploadFile}
          submittingWager={submittingWager}
          confirmIsParlay={confirmIsParlay}
          confirmOdds={confirmOdds}
          setConfirmOdds={setConfirmOdds}
          confirmStake={confirmStake}
          setConfirmStake={setConfirmStake}
          confirmNotes={confirmNotes}
          setConfirmNotes={setConfirmNotes}
          confirmPlayer={confirmPlayer}
          setConfirmPlayer={setConfirmPlayer}
          confirmStat={confirmStat}
          setConfirmStat={setConfirmStat}
          confirmLine={confirmLine}
          setConfirmLine={setConfirmLine}
          confirmOverUnder={confirmOverUnder}
          setConfirmOverUnder={setConfirmOverUnder}
          confirmOpponent={confirmOpponent}
          setConfirmOpponent={setConfirmOpponent}
          confirmLegs={confirmLegs}
          setConfirmLegs={setConfirmLegs}
          onSubmit={handleLogExtractedBet}
          onClose={() => {
            setUploadOpen(false);
            setUploadFile(null);
          }}
        />
      )}

      {/* ── Agent 13 Parlay Generator Modal ─────────────────────────────────── */}
      {generatorOpen && (
        <ParlayGeneratorModal
          generating={generating}
          generatedParlay={generatedParlay}
          generatorStake={generatorStake}
          setGeneratorStake={setGeneratorStake}
          submittingWager={submittingWager}
          onLogParlay={handleLogGeneratedParlay}
          onClose={() => setGeneratorOpen(false)}
        />
      )}
    </div>
  );
}
