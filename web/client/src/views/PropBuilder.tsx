import { useState } from 'react';
import { Wrench, ChevronRight, Activity } from 'lucide-react';
import { LineChart, Line, XAxis, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts';

export default function PropBuilder() {
  const [player, setPlayer] = useState('Breanna Stewart');
  const [team, setTeam] = useState('LVA');
  const [stat, setStat] = useState('Points');
  const [line, setLine] = useState(25.5);
  
  const [matchupData, setMatchupData] = useState<any>(null);
  const [projectionData, setProjectionData] = useState<any>(null);
  const [loading, setLoading] = useState(false);

  const handleBuild = async () => {
    setLoading(true);
    try {
      // 1. Fetch Qualitative Matchup Summary
      const matchupRes = await fetch(`http://localhost:3000/api/matchup/${encodeURIComponent(player)}/${encodeURIComponent(team)}`);
      const mData = await matchupRes.json();
      setMatchupData(mData);
      
      // 2. Fetch Quantitative Mathematical Distribution
      const projRes = await fetch(`http://localhost:3000/api/custom_prop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player, stat, line, opposing_team: team })
      });
      const pData = await projRes.json();
      setProjectionData(pData);
      
    } catch (err) {
      console.error(err);
    }
    setLoading(false);
  };

  return (
    <div className="p-8 space-y-6 max-w-7xl mx-auto w-full h-screen flex flex-col">
      <h1 className="text-3xl font-bold text-white flex items-center gap-3 shrink-0">
         <Wrench className="text-red-500" />
         Interactive Prop Builder
      </h1>

      <div className="flex-1 grid grid-cols-1 lg:grid-cols-3 gap-6 min-h-0">
        
        {/* Left Column: The Form */}
        <div className="glass-panel p-6 rounded-2xl flex flex-col gap-6 col-span-1">
          <h2 className="text-xl font-bold text-zinc-300">Custom Configuration</h2>
          
          <div className="space-y-4 flex-1">
            <div>
               <label className="text-xs text-zinc-500 uppercase tracking-wider font-bold">Player Name</label>
               <input value={player} onChange={e => setPlayer(e.target.value)} className="w-full mt-1 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2 text-white focus:border-red-500 outline-none" />
            </div>
            <div>
               <label className="text-xs text-zinc-500 uppercase tracking-wider font-bold">Opposing Team</label>
               <input value={team} onChange={e => setTeam(e.target.value)} className="w-full mt-1 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2 text-white focus:border-red-500 outline-none" />
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                 <label className="text-xs text-zinc-500 uppercase tracking-wider font-bold">Stat</label>
                 <select value={stat} onChange={e => setStat(e.target.value)} className="w-full mt-1 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2 text-white focus:border-red-500 outline-none">
                    <option>Points</option>
                    <option>Rebounds</option>
                    <option>Assists</option>
                 </select>
              </div>
              <div>
                 <label className="text-xs text-zinc-500 uppercase tracking-wider font-bold">Line</label>
                 <input type="number" step="0.5" value={line} onChange={e => setLine(parseFloat(e.target.value))} className="w-full mt-1 bg-zinc-900 border border-zinc-700 rounded-lg px-4 py-2 text-white focus:border-red-500 outline-none" />
              </div>
            </div>
          </div>
          
          <button 
             onClick={handleBuild} 
             disabled={loading}
             className="w-full bg-red-600 hover:bg-red-500 text-white font-bold py-3 rounded-lg flex items-center justify-center gap-2 transition-colors disabled:opacity-50"
          >
             {loading ? 'Building...' : 'Generate Prop'}
             <ChevronRight className="w-5 h-5" />
          </button>
        </div>

        {/* Right Column: Output */}
        <div className="col-span-2 flex flex-col gap-6">
           
           {/* Qualitative Matchup Oracle */}
           <div className="glass-panel p-6 rounded-2xl">
              <h2 className="text-xl font-bold text-zinc-300 mb-4 flex items-center gap-2">
                 <Activity className="text-red-500 w-5 h-5" /> Matchup Oracle (Nemotron)
              </h2>
              {matchupData ? (
                 <div className="space-y-4">
                    <p className="text-zinc-300 leading-relaxed text-sm bg-zinc-900/50 p-4 rounded-lg border border-zinc-800">
                       {matchupData.summary}
                    </p>
                    <div className="grid grid-cols-3 gap-4">
                       <div className="bg-zinc-900/50 p-3 rounded-lg border border-zinc-800 text-center">
                          <span className="text-xs text-zinc-500 block">Def Rating</span>
                          <span className="font-bold text-white">{matchupData.metrics.defensive_rating}</span>
                       </div>
                       <div className="bg-zinc-900/50 p-3 rounded-lg border border-zinc-800 text-center">
                          <span className="text-xs text-zinc-500 block">Pace</span>
                          <span className="font-bold text-white">{matchupData.metrics.pace}</span>
                       </div>
                       <div className="bg-zinc-900/50 p-3 rounded-lg border border-zinc-800 text-center">
                          <span className="text-xs text-zinc-500 block">Reb Rate</span>
                          <span className="font-bold text-white">{matchupData.metrics.rebound_rate}%</span>
                       </div>
                    </div>
                 </div>
              ) : (
                 <div className="h-24 flex items-center justify-center text-zinc-600 border border-zinc-800/50 rounded-lg border-dashed">
                    Waiting for input...
                 </div>
              )}
           </div>
           
           {/* Quantitative Distribution */}
           <div className="glass-panel p-6 rounded-2xl flex-1 flex flex-col min-h-0">
              <h2 className="text-xl font-bold text-zinc-300 mb-4">Probability Distribution (Agent 3)</h2>
              {projectionData ? (
                 <div className="flex-1 flex flex-col">
                    <div className="flex justify-between items-end mb-4">
                       <div>
                          <span className="text-xs text-zinc-500 block uppercase">True Odds (Over)</span>
                          <span className="text-2xl font-bold text-red-500">{projectionData.true_odds}%</span>
                       </div>
                       <div className="text-right">
                          <span className="text-xs text-zinc-500 block uppercase">Projected {stat}</span>
                          <span className="text-2xl font-bold text-white">{projectionData.projection.toFixed(1)}</span>
                       </div>
                    </div>
                    <div className="flex-1 min-h-[200px]">
                       <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={projectionData.distribution}>
                             <XAxis dataKey="value" stroke="#52525b" fontSize={12} tickLine={false} />
                             <Tooltip 
                                contentStyle={{ backgroundColor: '#18181b', borderColor: '#27272a', color: '#fff' }} 
                                itemStyle={{ color: '#ef4444' }} 
                             />
                             <ReferenceLine x={line} stroke="#fff" strokeDasharray="3 3" label={{ position: 'top', value: 'Book Line', fill: '#fff', fontSize: 10 }} />
                             <ReferenceLine x={projectionData.projection} stroke="#ef4444" label={{ position: 'top', value: 'Proj', fill: '#ef4444', fontSize: 10 }} />
                             <Line type="monotone" dataKey="probability" stroke="#ef4444" strokeWidth={3} dot={false} activeDot={{ r: 6 }} />
                          </LineChart>
                       </ResponsiveContainer>
                    </div>
                 </div>
              ) : (
                 <div className="flex-1 flex items-center justify-center text-zinc-600 border border-zinc-800/50 rounded-lg border-dashed">
                    Run projection to view curve
                 </div>
              )}
           </div>

        </div>
      </div>
    </div>
  );
}
