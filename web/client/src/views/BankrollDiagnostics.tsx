import { PieChart as PieChartIcon, TrendingUp, TrendingDown, Shield } from 'lucide-react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';

// Generate 30 days of mock CLV data
const clvData = Array.from({ length: 30 }, (_, i) => {
  const date = new Date();
  date.setDate(date.getDate() - (29 - i));
  return {
    date: `${date.getMonth() + 1}/${date.getDate()}`,
    clv: +(1 + Math.random() * 7).toFixed(2),
  };
});

const topStats = [
  {
    label: 'Total Bankroll',
    value: '$10,000',
    icon: PieChartIcon,
    color: 'text-white',
    accent: 'border-cs-border/50',
  },
  {
    label: "Today's P&L",
    value: '+$340',
    icon: TrendingUp,
    color: 'text-emerald-400',
    accent: 'border-emerald-500/20',
  },
  {
    label: 'CLV Score',
    value: '4.2%',
    icon: TrendingUp,
    color: 'text-cs-red-bright',
    accent: 'border-cs-red/20',
  },
  {
    label: 'Max Drawdown',
    value: '-$820',
    icon: TrendingDown,
    color: 'text-red-400',
    accent: 'border-red-500/20',
  },
];

const riskMetrics = [
  { label: 'Kelly Criterion', value: '2.4%' },
  { label: 'Sharpe Ratio', value: '1.31' },
  { label: 'Units Won', value: '+23.4' },
];

function DrawdownGauge({ percentage = 34 }: { percentage?: number }) {
  const rotation = (percentage / 100) * 180;

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-44 h-24 overflow-hidden">
        {/* Background arc */}
        <div
          className="absolute inset-0 w-44 h-44 rounded-full border-[10px] border-cs-border/30"
          style={{ clipPath: 'inset(0 0 50% 0)' }}
        />
        {/* Colored arc */}
        <div
          className="absolute inset-0 w-44 h-44 rounded-full border-[10px] border-transparent"
          style={{
            borderTopColor: percentage > 70 ? '#ef4444' : percentage > 40 ? '#eab308' : '#22c55e',
            borderRightColor: rotation > 90 ? (percentage > 70 ? '#ef4444' : percentage > 40 ? '#eab308' : '#22c55e') : 'transparent',
            transform: `rotate(${rotation > 180 ? 180 : 0}deg)`,
            clipPath: 'inset(0 0 50% 0)',
          }}
        />
        {/* Needle */}
        <div
          className="absolute bottom-0 left-1/2 w-0.5 h-20 bg-white origin-bottom rounded-full shadow-lg"
          style={{ transform: `translateX(-50%) rotate(${rotation - 90}deg)` }}
        />
        {/* Center dot */}
        <div className="absolute bottom-0 left-1/2 -translate-x-1/2 translate-y-1/2 w-3 h-3 rounded-full bg-white shadow-glow-red-sm" />
      </div>
      <div className="mt-3 text-center">
        <div className="text-2xl font-black text-white">{percentage}%</div>
        <div className="text-xs text-cs-muted uppercase tracking-wider">Current Drawdown</div>
      </div>
    </div>
  );
}

export default function BankrollDiagnostics() {
  const isHealthy = true;

  return (
    <div className="min-h-screen bg-cs-black p-6 animate-fade-in">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="w-10 h-10 rounded-xl bg-cs-red/10 border border-cs-red/20 flex items-center justify-center shadow-glow-red-sm">
          <PieChartIcon className="w-5 h-5 text-cs-red" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Bankroll Diagnostics</h1>
          <p className="text-sm text-cs-muted">Real-time portfolio health & risk analytics</p>
        </div>
      </div>

      {/* Top Stat Row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
        {topStats.map(({ label, value, icon: Icon, color, accent }) => (
          <div
            key={label}
            className={`cs-card p-5 border ${accent} animate-slide-up`}
          >
            <div className="flex items-center justify-between mb-3">
              <span className="cs-stat-label">{label}</span>
              <Icon className="w-4 h-4 text-cs-muted" />
            </div>
            <div className={`text-2xl font-black ${color}`}>{value}</div>
          </div>
        ))}
      </div>

      {/* Main Area — 2 Columns */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* CLV Over Time */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '80ms' }}>
          <div className="flex items-center justify-between mb-6">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-cs-red" />
              <h2 className="text-sm font-semibold text-white uppercase tracking-wider">CLV Over Time</h2>
            </div>
            <span className="cs-badge">30 Days</span>
          </div>

          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={clvData}>
                <defs>
                  <linearGradient id="clvGradient" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#dc2626" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#dc2626" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <XAxis
                  dataKey="date"
                  stroke="#444"
                  tick={{ fill: '#666', fontSize: 10 }}
                  axisLine={{ stroke: '#333' }}
                  interval={4}
                />
                <YAxis
                  stroke="#444"
                  tick={{ fill: '#666', fontSize: 11 }}
                  axisLine={{ stroke: '#333' }}
                  domain={[0, 10]}
                  tickFormatter={(v: number) => `${v}%`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#111111',
                    border: '1px solid #333',
                    borderRadius: '12px',
                    color: '#fff',
                    fontSize: '12px',
                  }}
                  formatter={(value: number) => [`${value}%`, 'CLV']}
                />
                <Area
                  type="monotone"
                  dataKey="clv"
                  stroke="#dc2626"
                  strokeWidth={2}
                  fill="url(#clvGradient)"
                  dot={false}
                  activeDot={{ r: 4, fill: '#dc2626', stroke: '#fff', strokeWidth: 2 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Risk Metrics */}
        <div className="cs-card p-6 animate-slide-up" style={{ animationDelay: '160ms' }}>
          <div className="flex items-center gap-2 mb-6">
            <Shield className="w-4 h-4 text-cs-red" />
            <h2 className="text-sm font-semibold text-white uppercase tracking-wider">Risk Metrics</h2>
          </div>

          <div className="flex flex-col items-center">
            {/* Drawdown Gauge */}
            <DrawdownGauge percentage={34} />

            {/* Metric Cards */}
            <div className="grid grid-cols-3 gap-3 w-full mt-8">
              {riskMetrics.map(({ label, value }) => (
                <div
                  key={label}
                  className="bg-cs-black/60 border border-cs-border/50 rounded-xl p-4 text-center"
                >
                  <div className="cs-stat">{value}</div>
                  <div className="cs-stat-label">{label}</div>
                </div>
              ))}
            </div>

            {/* System Status Badge */}
            <div className="mt-8">
              {isHealthy ? (
                <div className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-emerald-500/10 border border-emerald-500/30">
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-emerald-400" />
                  </span>
                  <span className="text-xs font-bold text-emerald-400 uppercase tracking-widest">
                    System Healthy
                  </span>
                </div>
              ) : (
                <div className="flex items-center gap-2 px-5 py-2.5 rounded-full bg-red-500/10 border border-red-500/30">
                  <span className="relative flex h-2.5 w-2.5">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
                    <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-400" />
                  </span>
                  <span className="text-xs font-bold text-red-400 uppercase tracking-widest">
                    Halt
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
