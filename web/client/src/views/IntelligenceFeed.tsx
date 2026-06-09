import { Database, AlertTriangle, MessageSquare } from 'lucide-react';

export default function IntelligenceFeed() {
  return (
    <div className="p-8 space-y-6 max-w-7xl mx-auto w-full h-screen flex flex-col">
      <h1 className="text-3xl font-bold text-white flex items-center gap-3">
         <Database className="text-red-500" />
         The Intelligence Feed
      </h1>

      <div className="flex-1 grid grid-cols-1 md:grid-cols-2 gap-6 min-h-0">
        <div className="glass-panel p-6 rounded-2xl flex flex-col">
           <h2 className="text-xl font-bold mb-4 text-zinc-300 flex items-center gap-2">
             <AlertTriangle className="text-red-500 w-5 h-5" /> 
             Live Injury Ticker
           </h2>
           <div className="flex-1 bg-zinc-900/50 rounded-xl border border-zinc-800 p-4 overflow-y-auto">
             <div className="animate-pulse flex items-center justify-center h-full text-zinc-500">
                Listening to Agent 2...
             </div>
           </div>
        </div>

        <div className="flex flex-col gap-6">
           <div className="glass-panel p-6 rounded-2xl flex-1 flex flex-col">
             <h2 className="text-xl font-bold mb-4 text-zinc-300 flex items-center gap-2">
               <MessageSquare className="text-red-400 w-5 h-5" /> 
               Motivation & Fatigue
             </h2>
             <div className="flex-1 bg-zinc-900/50 rounded-xl border border-zinc-800 p-4">
                <span className="text-sm text-zinc-500">Agent 9 qualitative scoring...</span>
             </div>
           </div>

           <div className="glass-panel p-6 rounded-2xl flex-1 flex flex-col">
             <h2 className="text-xl font-bold mb-4 text-zinc-300 flex items-center gap-2">
               <Database className="text-zinc-400 w-5 h-5" /> 
               Referee Profiles
             </h2>
             <div className="flex-1 bg-zinc-900/50 rounded-xl border border-zinc-800 p-4">
                <span className="text-sm text-zinc-500">Agent 5 tendencies...</span>
             </div>
           </div>
        </div>
      </div>
    </div>
  );
}
