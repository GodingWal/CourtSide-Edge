import { Activity, ShieldAlert, DollarSign } from 'lucide-react';

export default function ExecutionMonitor() {
  return (
    <div className="w-full max-w-md mx-auto space-y-4">
      <div className="glass-panel p-5 rounded-2xl flex items-center justify-between">
        <div>
          <h3 className="text-gray-400 text-sm font-semibold uppercase tracking-wider">Active Bankroll</h3>
          <p className="text-2xl font-bold text-white mt-1">$1,000.00</p>
        </div>
        <div className="h-12 w-12 rounded-full bg-emerald-500/10 flex items-center justify-center">
          <DollarSign className="text-emerald-400 w-6 h-6" />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="glass-panel p-4 rounded-2xl flex flex-col items-center justify-center text-center">
          <ShieldAlert className="text-blue-400 w-6 h-6 mb-2" />
          <h4 className="text-xs text-gray-400 font-semibold mb-1">Correlation Guard</h4>
          <span className="text-sm font-bold text-blue-300">Active</span>
        </div>
        <div className="glass-panel p-4 rounded-2xl flex flex-col items-center justify-center text-center">
          <Activity className="text-green-400 w-6 h-6 mb-2" />
          <h4 className="text-xs text-gray-400 font-semibold mb-1">Execution Oracle</h4>
          <span className="text-sm font-bold text-green-300">Armed</span>
        </div>
      </div>

      <div className="glass-panel p-5 rounded-2xl">
        <h3 className="text-gray-200 font-bold mb-3 flex items-center">
          <span className="w-2 h-2 rounded-full bg-neon-accent mr-2 animate-pulse"></span>
          Live Execution Log
        </h3>
        <div className="space-y-3">
          <div className="bg-white/5 border border-white/10 rounded-xl p-3 text-sm flex justify-between items-center hover:bg-white/10 transition-colors cursor-pointer">
             <div>
               <p className="text-gray-300 font-medium">B. Stewart O22.5 PTS</p>
               <p className="text-xs text-emerald-400 mt-0.5">Kelly: 0.02 | 1/4 Size</p>
             </div>
             <div className="text-right">
               <p className="text-white font-bold">$20.00</p>
               <p className="text-xs text-gray-500 mt-0.5">11:42 AM</p>
             </div>
          </div>
          <div className="bg-white/5 border border-white/10 rounded-xl p-3 text-sm flex justify-between items-center opacity-75 hover:opacity-100 transition-opacity">
             <div>
               <p className="text-gray-300 font-medium">A. Wilson O10.5 REB</p>
               <p className="text-xs text-emerald-400 mt-0.5">Kelly: 0.035 | 1/4 Size</p>
             </div>
             <div className="text-right">
               <p className="text-white font-bold">$35.00</p>
               <p className="text-xs text-gray-500 mt-0.5">11:38 AM</p>
             </div>
          </div>
        </div>
      </div>
    </div>
  );
}
