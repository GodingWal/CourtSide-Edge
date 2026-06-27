import { PieChart, TrendingUp, ShieldAlert, CheckCircle2 } from 'lucide-react';

export default function RiskDeskView() {
  return (
    <div className="flex flex-col h-full w-full animate-fade-in p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <PieChart className="w-5 h-5 text-cs-emerald" />
            <h1 className="text-xl font-bold text-white tracking-wide">Tier 3: Risk Desk</h1>
          </div>
          <p className="text-cs-muted text-sm">Portfolio Manager agent tracking Kelly constraints and global exposure.</p>
        </div>
        
        <div className="flex gap-4">
          <div className="cs-card px-4 py-2 flex items-center gap-3 border-cs-emerald/20 bg-cs-emerald/5">
            <span className="text-[10px] text-cs-emerald font-bold uppercase tracking-widest">Global Bankroll</span>
            <span className="text-lg font-mono text-white">$24,592.11</span>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 h-[calc(100vh-140px)]">
        {/* Constraints Panel */}
        <div className="col-span-1 flex flex-col gap-6">
          <div className="cs-card p-5 border-cs-border">
            <h3 className="text-xs font-bold text-white uppercase tracking-widest mb-4 flex items-center gap-2">
               <ShieldAlert className="w-4 h-4 text-cs-emerald" />
               Agent Constraints
            </h3>
            
            <div className="space-y-5">
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-cs-muted">Kelly Multiplier</span>
                  <span className="font-mono text-white">0.5x (Half-Kelly)</span>
                </div>
                <div className="w-full bg-cs-dark h-1.5 rounded-full overflow-hidden">
                  <div className="bg-cs-emerald h-full w-1/2 rounded-full" />
                </div>
              </div>
              
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-cs-muted">Max Wager (Single)</span>
                  <span className="font-mono text-white">5.0%</span>
                </div>
                <div className="w-full bg-cs-dark h-1.5 rounded-full overflow-hidden">
                  <div className="bg-cs-emerald h-full w-[5%] rounded-full" />
                </div>
              </div>

              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-cs-muted">Max Daily Exposure</span>
                  <span className="font-mono text-white">20.0%</span>
                </div>
                <div className="w-full bg-cs-dark h-1.5 rounded-full overflow-hidden">
                  <div className="bg-cs-emerald h-full w-1/5 rounded-full" />
                </div>
              </div>
            </div>
          </div>

          <div className="cs-card p-5 flex-1 border-cs-border">
            <h3 className="text-xs font-bold text-white uppercase tracking-widest mb-4">Risk Log</h3>
            <div className="space-y-3 font-mono text-[10px]">
              <div className="flex gap-2">
                <span className="text-cs-muted">14:02:11</span>
                <span className="text-cs-emerald">[APPROVE]</span>
                <span className="text-white/80">Wager 1.2% within constraints.</span>
              </div>
              <div className="flex gap-2">
                <span className="text-cs-muted">13:45:09</span>
                <span className="text-cs-red">[REJECT]</span>
                <span className="text-white/80">Edge 0.4% below 2.0% threshold.</span>
              </div>
              <div className="flex gap-2">
                <span className="text-cs-muted">12:11:42</span>
                <span className="text-cs-amber">[REDUCE]</span>
                <span className="text-white/80">Full-Kelly 8.4% exceeds max 5.0%. Clamping to 5.0%.</span>
              </div>
            </div>
          </div>
        </div>

        {/* Bankroll Chart & Recent Decisions */}
        <div className="col-span-2 flex flex-col gap-6">
           <div className="cs-card p-5 h-64 border-cs-border flex flex-col items-center justify-center relative overflow-hidden">
              {/* Fake chart visualization */}
              <div className="absolute inset-0 bg-gradient-to-t from-cs-emerald/10 to-transparent" />
              <svg viewBox="0 0 100 40" className="w-full h-full absolute bottom-0 left-0" preserveAspectRatio="none">
                <polyline 
                  points="0,35 10,32 20,34 30,28 40,29 50,20 60,15 70,18 80,10 90,8 100,5"
                  fill="none" 
                  stroke="currentColor" 
                  className="text-cs-emerald"
                  strokeWidth="0.5" 
                />
              </svg>
              <div className="relative z-10 text-center">
                <h3 className="text-xs font-bold text-cs-muted uppercase tracking-widest mb-2">Portfolio ROI (30D)</h3>
                <div className="text-4xl font-mono text-white mb-2">+14.2%</div>
                <div className="flex items-center justify-center gap-2 text-xs text-cs-emerald">
                  <TrendingUp className="w-3 h-3" />
                  <span>+$3,059.12</span>
                </div>
              </div>
           </div>

           <div className="cs-card p-0 flex-1 border-cs-border overflow-hidden flex flex-col">
              <div className="px-5 py-3 border-b border-cs-border/50 bg-cs-dark/50">
                <h3 className="text-xs font-bold text-white uppercase tracking-widest">Recent Portfolio Decisions</h3>
              </div>
              <div className="flex-1 overflow-y-auto">
                <table className="w-full text-left text-sm whitespace-nowrap">
                  <thead className="bg-cs-black/40 text-[10px] uppercase tracking-widest text-cs-muted">
                    <tr>
                      <th className="px-5 py-3 font-semibold">Time</th>
                      <th className="px-5 py-3 font-semibold">Asset</th>
                      <th className="px-5 py-3 font-semibold">Edge</th>
                      <th className="px-5 py-3 font-semibold">Kelly %</th>
                      <th className="px-5 py-3 font-semibold">Decision</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-xs divide-y divide-cs-border/30">
                    <tr className="hover:bg-white/5 transition-colors">
                      <td className="px-5 py-3 text-cs-muted">14:02</td>
                      <td className="px-5 py-3 text-white">A. Wilson PTS</td>
                      <td className="px-5 py-3 text-cs-emerald">+4.2%</td>
                      <td className="px-5 py-3 text-white">1.2%</td>
                      <td className="px-5 py-3"><CheckCircle2 className="w-4 h-4 text-cs-emerald" /></td>
                    </tr>
                    <tr className="hover:bg-white/5 transition-colors">
                      <td className="px-5 py-3 text-cs-muted">13:45</td>
                      <td className="px-5 py-3 text-white">B. Stewart REB</td>
                      <td className="px-5 py-3 text-cs-red">+0.4%</td>
                      <td className="px-5 py-3 text-white">0.0%</td>
                      <td className="px-5 py-3"><span className="text-cs-red text-[10px]">REJECTED</span></td>
                    </tr>
                    <tr className="hover:bg-white/5 transition-colors">
                      <td className="px-5 py-3 text-cs-muted">12:11</td>
                      <td className="px-5 py-3 text-white">S. Ionescu 3PT</td>
                      <td className="px-5 py-3 text-cs-emerald">+8.1%</td>
                      <td className="px-5 py-3 text-white">5.0% <span className="text-cs-amber text-[10px] ml-1">(MAX)</span></td>
                      <td className="px-5 py-3"><CheckCircle2 className="w-4 h-4 text-cs-emerald" /></td>
                    </tr>
                  </tbody>
                </table>
              </div>
           </div>
        </div>
      </div>
    </div>
  );
}
