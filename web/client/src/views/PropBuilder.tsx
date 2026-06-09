import { useState } from 'react';
import { Wrench, ChevronRight, Activity, Target } from 'lucide-react';
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';

interface MatchupData {
  summary: string;
  pace: number;
  offRating: number;
  defRating: number;
  projTotal: number;
}

interface DistPoint {
  x: number;
  y: number;
}

interface PropResult {
  distribution: DistPoint[];
  trueOdds: number;
  edge: number;
  median: number;
}

export default function PropBuilder() {
  const [player, setPlayer] = useState('');
  const [stat, setStat] = useState('Points');
  const [line, setLine] = useState('');
  const [opponent, setOpponent] = useState('');
  const [loading, setLoading] = useState(false);
  const [matchup, setMatchup] = useState<MatchupData | null>(null);
  const [propResult, setPropResult] = useState<PropResult | null>(null);

  const handleBuild = async () => {
    if (!player || !line || !opponent) return;
    setLoading(true);

    try {
      const matchupRes = await fetch('http://localhost:3000/api/matchup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player, opponent }),
      });
      const matchupData = await matchupRes.json();
      setMatchup(matchupData);

      const propRes = await fetch('http://localhost:3000/api/custom_prop', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ player, stat, line: parseFloat(line), opponent }),
      });
      const propData = await propRes.json();
      setPropResult(propData);
    } catch (err) {
      console.error('Build failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const statOptions = ['Points', 'Rebounds', 'Assists', 'PRA', '3PM', 'Steals', 'Blocks'];

  return (
    <div className="min-h-screen bg-cs-black p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="w-10 h-10 rounded-xl bg-cs-red/10 border border-cs-red/20 flex items-center justify-center shadow-glow-red-sm">
          <Wrench className="w-5 h-5 text-cs-red" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Prop Builder</h1>
          <p className="text-sm text-cs-muted">Custom projection engine powered by Nemotron</p>
        </div>
      </div>

      {/* 3-Column Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Column 1: Form */}
        <div className="cs-card p-6 space-y-5 animate-slide-up">
          <div className="flex items-center gap-2 mb-1">
            <Target className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Build Prop</h2>
          </div>

          <div className="space-y-4">
            <div>
              <label className="cs-label">Player Name</label>
              <input
                type="text"
                className="cs-input"
                placeholder="e.g. A'ja Wilson"
                value={player}
                onChange={(e) => setPlayer(e.target.value)}
              />
            </div>

            <div>
              <label className="cs-label">Stat Category</label>
              <select
                className="cs-input"
                value={stat}
                onChange={(e) => setStat(e.target.value)}
              >
                {statOptions.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label className="cs-label">Line</label>
              <input
                type="number"
                className="cs-input"
                placeholder="e.g. 22.5"
                value={line}
                onChange={(e) => setLine(e.target.value)}
              />
            </div>

            <div>
              <label className="cs-label">Opponent</label>
              <input
                type="text"
                className="cs-input"
                placeholder="e.g. Las Vegas Aces"
                value={opponent}
                onChange={(e) => setOpponent(e.target.value)}
              />
            </div>
          </div>

          <button
            className="cs-btn-primary w-full group"
            onClick={handleBuild}
            disabled={loading || !player || !line || !opponent}
          >
            {loading ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                Processing…
              </span>
            ) : (
              <span className="flex items-center justify-center gap-2">
                Build Projection
                <ChevronRight className="w-4 h-4 transition-transform group-hover:translate-x-0.5" />
              </span>
            )}
          </button>
        </div>

        {/* Column 2: Matchup Oracle */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '80ms' }}>
          <div className="flex items-center gap-2 mb-5">
            <Activity className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Matchup Oracle</h2>
          </div>

          {matchup ? (
            <div className="space-y-5">
              <p className="text-sm text-gray-300 leading-relaxed border-l-2 border-cs-red/40 pl-4">
                {matchup.summary}
              </p>

              <div className="grid grid-cols-2 gap-3">
                {[
                  { label: 'Pace', value: matchup.pace?.toFixed(1) },
                  { label: 'Off Rating', value: matchup.offRating?.toFixed(1) },
                  { label: 'Def Rating', value: matchup.defRating?.toFixed(1) },
                  { label: 'Proj Total', value: matchup.projTotal?.toFixed(1) },
                ].map((item) => (
                  <div
                    key={item.label}
                    className="bg-cs-black/60 border border-cs-border/50 rounded-xl p-3 text-center"
                  >
                    <div className="cs-stat">{item.value ?? '—'}</div>
                    <div className="cs-stat-label">{item.label}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-52 text-cs-muted">
              <Activity className="w-8 h-8 mb-3 opacity-30" />
              <p className="text-sm">Build a prop to see matchup intelligence</p>
            </div>
          )}
        </div>

        {/* Column 3: Distribution Chart */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '160ms' }}>
          <div className="flex items-center justify-between mb-5">
            <div className="flex items-center gap-2">
              <Target className="w-4 h-4 text-cs-red" />
              <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Distribution</h2>
            </div>
            {propResult && (
              <span className="cs-badge">
                Edge: {propResult.edge > 0 ? '+' : ''}
                {propResult.edge?.toFixed(1)}%
              </span>
            )}
          </div>

          {propResult ? (
            <div className="space-y-5">
              <div className="text-center">
                <span className="text-xs text-cs-muted uppercase tracking-wider">True Odds</span>
                <div className="text-3xl font-black text-gradient-red mt-1">
                  {propResult.trueOdds > 0 ? '+' : ''}
                  {propResult.trueOdds}
                </div>
              </div>

              <div className="h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={propResult.distribution}>
                    <defs>
                      <linearGradient id="redGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#dc2626" stopOpacity={0.35} />
                        <stop offset="100%" stopColor="#dc2626" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis
                      dataKey="x"
                      stroke="#444"
                      tick={{ fill: '#666', fontSize: 11 }}
                      axisLine={{ stroke: '#333' }}
                    />
                    <YAxis
                      stroke="#444"
                      tick={{ fill: '#666', fontSize: 11 }}
                      axisLine={{ stroke: '#333' }}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: '#111111',
                        border: '1px solid #333',
                        borderRadius: '12px',
                        color: '#fff',
                        fontSize: '12px',
                      }}
                    />
                    <ReferenceLine
                      x={parseFloat(line)}
                      stroke="#ffffff"
                      strokeDasharray="4 4"
                      strokeWidth={1.5}
                      label={{ value: 'Line', fill: '#888', fontSize: 11 }}
                    />
                    {propResult.median && (
                      <ReferenceLine
                        x={propResult.median}
                        stroke="#dc2626"
                        strokeDasharray="4 4"
                        strokeWidth={1.5}
                        label={{ value: 'Median', fill: '#dc2626', fontSize: 11 }}
                      />
                    )}
                    <Area
                      type="monotone"
                      dataKey="y"
                      stroke="#dc2626"
                      strokeWidth={2}
                      fill="url(#redGradient)"
                      dot={false}
                      activeDot={{ r: 4, fill: '#dc2626', stroke: '#fff', strokeWidth: 2 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-52 text-cs-muted">
              <Target className="w-8 h-8 mb-3 opacity-30" />
              <p className="text-sm">Distribution will appear here</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
