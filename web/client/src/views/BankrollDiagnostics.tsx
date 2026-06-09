import { PieChart as PieChartIcon } from 'lucide-react';

export default function BankrollDiagnostics() {
  return (
    <div className="p-8 space-y-6 max-w-7xl mx-auto w-full">
      <h1 className="text-3xl font-bold text-white flex items-center gap-3">
         <PieChartIcon className="text-purple-400" />
         Bankroll & CLV Diagnostics
      </h1>

      <div className="grid grid-cols-2 gap-6 mt-8">
        <div className="glass-panel p-6 rounded-2xl h-96 flex flex-col items-center justify-center">
           <h3 className="text-slate-400 font-bold mb-2">Closing Line Value (CLV)</h3>
           <p className="text-sm text-slate-500">Recharts will be mounted here shortly...</p>
        </div>
        <div className="glass-panel p-6 rounded-2xl h-96 flex flex-col items-center justify-center relative overflow-hidden">
           <h3 className="text-slate-400 font-bold mb-2">Drawdown Gauge</h3>
           <p className="text-sm text-slate-500 mb-8">Recharts Radial Bar mounted here...</p>
           
           <div className="absolute bottom-0 w-full p-4 bg-red-500/10 border-t border-red-500/20 text-center hidden">
              <span className="font-bold text-red-400 tracking-widest">SYSTEM HALTED</span>
           </div>
        </div>
      </div>
    </div>
  );
}
