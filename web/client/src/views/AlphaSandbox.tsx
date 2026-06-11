import { useEffect, useRef, useState } from 'react';
import { Cpu, Send, Terminal } from 'lucide-react';
import { API_BASE } from '../lib/config';

interface ChatMessage {
  role: 'user' | 'agent' | 'error';
  text: string;
  meta?: string;
}

export default function AlphaSandbox() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [lastMeta, setLastMeta] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' });
  }, [messages, sending]);

  const send = async () => {
    const message = input.trim();
    if (!message || sending) return;
    setInput('');
    setMessages((prev) => [...prev, { role: 'user', text: message }]);
    setSending(true);
    try {
      const res = await fetch(`${API_BASE}/sandbox/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.error || `Request failed (${res.status})`);
      }
      const meta = `local Nemotron · ${data.elapsed_seconds ?? '?'}s`;
      setLastMeta(meta);
      setMessages((prev) => [...prev, { role: 'agent', text: data.reply ?? '(empty reply)', meta }]);
    } catch (err: any) {
      setMessages((prev) => [...prev, { role: 'error', text: err?.message || 'Failed to reach Agent 12.' }]);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] w-full animate-fade-in">
      {/* ── Top Bar ── */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-4 md:px-6 py-3 shrink-0">
        <div className="flex items-center gap-3">
          <Cpu className="w-5 h-5 text-cs-red" />
          <span className="cs-badge">Agent 12 · Quantitative Signal</span>
        </div>
        <div className="cs-card px-3 py-1.5 flex items-center gap-2">
          <span className="cs-stat-label">Engine</span>
          <span className="text-xs font-mono text-white/80">{lastMeta ?? 'local Nemotron (GPU)'}</span>
        </div>
      </div>

      {/* ── Main Area: stacks on mobile, splits on lg ── */}
      <div className="flex-1 flex flex-col lg:flex-row gap-4 px-4 md:px-6 pb-4 md:pb-6 min-h-0">
        {/* ── Chat ── */}
        <div className="flex-1 lg:w-[60%] flex flex-col cs-card p-0 overflow-hidden min-h-[60vh] lg:min-h-0">
          <div className="flex items-center gap-2 px-5 py-3 border-b border-cs-border/30">
            <div className="w-2 h-2 rounded-full bg-cs-red shadow-glow-red-sm animate-pulse-slow" />
            <span className="text-sm font-medium text-white/70">Alpha Discovery Chat</span>
          </div>

          {/* Messages */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 md:px-5 py-5 space-y-4">
            {messages.length === 0 && (
              <div className="h-full flex items-center justify-center text-center px-6">
                <p className="text-sm text-cs-muted leading-relaxed">
                  Ask Agent 12 about today's matchups, prop lines, pace, fatigue or referee impact.
                  <br />
                  <span className="text-xs">Answers are generated live by the Nemotron model running on the GPU server.</span>
                </p>
              </div>
            )}
            {messages.map((m, i) =>
              m.role === 'user' ? (
                <div key={i} className="flex justify-end animate-slide-up">
                  <div className="max-w-[85%] md:max-w-[75%] rounded-2xl rounded-br-md px-4 py-3 bg-cs-red/10 border border-cs-red/20">
                    <p className="text-sm text-white leading-relaxed whitespace-pre-wrap">{m.text}</p>
                  </div>
                </div>
              ) : (
                <div key={i} className="flex justify-start animate-slide-up">
                  <div
                    className={`max-w-[90%] md:max-w-[80%] rounded-2xl rounded-bl-md px-4 py-4 border space-y-2 ${
                      m.role === 'error'
                        ? 'bg-red-950/40 border-red-500/30'
                        : 'bg-cs-dark border-cs-border/30'
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <Cpu className="w-3.5 h-3.5 text-cs-red" />
                      <span className="text-xs font-semibold text-cs-red tracking-wide uppercase">
                        {m.role === 'error' ? 'Engine Error' : 'Agent 12'}
                      </span>
                    </div>
                    <p className="text-sm text-white/80 leading-relaxed whitespace-pre-wrap">{m.text}</p>
                    {m.meta && <p className="text-[10px] text-cs-muted pt-1">✓ {m.meta}</p>}
                  </div>
                </div>
              )
            )}
            {sending && (
              <div className="flex justify-start">
                <div className="rounded-2xl rounded-bl-md px-4 py-3 bg-cs-dark border border-cs-border/30">
                  <span className="text-sm text-cs-muted animate-pulse">Agent 12 is analyzing…</span>
                </div>
              </div>
            )}
          </div>

          {/* Input bar */}
          <div className="px-3 md:px-4 py-3 border-t border-cs-border/30">
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && send()}
                disabled={sending}
                placeholder="Ask Agent 12 to discover a signal…"
                className="cs-input flex-1"
              />
              <button
                onClick={send}
                disabled={sending || !input.trim()}
                className="cs-btn-primary h-10 w-10 shrink-0 flex items-center justify-center !p-0 disabled:opacity-40"
                aria-label="Send"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>

        {/* ── Session log ── */}
        <div className="lg:w-[40%] flex flex-col gap-4 min-h-0">
          <div className="cs-card flex-1 flex flex-col p-0 overflow-hidden min-h-[180px]">
            <div className="flex items-center gap-2 px-5 py-3 border-b border-cs-border/30">
              <Terminal className="w-4 h-4 text-cs-red" />
              <span className="text-sm font-medium text-white/70">Session Log</span>
            </div>
            <div className="flex-1 overflow-y-auto p-4">
              <div className="bg-cs-black rounded-lg border border-cs-border/30 p-4 font-mono text-xs leading-6 text-white/70 space-y-1">
                {messages.length === 0 ? (
                  <p className="text-cs-muted">{'>'} awaiting first query…</p>
                ) : (
                  messages.map((m, i) => (
                    <p key={i}>
                      <span className="text-cs-muted">{'>'}</span>{' '}
                      {m.role === 'user' ? (
                        <span className="text-white">{m.text.slice(0, 70)}</span>
                      ) : m.role === 'error' ? (
                        <span className="text-red-400">ERROR: {m.text.slice(0, 70)}</span>
                      ) : (
                        <span className="text-cs-red">reply · {m.meta ?? 'ok'}</span>
                      )}
                    </p>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
