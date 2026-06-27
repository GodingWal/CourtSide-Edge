import { useEffect, useState } from 'react';
import { Database, CircleDot, RefreshCw } from 'lucide-react';

/* ── mock data ─────────────────────────────────────────────────────── */
const MOCK_RAW_FEED = [
  { id: 'ev_1', source: 'Pinnacle', type: 'ODDS_UPDATE', payload: 'A. Wilson PTS 22.5 O -110', timestamp: '12:41:02.105' },
  { id: 'ev_2', source: 'DraftKings', type: 'ODDS_UPDATE', payload: 'A. Wilson PTS 22.5 O -115', timestamp: '12:41:02.302' },
  { id: 'ev_3', source: 'Twitter', type: 'NEWS_ALERT', payload: 'Lineup confirmation: NYL starting 5 unchanged', timestamp: '12:41:05.801' },
  { id: 'ev_4', source: 'FanDuel', type: 'LINE_MOVEMENT', payload: 'B. Stewart REB 9.5 O -120 -> -110', timestamp: '12:41:10.045' },
];

export default function DataIngestionView() {
  const [feed, setFeed] = useState<any[]>(MOCK_RAW_FEED);
  const [isLive, setIsLive] = useState(true);

  // Simulate incoming data
  useEffect(() => {
    if (!isLive) return;
    const interval = setInterval(() => {
      const newEvent = {
        id: `ev_${Math.random()}`,
        source: ['Pinnacle', 'DraftKings', 'FanDuel', 'Twitter', 'ESPN'][Math.floor(Math.random() * 5)],
        type: ['ODDS_UPDATE', 'LINE_MOVEMENT', 'NEWS_ALERT', 'INJURY_REPORT'][Math.floor(Math.random() * 4)],
        payload: `Simulated data payload stream... ${Math.random().toString(36).substring(7)}`,
        timestamp: new Date().toISOString().substring(11, 23)
      };
      setFeed(prev => [newEvent, ...prev].slice(0, 50));
    }, 2000);
    return () => clearInterval(interval);
  }, [isLive]);

  return (
    <div className="flex flex-col h-full w-full animate-fade-in p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Database className="w-5 h-5 text-cs-neon-blue" />
            <h1 className="text-xl font-bold text-white tracking-wide">Tier 1: Data Ingestion</h1>
          </div>
          <p className="text-cs-muted text-sm">Raw websocket firehose aggregating odds, news, and market signals.</p>
        </div>
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2 px-3 py-1.5 bg-cs-dark border border-cs-border rounded-lg">
            <span className="relative flex h-2 w-2">
              <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${isLive ? 'bg-cs-emerald animate-ping' : 'bg-cs-red'}`} />
              <span className={`relative inline-flex h-2 w-2 rounded-full ${isLive ? 'bg-cs-emerald' : 'bg-cs-red'}`} />
            </span>
            <span className="text-xs font-mono text-white/70">{isLive ? 'FIREHOSE ACTIVE' : 'DISCONNECTED'}</span>
          </div>
          <button 
            onClick={() => setIsLive(!isLive)}
            className="p-2 hover:bg-cs-dark rounded-lg transition-colors border border-cs-border"
          >
            <RefreshCw className={`w-4 h-4 text-cs-muted ${isLive ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 h-[calc(100vh-140px)]">
        {/* Left Stats */}
        <div className="col-span-1 flex flex-col gap-4">
          <div className="cs-card p-4 border-cs-neon-blue/20 bg-cs-neon-blue-glow/5">
            <div className="text-[10px] text-cs-neon-blue uppercase tracking-widest font-bold mb-2">Ingestion Rate</div>
            <div className="text-3xl font-mono text-white">1,402 <span className="text-sm text-cs-muted">msg/sec</span></div>
          </div>
          <div className="cs-card p-4 border-cs-neon-purple/20 bg-cs-neon-purple-glow/5">
            <div className="text-[10px] text-cs-neon-purple uppercase tracking-widest font-bold mb-2">Connected Books</div>
            <div className="text-3xl font-mono text-white">8 <span className="text-sm text-cs-muted">active sockets</span></div>
          </div>
          <div className="cs-card p-4 flex-1 flex flex-col">
             <div className="text-[10px] text-cs-muted uppercase tracking-widest font-bold mb-4">Data Source Health</div>
             <div className="space-y-4">
               {['Pinnacle (Odds)', 'DraftKings (Odds)', 'Twitter (News)', 'Rotoworld (Injuries)'].map(source => (
                 <div key={source} className="flex items-center justify-between">
                   <span className="text-sm text-white/80">{source}</span>
                   <CircleDot className="w-3 h-3 text-cs-emerald" />
                 </div>
               ))}
             </div>
          </div>
        </div>

        {/* Right Terminal Log */}
        <div className="col-span-3 cs-console p-0 flex flex-col h-full border-cs-neon-blue/30 shadow-glow-blue-sm">
          <div className="px-4 py-2 bg-cs-dark/80 border-b border-cs-border/50 flex items-center justify-between">
             <span className="text-xs font-mono text-cs-neon-blue">syslog // TIER_1_ROUTER</span>
             <span className="text-xs font-mono text-cs-muted">latency: 42ms</span>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-1 font-mono text-xs">
            {feed.map(event => (
              <div key={event.id} className="flex items-start gap-4 hover:bg-white/5 px-2 py-1 rounded transition-colors animate-fade-in">
                <span className="text-cs-muted shrink-0 w-24">[{event.timestamp}]</span>
                <span className={`shrink-0 w-28 ${
                  event.type === 'NEWS_ALERT' || event.type === 'INJURY_REPORT' ? 'text-cs-neon-purple' : 'text-cs-neon-blue'
                }`}>
                  [{event.type}]
                </span>
                <span className="text-white/40 shrink-0 w-20">{event.source}</span>
                <span className="text-white break-all">{event.payload}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
