import { useEffect, useMemo, useState } from 'react';
import { BarChart3, Users, Swords, Search } from 'lucide-react';
import { API_BASE } from '../lib/config';

/* ── Types ── */

interface TeamStats {
  team: string;
  games: number;
  wins: number;
  losses: number;
  ppg: number;
  opp_ppg: number;
  net_ppg: number;
  rpg: number;
  apg: number;
  spg: number;
  bpg: number;
  topg: number;
  fg_pct: number | null;
  fg3_pct: number | null;
  ft_pct: number | null;
  last10: string;
}

interface PlayerStats {
  player_id: string;
  player: string;
  team: string | null;
  gp: number;
  mpg: number;
  ppg: number;
  rpg: number;
  apg: number;
  spg: number;
  bpg: number;
  topg: number;
  fg_pct: number | null;
  fg3_pct: number | null;
  ft_pct: number | null;
  usage: number | null;
  l5_ppg: number | null;
  last_game: string | null;
}

interface GameLogRow {
  date: string;
  team: string;
  opp: string;
  min: number | null;
  pts: number | null;
  reb: number | null;
  ast: number | null;
  stl: number | null;
  blk: number | null;
  tov: number | null;
  fgm: number | null;
  fga: number | null;
  tpm: number | null;
  tpa: number | null;
}

interface H2HGame {
  game_id: string;
  date: string;
  teams: Record<string, number>;
}

type Tab = 'teams' | 'players' | 'compare';
type CompareMode = 'team-team' | 'player-player' | 'player-team';

const fmt = (v: number | null | undefined, suffix = '') =>
  v === null || v === undefined ? '—' : `${v}${suffix}`;

