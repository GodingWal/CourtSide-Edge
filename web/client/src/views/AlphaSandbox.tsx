import { Cpu, Send, Terminal, BarChart3 } from 'lucide-react';

export default function AlphaSandbox() {
  return (
    <div className="flex flex-col h-full w-full animate-fade-in">
      {/* ── Top Bar ── */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0">
        <div className="flex items-center gap-3">
          <Cpu className="w-5 h-5 text-cs-red" />
          <span className="cs-badge">Agent 12 · Quantitative Signal</span>
        </div>

        <div className="flex items-center gap-3">
          <div className="cs-card px-4 py-2 flex items-center gap-3">
            <span className="cs-stat-label">Information Coefficient</span>
            <span className="cs-stat text-white">0.042</span>
          </div>
          <div className="cs-card px-4 py-2 flex items-center gap-3">
            <span className="cs-stat-label">Backtest Win Rate</span>
            <span className="cs-stat text-cs-red">58.3%</span>
          </div>
        </div>
      </div>

      {/* ── Main Area: Chat (60%) + Output (40%) ── */}
      <div className="flex-1 flex gap-4 px-6 pb-6 min-h-0">
        {/* ── Left Column: Chat ── */}
        <div className="w-[60%] flex flex-col cs-card p-0 overflow-hidden">
          {/* Chat header */}
          <div className="flex items-center gap-2 px-5 py-3 border-b border-cs-border/30">
            <div className="w-2 h-2 rounded-full bg-cs-red shadow-glow-red-sm animate-pulse-slow" />
            <span className="text-sm font-medium text-white/70">Alpha Discovery Chat</span>
          </div>

          {/* Messages */}
          <div className="flex-1 overflow-y-auto px-5 py-5 space-y-4">
            {/* User bubble */}
            <div className="flex justify-end animate-slide-up">
              <div className="max-w-[75%] rounded-2xl rounded-br-md px-4 py-3 bg-cs-red/10 border border-cs-red/20">
                <p className="text-sm text-white leading-relaxed">
                  Analyze centers vs Liberty on back-to-back games
                </p>
              </div>
            </div>

            {/* Agent bubble */}
            <div className="flex justify-start animate-slide-up" style={{ animationDelay: '80ms' }}>
              <div className="max-w-[80%] rounded-2xl rounded-bl-md px-4 py-4 bg-cs-dark border border-cs-border/30 space-y-3">
                <div className="flex items-center gap-2 mb-1">
                  <Cpu className="w-3.5 h-3.5 text-cs-red" />
                  <span className="text-xs font-semibold text-cs-red tracking-wide uppercase">
                    Agent 12
                  </span>
                </div>
                <p className="text-sm text-white/80 leading-relaxed">
                  Running quantitative analysis on <span className="text-cs-red font-medium">CEN vs NYL</span> back-to-back
                  scenarios. Here's what the signal engine found:
                </p>
                <ul className="text-sm text-white/70 space-y-1.5 pl-1">
                  <li className="flex items-start gap-2">
                    <span className="text-cs-red mt-1 shrink-0">•</span>
                    <span><span className="text-white/90 font-medium">Fatigue factor:</span> Centers show a 12.4% decline in paint scoring efficiency on the 2nd night of B2Bs</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-cs-red mt-1 shrink-0">•</span>
                    <span><span className="text-white/90 font-medium">Pace differential:</span> Liberty push pace +3.7 possessions/game at home — compounding fatigue effects</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-cs-red mt-1 shrink-0">•</span>
                    <span><span className="text-white/90 font-medium">Rebound margin:</span> B2B centers yield −4.1 RPG vs season average, creating 2nd-chance opportunities for NYL</span>
                  </li>
                  <li className="flex items-start gap-2">
                    <span className="text-cs-red mt-1 shrink-0">•</span>
                    <span><span className="text-white/90 font-medium">Edge detected:</span> +4.2% vs closing line — signal confirmed at p=0.031</span>
                  </li>
                </ul>
                <p className="text-xs text-cs-muted pt-1">
                  ✓ Backtest validated · 47 game sample · IC 0.042
                </p>
              </div>
            </div>
          </div>

          {/* Input bar */}
          <div className="px-4 py-3 border-t border-cs-border/30">
            <div className="flex items-center gap-2">
              <input
                type="text"
                placeholder="Ask Agent 12 to discover a signal…"
                className="cs-input flex-1"
              />
              <button className="cs-btn-primary h-10 w-10 shrink-0 flex items-center justify-center !p-0">
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>

        {/* ── Right Column: Validation Output ── */}
        <div className="w-[40%] flex flex-col gap-4 min-h-0">
          {/* Code output card */}
          <div className="cs-card flex-1 flex flex-col p-0 overflow-hidden">
            <div className="flex items-center gap-2 px-5 py-3 border-b border-cs-border/30">
              <Terminal className="w-4 h-4 text-cs-red" />
              <span className="text-sm font-medium text-white/70">Validation Output</span>
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              <div className="bg-cs-black rounded-lg border border-cs-border/30 p-4 font-mono text-xs leading-6 text-white/70">
                <p><span className="text-cs-muted">{'>'}</span> Running backtest: <span className="text-white">CEN vs NYL (B2B)</span></p>
                <p><span className="text-cs-muted">{'>'}</span> Sample size: <span className="text-white">47 games</span></p>
                <p><span className="text-cs-muted">{'>'}</span> Hit rate: <span className="text-cs-red font-semibold">58.3%</span> <span className="text-cs-muted">(p=0.031)</span></p>
                <p><span className="text-cs-muted">{'>'}</span> Edge: <span className="text-cs-red-bright font-semibold">+4.2%</span> vs closing line</p>
                <p><span className="text-cs-muted">{'>'}</span> IC: <span className="text-white">0.042</span></p>
                <p className="mt-2 pt-2 border-t border-cs-border/20">
                  <span className="text-cs-muted">{'>'}</span> Status: <span className="text-cs-red font-bold tracking-wider shadow-glow-red-sm">SIGNAL_CONFIRMED</span>
                </p>
              </div>
            </div>
          </div>

          {/* Metric cards */}
          <div className="grid grid-cols-2 gap-3 shrink-0">
            <div className="cs-card px-4 py-3 text-center">
              <div className="flex items-center justify-center gap-1.5 mb-1">
                <BarChart3 className="w-3.5 h-3.5 text-cs-red" />
                <span className="cs-stat-label">Sharpe Ratio</span>
              </div>
              <span className="cs-stat text-white text-lg">1.24</span>
            </div>
            <div className="cs-card px-4 py-3 text-center">
              <div className="flex items-center justify-center gap-1.5 mb-1">
                <BarChart3 className="w-3.5 h-3.5 text-cs-red" />
                <span className="cs-stat-label">Max Drawdown</span>
              </div>
              <span className="cs-stat text-cs-red text-lg">-8.2%</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
