import { useEffect, useState } from 'react';
import { Database, AlertTriangle, Users, Shield, Activity } from 'lucide-react';
import { API_BASE } from '../lib/config';

/* ── Types ── */

type InjuryStatus = 'out' | 'questionable' | 'active';

interface InjuryItem {
  player: string;
  team: string;
  status: InjuryStatus;
  injury: string;
}

interface SentimentItem {
  team: string;
  fatigue: number; // 0-100 derived from fatigue_penalty (-1..0)
  motivation: number; // 0-100 derived from motivation_score (0..1)
}

interface RefereeItem {
  crew: string;
  game: string;
  foulsPer40: number | null;
  paceEffect: number | null;
  ouTendency: string;
}

/* ── Mapping real agent events → view models ── */

function mapInjuryStatus(status: string): InjuryStatus {
  const s = String(status).toUpperCase();
  if (s === 'OUT') return 'out';
  if (s === 'ACTIVE') return 'active';
  return 'questionable'; // DOUBTFUL / QUESTIONABLE / PROBABLE
}

function parseEvents(events: any[]) {
  const injuries: InjuryItem[] = [];
  const sentiment: SentimentItem[] = [];
  const referees: RefereeItem[] = [];
  const seenPlayers = new Set<string>();
  const seenTeams = new Set<string>();
  const seenCrews = new Set<string>();

  for (const ev of events) {
    const p = ev.payload;
    if (!p || typeof p !== 'object') continue;

    if (ev.channel === 'channel_roster_updates' && p.player_name) {
      if (seenPlayers.has(p.player_name)) continue;
      seenPlayers.add(p.player_name);
      injuries.push({
        player: p.player_name,
        team: p.team ?? '—',
        status: mapInjuryStatus(p.injury_status ?? 'QUESTIONABLE'),
        injury: p.injury_status
          ? `${p.injury_status}${p.game_impact && p.game_impact !== 'NONE' ? ` · impact: ${p.game_impact}` : ''}`
          : 'Status update',
      });
    } else if (ev.channel === 'channel_sentiment_context') {
      const team = p.team ?? 'UNKNOWN';
      if (seenTeams.has(team)) continue;
      seenTeams.add(team);
      sentiment.push({
        team,
        fatigue: Math.round(Math.min(1, Math.abs(p.fatigue_penalty ?? 0)) * 100),
        motivation: Math.round(Math.min(1, Math.max(0, p.motivation_score ?? 0)) * 100),
      });
    } else if (ev.channel === 'channel_referee_context' && p.crew) {
      // Agent 5 publishes the crew as a list of official names.
      const crew = Array.isArray(p.crew) ? p.crew.join(', ') : String(p.crew);
      if (seenCrews.has(crew)) continue;
      seenCrews.add(crew);
      const t = p.tendencies ?? {};
      referees.push({
        crew,
        game: p.game_id ?? '—',
        foulsPer40: t.fouls_per_40 ?? null,
        paceEffect: t.pace_effect ?? null,
        ouTendency: String(t.ou_tendency ?? t.ou_hit_rate ?? '—').replace(/_/g, ' '),
      });
    }
  }
  return { injuries: injuries.slice(0, 8), sentiment: sentiment.slice(0, 6), referees: referees.slice(0, 6) };
}

/* ── Helpers ── */

const statusColors: Record<InjuryStatus, string> = {
  out: 'bg-red-500',
  questionable: 'bg-yellow-500',
  active: 'bg-emerald-500',
};

const statusGlow: Record<InjuryStatus, string> = {
  out: 'shadow-[0_0_6px_rgba(239,68,68,0.6)]',
  questionable: 'shadow-[0_0_6px_rgba(234,179,8,0.6)]',
  active: 'shadow-[0_0_6px_rgba(34,197,94,0.6)]',
};

function fatigueColor(score: number): string {
  if (score > 70) return 'bg-red-500';
  if (score > 40) return 'bg-yellow-500';
  return 'bg-emerald-500';
}

function EmptyState({ text }: { text: string }) {
  return (
    <p className="text-xs text-cs-muted font-mono text-center py-8">{text}</p>
  );
}

/* ── Ticker (built from live injuries) ── */

function Ticker({ injuries }: { injuries: InjuryItem[] }) {
  const dot = (s: InjuryStatus) => (s === 'out' ? '🔴' : s === 'questionable' ? '🟡' : '🟢');
  const tickerText = injuries.length
    ? injuries.map((i) => `${dot(i.status)} ${i.player} — ${i.injury}`).join('  ·  ')
    : 'Awaiting live injury intel from Agent 2…';

  return (
    <div className="relative w-full overflow-hidden rounded-xl bg-cs-dark/80 border border-cs-border/40 mb-6">
      <div className="flex items-center">
        <div className="shrink-0 flex items-center gap-1.5 px-4 py-2.5 bg-cs-red/10 border-r border-cs-border/40">
          <AlertTriangle className="w-3.5 h-3.5 text-cs-red" />
          <span className="text-[10px] font-bold text-cs-red uppercase tracking-widest">Live</span>
        </div>
        <div className="overflow-hidden whitespace-nowrap flex-1">
          <div className="inline-block animate-[marquee_28s_linear_infinite] pl-4">
            <span className="text-xs text-gray-300 tracking-wide">
              {tickerText}
              {'    '}
              {tickerText}
            </span>
          </div>
        </div>
      </div>

      {/* Inline keyframe for marquee */}
      <style>{`
        @keyframes marquee {
          0% { transform: translateX(0); }
          100% { transform: translateX(-50%); }
        }
      `}</style>
    </div>
  );
}