/* ── Comparison row: highlights the better side ── */
function CompareRow({
  label, a, b, lowerIsBetter = false,
}: { label: string; a: number | null | undefined; b: number | null | undefined; lowerIsBetter?: boolean }) {
  const aWins = a !== null && a !== undefined && b !== null && b !== undefined &&
    (lowerIsBetter ? a < b : a > b);
  const bWins = a !== null && a !== undefined && b !== null && b !== undefined &&
    (lowerIsBetter ? b < a : b > a);
  return (
    <div className="grid grid-cols-3 items-center py-2 border-b border-cs-border/20 last:border-0">
      <span className={`text-sm font-mono font-bold text-right pr-4 ${aWins ? 'text-emerald-400' : 'text-white/80'}`}>
        {fmt(a)}
      </span>
      <span className="text-[10px] text-cs-muted uppercase tracking-wider text-center">{label}</span>
      <span className={`text-sm font-mono font-bold pl-4 ${bWins ? 'text-emerald-400' : 'text-white/80'}`}>
        {fmt(b)}
      </span>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <p className="text-xs text-cs-muted font-mono text-center py-10">{text}</p>;
}

/* ═══════════════════════════════════════════════════════════════════ */
export default function StatsCenter() {
  const [tab, setTab] = useState<Tab>('teams');
  const [teams, setTeams] = useState<Record<string, TeamStats>>({});
  const [players, setPlayers] = useState<PlayerStats[]>([]);
  const [updated, setUpdated] = useState<number | null>(null);
  const [playerSearch, setPlayerSearch] = useState('');

  // Compare state
  const [compareMode, setCompareMode] = useState<CompareMode>('team-team');
  const [teamA, setTeamA] = useState('');
  const [teamB, setTeamB] = useState('');
  const [playerA, setPlayerA] = useState('');
  const [playerB, setPlayerB] = useState('');
  const [h2h, setH2h] = useState<H2HGame[]>([]);
  const [gamelog, setGamelog] = useState<GameLogRow[]>([]);

  useEffect(() => {
    const fetchSnapshots = async () => {
      try {
        const [teamsRes, playersRes] = await Promise.all([
          fetch(`${API_BASE}/stats/teams`),
          fetch(`${API_BASE}/stats/players`),
        ]);
        if (teamsRes.ok) {
          const data = await teamsRes.json();
          setTeams(data.teams ?? {});
          if (data.updated) setUpdated(data.updated);
        }
        if (playersRes.ok) {
          const data = await playersRes.json();
          setPlayers(data.players ?? []);
        }
      } catch (err) {
        console.error('Failed to fetch stats snapshots:', err);
      }
    };
    fetchSnapshots();
    const interval = setInterval(fetchSnapshots, 60000);
    return () => clearInterval(interval);
  }, []);

  const teamList = useMemo(() => Object.values(teams).sort((a, b) => b.wins - a.wins || b.net_ppg - a.net_ppg), [teams]);
  const teamCodes = useMemo(() => Object.keys(teams).sort(), [teams]);
  const playerNames = useMemo(() => players.map((p) => p.player), [players]);

  const filteredPlayers = useMemo(() => {
    const q = playerSearch.trim().toLowerCase();
    if (!q) return players;
    return players.filter((p) => p.player.toLowerCase().includes(q) || (p.team ?? '').toLowerCase().includes(q));
  }, [players, playerSearch]);

  /* head-to-head fetch (team vs team) */
  useEffect(() => {
    if (compareMode !== 'team-team' || !teamA || !teamB || teamA === teamB) {
      setH2h([]);
      return;
    }
    fetch(`${API_BASE}/stats/h2h?a=${encodeURIComponent(teamA)}&b=${encodeURIComponent(teamB)}`)
      .then((r) => (r.ok ? r.json() : { games: [] }))
      .then((d) => setH2h(d.games ?? []))
      .catch(() => setH2h([]));
  }, [compareMode, teamA, teamB]);

  /* player game log fetch (player vs team / matchups) */
  useEffect(() => {
    if (compareMode !== 'player-team' || !playerA) {
      setGamelog([]);
      return;
    }
    fetch(`${API_BASE}/stats/gamelog?player=${encodeURIComponent(playerA)}`)
      .then((r) => (r.ok ? r.json() : { games: [] }))
      .then((d) => setGamelog(d.games ?? []))
      .catch(() => setGamelog([]));
  }, [compareMode, playerA]);

  const pA = players.find((p) => p.player === playerA) ?? null;
  const pB = players.find((p) => p.player === playerB) ?? null;
  const tA = teams[teamA] ?? null;
  const tB = teams[teamB] ?? null;

  const vsTeamLog = useMemo(
    () => gamelog.filter((g) => g.opp === teamB),
    [gamelog, teamB]
  );
  const vsTeamAvg = useMemo(() => {
    if (!vsTeamLog.length) return null;
    const avg = (key: keyof GameLogRow) => {
      const vals = vsTeamLog.map((g) => g[key]).filter((v): v is number => typeof v === 'number');
      return vals.length ? Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 10) / 10 : null;
    };
    return { pts: avg('pts'), reb: avg('reb'), ast: avg('ast'), min: avg('min'), games: vsTeamLog.length };
  }, [vsTeamLog]);

  const noData = teamList.length === 0 && players.length === 0;

  const selectCls = 'cs-input !py-2 text-sm font-mono';

  return (
    <div className="min-h-screen bg-cs-black p-4 md:p-6 animate-fade-in">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-cs-red/10 border border-cs-red/20 flex items-center justify-center shadow-glow-red-sm">
            <BarChart3 className="w-5 h-5 text-cs-red" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-white tracking-tight">Stats Center</h1>
            <p className="text-sm text-cs-muted">
              Team & player stats · comparisons · matchups
              {updated && (
                <span className="ml-2 text-[10px] font-mono text-cs-muted/70">
                  snapshot {new Date(updated * 1000).toLocaleString()}
                </span>
              )}
            </p>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1.5 bg-cs-dark/60 border border-cs-border/40 rounded-xl p-1">
          {([
            { id: 'teams', label: 'Teams', icon: BarChart3 },
            { id: 'players', label: 'Players', icon: Users },
            { id: 'compare', label: 'Compare', icon: Swords },
          ] as { id: Tab; label: string; icon: any }[]).map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold transition-all cursor-pointer ${
                tab === id ? 'bg-cs-red/15 text-cs-red shadow-glow-red-sm' : 'text-cs-muted hover:text-white'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {label}
            </button>
          ))}
        </div>
      </div>

      {noData && (
        <div className="cs-card p-8">
          <EmptyState text="No stats snapshot yet — Agent 0 publishes team & player aggregates after its box-score ETL runs." />
        </div>
      )}

      {/* ── Teams tab ── */}
      {tab === 'teams' && teamList.length > 0 && (
        <div className="cs-card p-0 overflow-hidden animate-slide-up">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead>
                <tr className="border-b border-cs-border text-cs-muted uppercase font-mono text-[10px] tracking-wider">
                  <th className="px-4 py-3">Team</th>
                  <th className="px-3 py-3 text-center">W-L</th>
                  <th className="px-3 py-3 text-center">Last 10</th>
                  <th className="px-3 py-3 text-right">PPG</th>
                  <th className="px-3 py-3 text-right">OPP PPG</th>
                  <th className="px-3 py-3 text-right">NET</th>
                  <th className="px-3 py-3 text-right">REB</th>
                  <th className="px-3 py-3 text-right">AST</th>
                  <th className="px-3 py-3 text-right">TOV</th>
                  <th className="px-3 py-3 text-right">FG%</th>
                  <th className="px-3 py-3 text-right">3P%</th>
                </tr>
              </thead>
              <tbody>
                {teamList.map((t, i) => (
                  <tr key={t.team} className={`border-b border-cs-border/30 hover:bg-cs-red/[0.04] transition-colors ${i % 2 === 0 ? 'bg-cs-dark/40' : ''}`}>
                    <td className="px-4 py-3 font-bold text-white">{t.team}</td>
                    <td className="px-3 py-3 text-center font-mono text-white">{t.wins}-{t.losses}</td>
                    <td className="px-3 py-3 text-center font-mono text-cs-muted">{t.last10}</td>
                    <td className="px-3 py-3 text-right font-mono text-white">{t.ppg}</td>
                    <td className="px-3 py-3 text-right font-mono text-cs-muted">{t.opp_ppg}</td>
                    <td className={`px-3 py-3 text-right font-mono font-bold ${t.net_ppg >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {t.net_ppg > 0 ? `+${t.net_ppg}` : t.net_ppg}
                    </td>
                    <td className="px-3 py-3 text-right font-mono text-cs-muted">{t.rpg}</td>
                    <td className="px-3 py-3 text-right font-mono text-cs-muted">{t.apg}</td>
                    <td className="px-3 py-3 text-right font-mono text-cs-muted">{t.topg}</td>
                    <td className="px-3 py-3 text-right font-mono text-cs-muted">{fmt(t.fg_pct)}</td>
                    <td className="px-3 py-3 text-right font-mono text-cs-muted">{fmt(t.fg3_pct)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Players tab ── */}
      {tab === 'players' && players.length > 0 && (
        <div className="space-y-4 animate-slide-up">
          <div className="relative max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-cs-muted" />
            <input
              type="text"
              value={playerSearch}
              onChange={(e) => setPlayerSearch(e.target.value)}
              placeholder="Search player or team…"
              className="cs-input !pl-9"
            />
          </div>
          <div className="cs-card p-0 overflow-hidden">
            <div className="overflow-x-auto max-h-[65vh] overflow-y-auto">
              <table className="w-full text-left text-xs">
                <thead className="sticky top-0 bg-cs-black z-10">
                  <tr className="border-b border-cs-border text-cs-muted uppercase font-mono text-[10px] tracking-wider">
                    <th className="px-4 py-3">Player</th>
                    <th className="px-3 py-3 text-center">Team</th>
                    <th className="px-3 py-3 text-right">GP</th>
                    <th className="px-3 py-3 text-right">MIN</th>
                    <th className="px-3 py-3 text-right">PTS</th>
                    <th className="px-3 py-3 text-right">REB</th>
                    <th className="px-3 py-3 text-right">AST</th>
                    <th className="px-3 py-3 text-right">STL</th>
                    <th className="px-3 py-3 text-right">BLK</th>
                    <th className="px-3 py-3 text-right">FG%</th>
                    <th className="px-3 py-3 text-right">3P%</th>
                    <th className="px-3 py-3 text-right">USG</th>
                    <th className="px-3 py-3 text-right">L5 PTS</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPlayers.slice(0, 150).map((p, i) => (
                    <tr key={p.player_id || p.player} className={`border-b border-cs-border/30 hover:bg-cs-red/[0.04] transition-colors ${i % 2 === 0 ? 'bg-cs-dark/40' : ''}`}>
                      <td className="px-4 py-2.5 font-bold text-white whitespace-nowrap">{p.player}</td>
                      <td className="px-3 py-2.5 text-center font-mono text-cs-muted">{p.team ?? '—'}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-cs-muted">{p.gp}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-cs-muted">{p.mpg}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-white font-bold">{p.ppg}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-white/80">{p.rpg}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-white/80">{p.apg}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-cs-muted">{p.spg}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-cs-muted">{p.bpg}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-cs-muted">{fmt(p.fg_pct)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-cs-muted">{fmt(p.fg3_pct)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-cs-muted">{fmt(p.usage)}</td>
                      <td className="px-3 py-2.5 text-right font-mono text-cs-red-bright">{fmt(p.l5_ppg)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── Compare tab ── */}
      {tab === 'compare' && !noData && (
        <div className="space-y-5 animate-slide-up">
          {/* Mode selector */}
          <div className="flex flex-wrap items-center gap-2">
            {([
              { id: 'team-team', label: 'Team vs Team' },
              { id: 'player-player', label: 'Player vs Player' },
              { id: 'player-team', label: 'Player vs Team' },
            ] as { id: CompareMode; label: string }[]).map(({ id, label }) => (
              <button
                key={id}
                onClick={() => setCompareMode(id)}
                className={`px-4 py-2 rounded-xl border text-xs font-bold transition-all cursor-pointer ${
                  compareMode === id
                    ? 'border-cs-red bg-cs-red/15 text-white shadow-glow-red-sm'
                    : 'border-cs-border text-cs-muted hover:border-cs-red/40 hover:text-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          {/* Selectors */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-2xl">
            {compareMode === 'team-team' && (
              <>
                <select value={teamA} onChange={(e) => setTeamA(e.target.value)} className={selectCls}>
                  <option value="">Select Team A…</option>
                  {teamCodes.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
                <select value={teamB} onChange={(e) => setTeamB(e.target.value)} className={selectCls}>
                  <option value="">Select Team B…</option>
                  {teamCodes.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </>
            )}
            {compareMode === 'player-player' && (
              <>
                <select value={playerA} onChange={(e) => setPlayerA(e.target.value)} className={selectCls}>
                  <option value="">Select Player A…</option>
                  {playerNames.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
                <select value={playerB} onChange={(e) => setPlayerB(e.target.value)} className={selectCls}>
                  <option value="">Select Player B…</option>
                  {playerNames.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
              </>
            )}
            {compareMode === 'player-team' && (
              <>
                <select value={playerA} onChange={(e) => setPlayerA(e.target.value)} className={selectCls}>
                  <option value="">Select Player…</option>
                  {playerNames.map((p) => <option key={p} value={p}>{p}</option>)}
                </select>
                <select value={teamB} onChange={(e) => setTeamB(e.target.value)} className={selectCls}>
                  <option value="">vs Opponent Team…</option>
                  {teamCodes.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </>
            )}
          </div>

          {/* ── Team vs Team ── */}
          {compareMode === 'team-team' && tA && tB && teamA !== teamB && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <div className="cs-card p-6">
                <div className="grid grid-cols-3 items-center mb-4">
                  <span className="text-lg font-black text-white text-right pr-4">{tA.team}</span>
                  <span className="text-[10px] text-cs-muted uppercase tracking-widest text-center">Season</span>
                  <span className="text-lg font-black text-white pl-4">{tB.team}</span>
                </div>
                <CompareRow label="Wins" a={tA.wins} b={tB.wins} />
                <CompareRow label="PPG" a={tA.ppg} b={tB.ppg} />
                <CompareRow label="Opp PPG" a={tA.opp_ppg} b={tB.opp_ppg} lowerIsBetter />
                <CompareRow label="Net PPG" a={tA.net_ppg} b={tB.net_ppg} />
                <CompareRow label="Rebounds" a={tA.rpg} b={tB.rpg} />
                <CompareRow label="Assists" a={tA.apg} b={tB.apg} />
                <CompareRow label="Turnovers" a={tA.topg} b={tB.topg} lowerIsBetter />
                <CompareRow label="FG%" a={tA.fg_pct} b={tB.fg_pct} />
                <CompareRow label="3P%" a={tA.fg3_pct} b={tB.fg3_pct} />
                <div className="grid grid-cols-3 items-center pt-3">
                  <span className="text-xs font-mono text-cs-muted text-right pr-4">{tA.last10}</span>
                  <span className="text-[10px] text-cs-muted uppercase tracking-wider text-center">Last 10</span>
                  <span className="text-xs font-mono text-cs-muted pl-4">{tB.last10}</span>
                </div>
              </div>

              <div className="cs-card p-6">
                <h3 className="text-sm font-semibold text-white uppercase tracking-wider mb-4">Head-to-Head Meetings</h3>
                {h2h.length === 0 ? (
                  <EmptyState text="No stored meetings between these teams yet." />
                ) : (
                  <div className="space-y-2 max-h-[360px] overflow-y-auto">
                    {h2h.map((g) => {
                      const aPts = g.teams[teamA];
                      const bPts = g.teams[teamB];
                      return (
                        <div key={g.game_id} className="flex items-center justify-between bg-cs-black/50 border border-cs-border/30 rounded-xl px-4 py-2.5">
                          <span className="text-[10px] font-mono text-cs-muted">{g.date}</span>
                          <span className="text-sm font-mono">
                            <span className={aPts > bPts ? 'text-emerald-400 font-bold' : 'text-white/70'}>{teamA} {aPts}</span>
                            <span className="text-cs-muted mx-2">—</span>
                            <span className={bPts > aPts ? 'text-emerald-400 font-bold' : 'text-white/70'}>{bPts} {teamB}</span>
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          )}

          {/* ── Player vs Player ── */}
          {compareMode === 'player-player' && pA && pB && playerA !== playerB && (
            <div className="cs-card p-6 max-w-2xl">
              <div className="grid grid-cols-3 items-center mb-4">
                <div className="text-right pr-4">
                  <div className="text-base font-black text-white">{pA.player}</div>
                  <div className="text-[10px] font-mono text-cs-muted">{pA.team} · {pA.gp} GP</div>
                </div>
                <span className="text-[10px] text-cs-muted uppercase tracking-widest text-center">Per Game</span>
                <div className="pl-4">
                  <div className="text-base font-black text-white">{pB.player}</div>
                  <div className="text-[10px] font-mono text-cs-muted">{pB.team} · {pB.gp} GP</div>
                </div>
              </div>
              <CompareRow label="Minutes" a={pA.mpg} b={pB.mpg} />
              <CompareRow label="Points" a={pA.ppg} b={pB.ppg} />
              <CompareRow label="Rebounds" a={pA.rpg} b={pB.rpg} />
              <CompareRow label="Assists" a={pA.apg} b={pB.apg} />
              <CompareRow label="Steals" a={pA.spg} b={pB.spg} />
              <CompareRow label="Blocks" a={pA.bpg} b={pB.bpg} />
              <CompareRow label="Turnovers" a={pA.topg} b={pB.topg} lowerIsBetter />
              <CompareRow label="FG%" a={pA.fg_pct} b={pB.fg_pct} />
              <CompareRow label="3P%" a={pA.fg3_pct} b={pB.fg3_pct} />
              <CompareRow label="Usage" a={pA.usage} b={pB.usage} />
              <CompareRow label="L5 PTS" a={pA.l5_ppg} b={pB.l5_ppg} />
            </div>
          )}

          {/* ── Player vs Team ── */}
          {compareMode === 'player-team' && pA && teamB && (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
              <div className="cs-card p-6">
                <h3 className="text-sm font-semibold text-white uppercase tracking-wider mb-1">
                  {pA.player} vs {teamB}
                </h3>
                <p className="text-[10px] font-mono text-cs-muted mb-4">
                  Season averages vs. averages against {teamB}
                </p>
                {vsTeamAvg ? (
                  <>
                    <div className="grid grid-cols-3 items-center mb-2">
                      <span className="text-[10px] text-cs-muted uppercase text-right pr-4">Season</span>
                      <span />
                      <span className="text-[10px] text-cs-muted uppercase pl-4">vs {teamB} ({vsTeamAvg.games} G)</span>
                    </div>
                    <CompareRow label="Points" a={pA.ppg} b={vsTeamAvg.pts} />
                    <CompareRow label="Rebounds" a={pA.rpg} b={vsTeamAvg.reb} />
                    <CompareRow label="Assists" a={pA.apg} b={vsTeamAvg.ast} />
                    <CompareRow label="Minutes" a={pA.mpg} b={vsTeamAvg.min} />
                  </>
                ) : (
                  <EmptyState text={`No stored games for ${pA.player} against ${teamB}.`} />
                )}
              </div>

              <div className="cs-card p-6">
                <h3 className="text-sm font-semibold text-white uppercase tracking-wider mb-4">Game Log vs {teamB}</h3>
                {vsTeamLog.length === 0 ? (
                  <EmptyState text="No matchup games stored." />
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-xs">
                      <thead>
                        <tr className="border-b border-cs-border text-cs-muted uppercase font-mono text-[10px] tracking-wider">
                          <th className="px-2 py-2">Date</th>
                          <th className="px-2 py-2 text-right">MIN</th>
                          <th className="px-2 py-2 text-right">PTS</th>
                          <th className="px-2 py-2 text-right">REB</th>
                          <th className="px-2 py-2 text-right">AST</th>
                          <th className="px-2 py-2 text-right">FG</th>
                          <th className="px-2 py-2 text-right">3PT</th>
                        </tr>
                      </thead>
                      <tbody>
                        {vsTeamLog.slice(0, 12).map((g, i) => (
                          <tr key={`${g.date}-${i}`} className={`border-b border-cs-border/20 ${i % 2 === 0 ? 'bg-cs-dark/40' : ''}`}>
                            <td className="px-2 py-2 font-mono text-cs-muted">{g.date}</td>
                            <td className="px-2 py-2 text-right font-mono text-cs-muted">{fmt(g.min)}</td>
                            <td className="px-2 py-2 text-right font-mono text-white font-bold">{fmt(g.pts)}</td>
                            <td className="px-2 py-2 text-right font-mono text-white/80">{fmt(g.reb)}</td>
                            <td className="px-2 py-2 text-right font-mono text-white/80">{fmt(g.ast)}</td>
                            <td className="px-2 py-2 text-right font-mono text-cs-muted">{g.fgm ?? '—'}/{g.fga ?? '—'}</td>
                            <td className="px-2 py-2 text-right font-mono text-cs-muted">{g.tpm ?? '—'}/{g.tpa ?? '—'}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
