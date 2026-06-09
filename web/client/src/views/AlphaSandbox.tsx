import { Cpu, Send } from 'lucide-react';

export default function AlphaSandbox() {
  return (
    <div className="p-8 space-y-6 max-w-7xl mx-auto w-full h-screen flex flex-col">
      <h1 className="text-3xl font-bold text-white flex items-center gap-3 shrink-0">
         <Cpu className="text-blue-400" />
         Alpha Discovery Sandbox
      </h1>

      <div className="flex-1 grid grid-cols-2 gap-6 min-h-0">
        <div className="glass-panel p-6 rounded-2xl flex flex-col">
          <h2 className="text-xl font-bold mb-4 text-slate-300">Agent 12 Prompt</h2>
          <div className="flex-1 bg-slate-900/50 rounded-lg border border-slate-800 p-4 mb-4 text-slate-400 text-sm overflow-y-auto">
             Agent 12 (Quantitative Signal) is not currently active in the Python mesh.
          </div>
          <div className="flex gap-2">
            <input 
              type="text" 
              placeholder="Test centers vs Liberty on a back-to-back..." 
              className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-4 py-2 text-white focus:outline-none focus:border-blue-500"
            />
            <button className="bg-blue-600 hover:bg-blue-500 text-white p-2 px-4 rounded-lg flex items-center justify-center transition-colors">
               <Send className="w-5 h-5" />
            </button>
          </div>
        </div>
        
        <div className="glass-panel p-6 rounded-2xl flex flex-col">
          <h2 className="text-xl font-bold mb-4 text-slate-300">Output Validation</h2>
          <div className="flex-1 bg-slate-950 rounded-lg border border-slate-800 p-4 font-mono text-sm text-emerald-400 overflow-y-auto">
             {`// Python Pandas vectorization output will appear here
# import pandas as pd
# df = load_data()
`}
          </div>
          <div className="mt-4 grid grid-cols-2 gap-4">
             <div className="bg-slate-900/80 p-3 rounded-lg border border-slate-800 text-center">
                <span className="text-xs text-slate-400 block mb-1">Information Coefficient</span>
                <span className="text-lg font-bold text-white">0.000</span>
             </div>
             <div className="bg-slate-900/80 p-3 rounded-lg border border-slate-800 text-center">
                <span className="text-xs text-slate-400 block mb-1">Win Rate</span>
                <span className="text-lg font-bold text-white">--%</span>
             </div>
          </div>
        </div>
      </div>
    </div>
  );
}
