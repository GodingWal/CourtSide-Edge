/*
  AlertFeed — Formatted real-time alert stream.
  Replaces raw JSON.stringify() dumps with human-readable cards.
  Color-coded by channel type. Collapsible raw JSON for debug.
*/

import { useState, useCallback } from 'react';
import {
  Zap,
  Newspaper,
  Activity,
  AlertTriangle,
  TrendingUp,
  Info,
  ChevronDown,
  ChevronRight,
  BrainCircuit,
  X,
  Radio,
} from 'lucide-react';

interface StreamMessage {
  channel: string;
  message: Record<string, unknown>;
}

interface AlertFeedProps {
  messages: StreamMessage[];
  maxHeight?: string;
}

/* ── Channel formatting rules ── */

interface ChannelFmt {
  icon: React.ElementType;
  label: string;
  cardClass: string;
  format: (msg: Record<string, unknown>) => React.ReactNode;
}

function EdgeValue(v: unknown): string {
  const n = typeof v === 'number' ? v : parseFloat(String(v));
  if (Number.isNaN(n)) return '—';
  return n > 0 ? `+${n.toFixed(1)}%` : `${n.toFixed(1)}%`;
}

function fmtNum(v: unknown, digits = 1): string {
  const n = typeof v === 'number' ? v : parseFloat(String(v));
  if (Number.isNaN(n)) return '—';
  return n.toFixed(digits);
}

