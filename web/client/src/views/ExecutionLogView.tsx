import { Receipt, Server, ArrowUpRight } from 'lucide-react';

export default function ExecutionLogView() {
  return (
    <div className="flex flex-col h-full w-full animate-fade-in p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Receipt className="w-5 h-5 text-cs-neon-cyan" />
            <h1 className="text-xl font-bold text-white tracking-wide">Tier 4: Execution Log</h1>
          </div>
          <p className="text-cs-muted text-sm">Immutable audit trail of Execution Agent slips and routing latency.</p>
        </div>
        
        <div className="flex gap-4">
          <div className="cs-card px-4 py-2 flex items-center gap-3 border-cs-neon-cyan/20 bg-cs-neon-cyan-glow/5">
            <span className="text-[10px] text-cs-neon-cyan font-bold uppercase tracking-widest">Avg Latency</span>
            <span className="text-lg font-mono text-white">142ms</span>
          </div>
          <div className="cs-card px-4 py-2 flex items-center gap-3 border-cs-neon-cyan/20 bg-cs-neon-cyan-glow/5">
            <span className="text-[10px] text-cs-neon-cyan font-bold uppercase tracking-widest">CLV Beaten</span>
            <span className="text-lg font-mono text-white">68.4%</span>
          </div>
        </div>
      </div>

      <div className="flex-1 cs-card p-0 overflow-hidden flex flex-col border-cs-neon-cyan/20 shadow-glow-cyan-sm h-[calc(100vh-140px)]">
        <div className="px-6 py-4 bg-cs-dark border-b border-cs-border/50 flex items-center gap-4">
          <Server className="w-4 h-4 text-cs-neon-cyan" />
          <span className="text-xs font-bold text-white uppercase tracking-widest">Execution Agent Routing DB</span>
        </div>

        <div className="flex-1 overflow-y-auto">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-cs-black/40 text-[10px] uppercase tracking-widest text-cs-muted">
              <tr>
                <th className="px-6 py-4 font-semibold">Slip ID</th>
                <th className="px-6 py-4 font-semibold">Timestamp</th>
                <th className="px-6 py-4 font-semibold">Selection</th>
                <th className="px-6 py-4 font-semibold">Odds</th>
                <th className="px-6 py-4 font-semibold">Wager</th>
                <th className="px-6 py-4 font-semibold">Route</th>
                <th className="px-6 py-4 font-semibold">Status</th>
              </tr>
            </thead>
            <tbody className="font-mono text-xs divide-y divide-cs-border/30">
              {/* Mock Row 1 */}
              <tr className="hover:bg-white/5 transition-colors">
                <td className="px-6 py-4 text-cs-muted">#EX-9042</td>
                <td className="px-6 py-4 text-white/80">14:02:12.441</td>
                <td className="px-6 py-4 text-white font-sans font-medium">A. Wilson PTS 22.5 O</td>
                <td className="px-6 py-4 text-cs-neon-cyan">-110</td>
                <td className="px-6 py-4 text-white">$295.10</td>
                <td className="px-6 py-4">
                  <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-cs-dark border border-cs-border text-cs-muted">
                    Pinnacle <ArrowUpRight className="w-3 h-3 text-cs-emerald" />
                  </span>
                </td>
                <td className="px-6 py-4">
                  <span className="text-cs-emerald bg-cs-emerald/10 px-2 py-1 rounded">FILLED</span>
                </td>
              </tr>

              {/* Mock Row 2 */}
              <tr className="hover:bg-white/5 transition-colors">
                <td className="px-6 py-4 text-cs-muted">#EX-9041</td>
                <td className="px-6 py-4 text-white/80">12:11:43.012</td>
                <td className="px-6 py-4 text-white font-sans font-medium">S. Ionescu 3PT 2.5 O</td>
                <td className="px-6 py-4 text-cs-neon-cyan">+115</td>
                <td className="px-6 py-4 text-white">$1,229.60</td>
                <td className="px-6 py-4">
                  <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-cs-dark border border-cs-border text-cs-muted">
                    DraftKings <ArrowUpRight className="w-3 h-3 text-cs-emerald" />
                  </span>
                </td>
                <td className="px-6 py-4">
                  <span className="text-cs-emerald bg-cs-emerald/10 px-2 py-1 rounded">FILLED</span>
                </td>
              </tr>

              {/* Mock Row 3 */}
              <tr className="hover:bg-white/5 transition-colors opacity-60">
                <td className="px-6 py-4 text-cs-muted">#EX-9040</td>
                <td className="px-6 py-4 text-white/80">10:45:01.992</td>
                <td className="px-6 py-4 text-white font-sans font-medium">B. Stewart REB 9.5 O</td>
                <td className="px-6 py-4 text-cs-neon-cyan">-120</td>
                <td className="px-6 py-4 text-white">$450.00</td>
                <td className="px-6 py-4">
                  <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded bg-cs-dark border border-cs-border text-cs-muted">
                    FanDuel <span className="text-cs-red font-sans text-[10px]">TIMEOUT</span>
                  </span>
                </td>
                <td className="px-6 py-4">
                  <span className="text-cs-red bg-cs-red/10 px-2 py-1 rounded">FAILED</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