/* ── Component ── */

export default function IntelligenceFeed() {
  const [injuries, setInjuries] = useState<InjuryItem[]>([]);
  const [sentiment, setSentiment] = useState<SentimentItem[]>([]);
  const [referees, setReferees] = useState<RefereeItem[]>([]);

  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const res = await fetch(`${API_BASE}/events/recent`);
        if (!res.ok) return;
        const events = await res.json();
        const parsed = parseEvents(events);
        setInjuries(parsed.injuries);
        setSentiment(parsed.sentiment);
        setReferees(parsed.referees);
      } catch (err) {
        console.error('Failed to fetch intelligence events:', err);
      }
    };
    fetchEvents();
    const interval = setInterval(fetchEvents, 15000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div className="min-h-screen bg-cs-black p-4 md:p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="w-10 h-10 rounded-xl bg-cs-red/10 border border-cs-red/20 flex items-center justify-center shadow-glow-red-sm">
          <Database className="w-5 h-5 text-cs-red" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Intelligence Feed</h1>
          <p className="text-sm text-cs-muted">Injury intel · Fatigue · Referee tendencies</p>
        </div>
      </div>

      {/* Ticker */}
      <Ticker injuries={injuries} />

      {/* 3 Column Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Col 1: Live Injury Intel */}
        <div className="cs-card p-6 animate-slide-up">
          <div className="flex items-center gap-2 mb-5">
            <Activity className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Live Injury Intel</h2>
          </div>

          <div className="space-y-1">
            {injuries.length === 0 ? (
              <EmptyState text="No live injury intel yet — Agent 2 publishes here." />
            ) : (
              injuries.map((item) => (
                <div
                  key={item.player}
                  className="flex items-center gap-3 p-3 rounded-xl hover:bg-white/[0.02] transition-colors"
                >
                  <span
                    className={`shrink-0 w-2.5 h-2.5 rounded-full ${statusColors[item.status]} ${statusGlow[item.status]}`}
                  />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-white truncate">{item.player}</span>
                      <span className="shrink-0 text-[10px] font-bold text-cs-muted bg-cs-border/30 px-1.5 py-0.5 rounded">
                        {item.team}
                      </span>
                    </div>
                    <p className="text-xs text-cs-muted mt-0.5 truncate">{item.injury}</p>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Col 2: Motivation & Fatigue */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '80ms' }}>
          <div className="flex items-center gap-2 mb-5">
            <Users className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">
              Motivation & Fatigue
            </h2>
          </div>

          <div className="space-y-4">
            {sentiment.length === 0 ? (
              <EmptyState text="No sentiment context yet — Agent 9 publishes here." />
            ) : (
              sentiment.map((t) => (
                <div
                  key={t.team}
                  className="bg-cs-black/60 border border-cs-border/40 rounded-xl p-4 space-y-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-white">{t.team}</span>
                  </div>

                  {/* Fatigue bar */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[10px] text-cs-muted uppercase tracking-wider">Fatigue</span>
                      <span className="text-xs font-bold text-white">{t.fatigue}/100</span>
                    </div>
                    <div className="h-1.5 w-full bg-cs-border/30 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-700 ${fatigueColor(t.fatigue)}`}
                        style={{ width: `${t.fatigue}%` }}
                      />
                    </div>
                  </div>

                  {/* Motivation bar */}
                  <div>
                    <div className="flex items-center justify-between mb-1.5">
                      <span className="text-[10px] text-cs-muted uppercase tracking-wider">Motivation</span>
                      <span className="text-xs font-bold text-white">{t.motivation}/100</span>
                    </div>
                    <div className="h-1.5 w-full bg-cs-border/30 rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-cs-red to-cs-red-bright transition-all duration-700"
                        style={{ width: `${t.motivation}%` }}
                      />
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Col 3: Referee Profiles */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '160ms' }}>
          <div className="flex items-center gap-2 mb-5">
            <Shield className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Referee Profiles</h2>
          </div>

          <div className="space-y-4">
            {referees.length === 0 ? (
              <EmptyState text="No referee context yet — Agent 5 publishes here." />
            ) : (
              referees.map((ref) => (
                <div
                  key={ref.crew}
                  className="bg-cs-black/60 border border-cs-border/40 rounded-xl p-4 space-y-3"
                >
                  <div className="flex items-center justify-between">
                    <span className="text-sm font-semibold text-white">{ref.crew}</span>
                    <span className="text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-full border text-cs-muted bg-cs-border/10 border-cs-border/40">
                      {ref.game}
                    </span>
                  </div>

                  <div className="grid grid-cols-3 gap-2">
                    <div className="text-center">
                      <div className="text-lg font-black text-white">{ref.foulsPer40 ?? '—'}</div>
                      <div className="text-[10px] text-cs-muted uppercase tracking-wider">Fouls/40</div>
                    </div>
                    <div className="text-center">
                      <div className="text-lg font-black text-white">
                        {ref.paceEffect !== null ? (ref.paceEffect > 0 ? `+${ref.paceEffect}` : ref.paceEffect) : '—'}
                      </div>
                      <div className="text-[10px] text-cs-muted uppercase tracking-wider">Pace</div>
                    </div>
                    <div className="text-center">
                      <div className="text-sm font-black text-white leading-6">{ref.ouTendency}</div>
                      <div className="text-[10px] text-cs-muted uppercase tracking-wider">O/U Lean</div>
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