function fmtMoney(v: unknown): string {
  const n = typeof v === 'number' ? v : parseFloat(String(v));
  if (Number.isNaN(n)) return '—';
  const sign = n >= 0 ? '+' : '-';
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

const channelFormats: Record<string, ChannelFmt> = {
  channel_steam_alerts: {
    icon: Zap,
    label: 'Steam Alert',
    cardClass: 'cs-alert-card-steam',
    format: (msg) => (
      <div className="space-y-0.5">
        <span className="text-xs font-bold text-emerald-300">
          {msg.player ?? msg.player_name ?? 'Market'}
        </span>
        <p className="text-[10px] text-emerald-400/70 leading-relaxed">
          {msg.reason ?? msg.message ?? 'Sharp liquidity movement detected'}
        </p>
        {(msg.delta || msg.odds_delta) && (
          <div className="flex gap-3 text-[10px] font-mono text-emerald-400/60 mt-1">
            {msg.delta && <span>shift: {msg.delta}</span>}
            {msg.odds_delta && <span>odds: {msg.odds_delta}</span>}
          </div>
        )}
      </div>
    ),
  },
  channel_roster_updates: {
    icon: Newspaper,
    label: 'Roster News',
    cardClass: 'cs-alert-card-news',
    format: (msg) => (
      <div className="space-y-0.5">
        <span className="text-xs font-bold text-blue-300">
          {msg.player_name ?? msg.player ?? 'Team Update'}
        </span>
        <p className="text-[10px] text-blue-400/70 leading-relaxed">
          {msg.injury_status ? `Status: ${msg.injury_status}` : msg.message ?? 'Roster update'}
        </p>
        {msg.team && (
          <span className="text-[9px] font-mono text-blue-400/50">{msg.team}</span>
        )}
      </div>
    ),
  },
  channel_referee_context: {
    icon: Info,
    label: 'Referee',
    cardClass: 'cs-alert-card-news',
    format: (msg) => (
      <div className="space-y-0.5">
        <span className="text-xs font-bold text-blue-300">
          Ref: {Array.isArray(msg.crew) ? msg.crew.join(', ') : msg.crew ?? 'Assignment'}
        </span>
        {msg.tendencies && (
          <p className="text-[10px] text-blue-400/70 font-mono">
            pace: {fmtNum((msg.tendencies as Record<string, unknown>)?.pace_effect)} ·
            fouls/40: {fmtNum((msg.tendencies as Record<string, unknown>)?.fouls_per_40)}
          </p>
        )}
      </div>
    ),
  },
  channel_sentiment_context: {
    icon: BrainCircuit,
    label: 'Sentiment',
    cardClass: 'cs-alert-card-info',
    format: (msg) => (
      <div className="space-y-0.5">
        <span className="text-xs font-bold text-blue-300">{msg.team ?? 'Team'}</span>
        <div className="flex gap-3 text-[10px] font-mono text-blue-400/70">
          {msg.fatigue_penalty !== undefined && (
            <span>fatigue: {fmtNum(msg.fatigue_penalty)}</span>
          )}
          {msg.motivation_score !== undefined && (
            <span>motivation: {fmtNum(msg.motivation_score)}</span>
          )}
        </div>
      </div>
    ),
  },
  channel_live_odds: {
    icon: TrendingUp,
    label: 'Odds Update',
    cardClass: 'cs-alert-card-edge',
    format: (msg) => (
      <div className="space-y-0.5">
        <span className="text-xs font-bold text-amber-300">
          {msg.player ?? msg.player_name ?? 'Market'}
        </span>
        <p className="text-[10px] text-amber-400/70 font-mono">
          {msg.stat ?? msg.market ?? 'Line'}: {msg.line ?? msg.book_line ?? '—'}
          {msg.odds ? ` @ ${msg.odds}` : ''}
        </p>
      </div>
    ),
  },
  channel_true_projections: {
    icon: Activity,
    label: 'Projection',
    cardClass: 'cs-alert-card-edge',
    format: (msg) => (
      <div className="space-y-0.5">
        <span className="text-xs font-bold text-amber-300">
          {msg.player ?? msg.player_name ?? 'Player'}
        </span>
        <p className="text-[10px] text-amber-400/70 font-mono">
          {msg.stat ?? 'proj'}: {fmtNum(msg.projection ?? msg.line)} ·
          confidence: {fmtNum(msg.confidence ?? 0.7)}
        </p>
      </div>
    ),
  },
  channel_meta_analysis: {
    icon: BrainCircuit,
    label: 'Meta-Agent',
    cardClass: 'cs-alert-card-steam',
    format: (msg) => (
      <div className="space-y-0.5">
        <span className="text-xs font-bold text-emerald-300">
          Mode: {String((msg.confidence as Record<string, unknown>)?.mode ?? 'normal').toUpperCase()}
        </span>
        <p className="text-[10px] text-emerald-400/70">
          Overall: {fmtNum((msg.confidence as Record<string, unknown>)?.overall_score ?? 0, 0)}% ·{' '}
          {(msg.confidence as Record<string, unknown>)?.reason as string ?? 'System analysis'}
        </p>
      </div>
    ),
  },
  default: {
    icon: Radio,
    label: 'Signal',
    cardClass: 'cs-alert-card border-l-2 border-l-cs-muted',
    format: (msg) => (
      <div className="space-y-0.5">
        <span className="text-xs font-bold text-gray-300">
          {msg.player_name ?? msg.player ?? msg.player ?? 'Signal'}
        </span>
        <p className="text-[10px] text-gray-400/70 font-mono truncate">
          {msg.message ? String(msg.message).slice(0, 80) : 'Data received'}
        </p>
      </div>
    ),
  },
};

/* ── Single alert card ── */

function AlertCard({ msg, index }: { msg: StreamMessage; index: number }) {
  const [expanded, setExpanded] = useState(false);
  const fmt = channelFormats[msg.channel] ?? channelFormats.default;
  const Icon = fmt.icon;

  return (
    <div
      className={`${fmt.cardClass} animate-fade-in`}
      style={{ animationDelay: `${Math.min(index * 30, 300)}ms` }}
    >
      <div className="flex items-start gap-2.5">
        {/* Icon */}
        <div className="shrink-0 mt-0.5">
          <Icon className="w-3.5 h-3.5 opacity-70" style={{ color: 'inherit' }} />
        </div>

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Header */}
          <div className="flex items-center gap-1.5 mb-1">
            <span className="text-[9px] font-bold uppercase tracking-wider text-cs-muted">
              {fmt.label}
            </span>
            <span className="text-[9px] font-mono text-cs-muted/40">
              {new Date().toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
            </span>
          </div>

          {/* Formatted body */}
          {fmt.format(msg.message as Record<string, unknown>)}

          {/* Expand for raw JSON */}
          <button
            onClick={() => setExpanded(!expanded)}
            className="flex items-center gap-1 mt-2 text-[9px] text-cs-muted/50 hover:text-cs-muted transition-colors"
          >
            {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            Raw
          </button>

          {expanded && (
            <pre className="mt-1.5 text-[9px] text-cs-muted/40 font-mono leading-relaxed overflow-auto max-h-32 bg-cs-black/50 rounded-lg p-2">
              {JSON.stringify(msg.message, null, 2)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Empty state ── */

function EmptyAlerts() {
  return (
    <div className="flex flex-col items-center justify-center h-full py-12 text-center animate-fade-in">
      {/* Radar pulse */}
      <div className="relative flex items-center justify-center w-16 h-16 mb-4">
        <span className="absolute inline-flex h-full w-full rounded-full bg-cs-red/20 animate-ping" />
        <span className="absolute inline-flex h-10 w-10 rounded-full bg-cs-red/10 animate-pulse-slow" />
        <Radio className="relative z-10 w-6 h-6 text-cs-red" />
      </div>
      <p className="text-sm text-cs-muted font-medium animate-pulse-slow">
        Scanning markets…
      </p>
      <p className="text-xs text-cs-muted/50 mt-1">Waiting for live signals</p>
    </div>
  );
}

/* ── Main component ── */

export default function AlertFeed({ messages, maxHeight = 'max-h-[420px]' }: AlertFeedProps) {
  const [filter, setFilter] = useState<string | null>(null);

  const channels = [...new Set(messages.map(m => m.channel))];
  const filtered = filter ? messages.filter(m => m.channel === filter) : messages;

  const clearFilter = useCallback(() => setFilter(null), []);

  return (
    <div className="flex flex-col h-full">
      {/* Channel filter bar */}
      {messages.length > 0 && (
        <div className="flex items-center gap-1.5 px-4 pt-4 pb-2 flex-wrap">
          <button
            onClick={clearFilter}
            className={`text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded transition-colors ${
              filter === null ? 'bg-cs-red/20 text-cs-red' : 'text-cs-muted hover:text-white'
            }`}
          >
            All
          </button>
          {channels.slice(0, 6).map(ch => {
            const fmt = channelFormats[ch] ?? channelFormats.default;
            const Icon = fmt.icon;
            return (
              <button
                key={ch}
                onClick={() => setFilter(filter === ch ? null : ch)}
                className={`flex items-center gap-1 text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 rounded transition-colors ${
                  filter === ch ? 'bg-cs-red/20 text-cs-red' : 'text-cs-muted hover:text-white'
                }`}
                title={ch}
              >
                <Icon className="w-2.5 h-2.5" />
                <span className="hidden sm:inline">{fmt.label}</span>
              </button>
            );
          })}
          {filter && (
            <button onClick={clearFilter} className="ml-auto text-cs-muted hover:text-red-400 transition-colors">
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      )}

      {/* Alert list */}
      <div className={`flex-1 overflow-y-auto p-4 space-y-2 ${maxHeight} scrollbar-thin scrollbar-thumb-cs-border scrollbar-track-transparent`}>
        {filtered.length === 0 ? (
          messages.length === 0 ? <EmptyAlerts /> : (
            <p className="text-xs text-cs-muted font-mono text-center py-8">No alerts for this channel.</p>
          )
        ) : (
          filtered.map((m, i) => (
            <AlertCard key={i} msg={m} index={i} />
          ))
        )}
      </div>
    </div>
  );
}
