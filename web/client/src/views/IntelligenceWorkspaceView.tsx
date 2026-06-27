import { useState } from 'react';
import { Cpu, Send, BrainCircuit, Activity } from 'lucide-react';

export default function IntelligenceWorkspaceView() {
  const [input, setInput] = useState('');
  
  return (
    <div className="flex flex-col h-full w-full animate-fade-in p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Cpu className="w-5 h-5 text-cs-neon-purple" />
            <h1 className="text-xl font-bold text-white tracking-wide">Tier 2: Intelligence Workspace</h1>
          </div>
          <p className="text-cs-muted text-sm">Direct interaction with Specialized AI Agents (Quant, Sentiment, Line).</p>
        </div>
        <div className="flex gap-4">
           <div className="cs-card px-4 py-2 flex items-center gap-3 border-cs-neon-blue/20 bg-cs-neon-blue-glow/5">
            <span className="text-[10px] text-cs-neon-blue font-bold uppercase tracking-widest">Quant IC</span>
            <span className="text-lg font-mono text-white">0.042</span>
          </div>
          <div className="cs-card px-4 py-2 flex items-center gap-3 border-cs-neon-purple/20 bg-cs-neon-purple-glow/5">
            <span className="text-[10px] text-cs-neon-purple font-bold uppercase tracking-widest">Sentiment F1</span>
            <span className="text-lg font-mono text-white">0.89</span>
          </div>
        </div>
      </div>

      <div className="flex-1 flex gap-6 h-[calc(100vh-140px)]">
        {/* Chat Interface */}
        <div className="w-[60%] flex flex-col cs-card p-0 border-cs-border shadow-card overflow-hidden">
          <div className="px-4 py-3 bg-cs-dark/80 border-b border-cs-border/50 flex items-center gap-3">
             <div className="w-2 h-2 rounded-full bg-cs-emerald animate-pulse-slow shadow-glow-emerald-sm" />
             <span className="text-xs font-bold text-white/70 uppercase tracking-widest">Agent Console</span>
          </div>
          
          <div className="flex-1 overflow-y-auto p-6 space-y-6">
            {/* User Message */}
            <div className="flex justify-end animate-slide-up">
              <div className="max-w-[80%] rounded-2xl rounded-br-sm px-4 py-3 bg-cs-neon-purple/10 border border-cs-neon-purple/20">
                <p className="text-sm text-white/90">Run a sentiment and quantitative analysis on A'ja Wilson given the latest injury report.</p>
              </div>
            </div>

            {/* Sentiment Agent Response */}
            <div className="flex justify-start animate-slide-up" style={{ animationDelay: '100ms' }}>
              <div className="max-w-[85%] rounded-2xl rounded-bl-sm p-4 bg-cs-dark border border-cs-border">
                <div className="flex items-center gap-2 mb-3">
                  <BrainCircuit className="w-4 h-4 text-cs-neon-purple" />
                  <span className="text-[10px] font-bold text-cs-neon-purple uppercase tracking-widest">Sentiment Agent</span>
                </div>
                <p className="text-sm text-white/80 leading-relaxed mb-3">
                  Parsed 142 recent tweets and 3 press conference transcripts. Key findings:
                </p>
                <ul className="text-sm text-white/70 space-y-2 font-mono bg-black/20 p-3 rounded-lg border border-cs-border/50">
                  <li>&gt; Coach stated Wilson is "100% full go"</li>
                  <li>&gt; Social sentiment index: +45 (Highly Positive)</li>
                  <li>&gt; Injury risk score: 0.02 (Negligible)</li>
                  <li className="text-cs-neon-purple mt-2">&gt; <span className="font-bold">Recommendation: NO MINUTES RESTRICTION (-0% penalty)</span></li>
                </ul>
              </div>
            </div>

            {/* Quant Agent Response */}
            <div className="flex justify-start animate-slide-up" style={{ animationDelay: '300ms' }}>
              <div className="max-w-[85%] rounded-2xl rounded-bl-sm p-4 bg-cs-dark border border-cs-border">
                <div className="flex items-center gap-2 mb-3">
                  <Activity className="w-4 h-4 text-cs-neon-blue" />
                  <span className="text-[10px] font-bold text-cs-neon-blue uppercase tracking-widest">Quant Agent</span>
                </div>
                <p className="text-sm text-white/80 leading-relaxed mb-3">
                  Executing Monte Carlo simulation (N=10,000) using Bayesian posterior distribution.
                </p>
                <div className="grid grid-cols-2 gap-4 text-sm font-mono mb-3">
                  <div className="bg-black/20 p-2 rounded border border-cs-border/50">
                    <div className="text-cs-muted text-[10px]">Median Projection</div>
                    <div className="text-white text-lg mt-1">24.2 PTS</div>
                  </div>
                  <div className="bg-black/20 p-2 rounded border border-cs-border/50">
                    <div className="text-cs-muted text-[10px]">Over 22.5 Prob</div>
                    <div className="text-cs-neon-blue text-lg mt-1">62.4%</div>
                  </div>
                </div>
                <p className="text-xs text-cs-emerald font-mono">&gt; Passed edge threshold (2.0%). Sending signal to Portfolio Manager.</p>
              </div>
            </div>
          </div>

          <div className="p-4 border-t border-cs-border/50 bg-cs-dark/40">
            <div className="relative flex items-center">
              <input 
                type="text" 
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Query the Tier 2 Agents..." 
                className="w-full bg-cs-black border border-cs-border rounded-xl pl-4 pr-12 py-3 text-sm text-white focus:outline-none focus:border-cs-neon-purple/50 focus:shadow-glow-purple-sm transition-all"
              />
              <button className="absolute right-2 p-2 bg-cs-neon-purple hover:bg-cs-neon-purple-bright text-white rounded-lg transition-colors">
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>

        {/* Right Sidebar: Active Models */}
        <div className="w-[40%] flex flex-col gap-6">
           <div className="cs-card p-5 border-cs-neon-blue/20 flex-1">
             <h3 className="text-xs font-bold text-white uppercase tracking-widest mb-4 flex items-center gap-2">
               <Activity className="w-4 h-4 text-cs-neon-blue" />
               Quant Models Active
             </h3>
             <div className="space-y-4">
               {[
                 { name: 'Bayesian Player Prop v4', status: 'Running', lat: '45ms' },
                 { name: 'Markov Chain Rebounds', status: 'Running', lat: '62ms' },
                 { name: 'Pace Differential Engine', status: 'Running', lat: '31ms' }
               ].map(m => (
                 <div key={m.name} className="flex justify-between items-center pb-3 border-b border-cs-border/30 last:border-0">
                   <span className="text-sm text-white/80">{m.name}</span>
                   <div className="flex gap-3 text-xs font-mono">
                     <span className="text-cs-emerald">{m.status}</span>
                     <span className="text-cs-muted">{m.lat}</span>
                   </div>
                 </div>
               ))}
             </div>
           </div>

           <div className="cs-card p-5 border-cs-neon-purple/20 flex-1">
             <h3 className="text-xs font-bold text-white uppercase tracking-widest mb-4 flex items-center gap-2">
               <BrainCircuit className="w-4 h-4 text-cs-neon-purple" />
               Sentiment Classifiers
             </h3>
             <div className="space-y-4">
               {[
                 { name: 'Twitter Firehose NLP', status: 'Running', lat: '12ms' },
                 { name: 'Beat Reporter Vector DB', status: 'Running', lat: '84ms' },
                 { name: 'Injury Impact Scorer', status: 'Running', lat: '21ms' }
               ].map(m => (
                 <div key={m.name} className="flex justify-between items-center pb-3 border-b border-cs-border/30 last:border-0">
                   <span className="text-sm text-white/80">{m.name}</span>
                   <div className="flex gap-3 text-xs font-mono">
                     <span className="text-cs-emerald">{m.status}</span>
                     <span className="text-cs-muted">{m.lat}</span>
                   </div>
                 </div>
               ))}
             </div>
           </div>
        </div>
      </div>
    </div>
  );
}
