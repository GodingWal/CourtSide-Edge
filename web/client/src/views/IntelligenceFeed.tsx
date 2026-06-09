import { Database, AlertTriangle, Users, Shield, Activity } from 'lucide-react';

/* ── Mock Data ── */

type InjuryStatus = 'out' | 'questionable' | 'active';

interface InjuryItem {
  player: string;
  team: string;
  status: InjuryStatus;
  injury: string;
}

const injuries: InjuryItem[] = [
  { player: "A'ja Wilson", team: 'LVA', status: 'out', injury: 'Right ankle sprain' },
  { player: 'Alyssa Thomas', team: 'CON', status: 'questionable', injury: 'Knee soreness' },
  { player: 'Kelsey Plum', team: 'LVA', status: 'questionable', injury: 'Rest day — load management' },
  { player: 'Breanna Stewart', team: 'NYL', status: 'active', injury: 'Full practice participant' },
  { player: 'Sabrina Ionescu', team: 'NYL', status: 'active', injury: 'Cleared — no limitations' },
];

interface TeamFatigue {
  team: string;
  fatigue: number;
  motivation: string;
  isB2B: boolean;
}

const teams: TeamFatigue[] = [
  { team: 'Las Vegas Aces', fatigue: 78, motivation: 'Playoff push', isB2B: true },
  { team: 'New York Liberty', fatigue: 45, motivation: 'Playoff push', isB2B: false },
  { team: 'Indiana Fever', fatigue: 32, motivation: 'Rebuild', isB2B: false },
  { team: 'Los Angeles Sparks', fatigue: 85, motivation: 'Eliminated', isB2B: true },
];

interface Referee {
  name: string;
  games: number;
  avgFouls: number;
  ouTendency: number;
  style: string;
}

const referees: Referee[] = [
  { name: 'Maj Forsberg', games: 142, avgFouls: 22.4, ouTendency: 56, style: 'Tight whistle' },
  { name: 'Danielle Scott', games: 198, avgFouls: 18.1, ouTendency: 48, style: 'Player-friendly' },
  { name: 'Isaac Barnett', games: 87, avgFouls: 20.7, ouTendency: 52, style: 'Tight whistle' },
];

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

function motivationColor(tag: string): string {
  switch (tag) {
    case 'Playoff push':
      return 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30';
    case 'Eliminated':
      return 'text-red-400 bg-red-500/10 border-red-500/30';
    default:
      return 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30';
  }
}

/* ── Ticker ── */

const tickerText =
  "🔴 A. Thomas (Q) — Knee soreness  ·  🟡 K. Plum (P) — Rest day  ·  🟢 B. Stewart — Full practice  ·  🔴 A'ja Wilson (O) — Right ankle  ·  🟢 S. Ionescu — Cleared";

function Ticker() {
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
  return (
    <div className="min-h-screen bg-cs-black p-6 animate-fade-in">
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
      <Ticker />

      {/* 3 Column Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Col 1: Live Injury Intel */}
        <div className="cs-card p-6 animate-slide-up">
          <div className="flex items-center gap-2 mb-5">
            <Activity className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Live Injury Intel</h2>
          </div>

          <div className="space-y-1">
            {injuries.map((item) => (
              <div
                key={item.player}
                className="flex items-center gap-3 p-3 rounded-xl hover:bg-white/[0.02] transition-colors"
              >
                {/* Status dot */}
                <span
                  className={`shrink-0 w-2.5 h-2.5 rounded-full ${statusColors[item.status]} ${statusGlow[item.status]}`}
                />

                {/* Info */}
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
            ))}
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
            {teams.map((t) => (
              <div
                key={t.team}
                className="bg-cs-black/60 border border-cs-border/40 rounded-xl p-4 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-white">{t.team}</span>
                  {t.isB2B && (
                    <span className="text-[10px] font-bold text-orange-400 bg-orange-500/10 border border-orange-500/30 px-2 py-0.5 rounded-full uppercase tracking-wider">
                      B2B
                    </span>
                  )}
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

                {/* Motivation tag */}
                <span
                  className={`inline-block text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-full border ${motivationColor(t.motivation)}`}
                >
                  {t.motivation}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* Col 3: Referee Profiles */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '160ms' }}>
          <div className="flex items-center gap-2 mb-5">
            <Shield className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Referee Profiles</h2>
          </div>

          <div className="space-y-4">
            {referees.map((ref) => (
              <div
                key={ref.name}
                className="bg-cs-black/60 border border-cs-border/40 rounded-xl p-4 space-y-3"
              >
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-white">{ref.name}</span>
                  <span
                    className={`text-[10px] font-bold uppercase tracking-wider px-2.5 py-1 rounded-full border ${
                      ref.style === 'Tight whistle'
                        ? 'text-red-400 bg-red-500/10 border-red-500/30'
                        : 'text-emerald-400 bg-emerald-500/10 border-emerald-500/30'
                    }`}
                  >
                    {ref.style}
                  </span>
                </div>

                <div className="grid grid-cols-3 gap-2">
                  <div className="text-center">
                    <div className="text-lg font-black text-white">{ref.games}</div>
                    <div className="text-[10px] text-cs-muted uppercase tracking-wider">Games</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-black text-white">{ref.avgFouls}</div>
                    <div className="text-[10px] text-cs-muted uppercase tracking-wider">Fouls/G</div>
                  </div>
                  <div className="text-center">
                    <div className="text-lg font-black text-white">{ref.ouTendency}%</div>
                    <div className="text-[10px] text-cs-muted uppercase tracking-wider">O/U %</div>
                  </div>
                </div>

                {/* Visual tendency bar */}
                <div className="h-1 w-full bg-cs-border/30 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-cs-red to-cs-red-bright transition-all duration-700"
                    style={{ width: `${ref.ouTendency}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
