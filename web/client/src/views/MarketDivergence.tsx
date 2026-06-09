import { useEffect, useState } from 'react';
import { Activity } from 'lucide-react';

export default function MarketDivergence() {
  const [messages, setMessages] = useState<any[]>([]);

  useEffect(() => {
    const sse = new EventSource('http://localhost:3000/api/stream/alerts');
    sse.onmessage = (e) => {
      if (e.data !== 'heartbeat') {
        const data = JSON.parse(e.data);
        setMessages(prev => [data, ...prev].slice(0, 50));
      }
    };
    return () => sse.close();
  }, []);

  return (
    <div className="p-8 space-y-6 max-w-7xl mx-auto w-full h-screen overflow-hidden flex flex-col">
      <div className="flex items-center justify-between">
        <h1 className="text-3xl font-bold text-white flex items-center gap-3">
           <Activity className="text-emerald-400" />
           Market Divergence Terminal
        </h1>
      </div>
      
      <div className="flex-1 grid grid-cols-3 gap-6 min-h-0">
        <div className="col-span-2 glass-panel rounded-2xl overflow-hidden p-6 flex flex-col">
           <h2 className="text-xl font-bold mb-4 text-slate-300">Live EV Discrepancies</h2>
           {/* Shadcn Table goes here */}
           <div className="flex-1 flex items-center justify-center border border-slate-800 rounded-lg bg-slate-900/50">
             <span className="text-slate-500 font-medium">Listening for Ensemble Edges...</span>
           </div>
        </div>
        <div className="col-span-1 glass-panel rounded-2xl p-6 flex flex-col">
           <h2 className="text-xl font-bold mb-4 text-slate-300">Alert Stream</h2>
           <div className="flex-1 overflow-y-auto space-y-3">
              {messages.length === 0 ? (
                 <div className="text-sm text-slate-500">No alerts yet. Wait for next heartbeat...</div>
              ) : (
                 messages.map((m, i) => (
                    <div key={i} className="bg-slate-800/50 p-3 rounded-lg text-sm border border-slate-700">
                      <span className="text-emerald-400 font-bold mb-1 block">{m.channel}</span>
                      <pre className="text-slate-300 text-xs overflow-hidden text-ellipsis">{JSON.stringify(m.message, null, 2)}</pre>
                    </div>
                 ))
              )}
           </div>
        </div>
      </div>
    </div>
  );
}
