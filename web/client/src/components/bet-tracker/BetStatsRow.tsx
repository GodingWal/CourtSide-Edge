import type { BetStats } from './types';

interface BetStatsRowProps {
  stats: BetStats | null;
}

export default function BetStatsRow({ stats }: BetStatsRowProps) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      <div className="cs-card p-5 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '0ms' }}>
        <p className="cs-stat-label">Total Bets Logged</p>
        <span className="cs-stat text-3xl mt-1.5 block">{stats?.total_bets || 0}</span>
      </div>

      <div className="cs-card p-5 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '100ms' }}>
        <p className="cs-stat-label">Win Rate</p>
        <span className="cs-stat text-3xl mt-1.5 block text-gradient-red">
          {stats?.win_rate ? `${stats.win_rate.toFixed(1)}%` : '0.0%'}
        </span>
      </div>

      <div className="cs-card p-5 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '200ms' }}>
        <p className="cs-stat-label">Net Profit/Loss</p>
        <span className={`cs-stat text-3xl mt-1.5 block ${stats && stats.total_profit >= 0 ? 'text-emerald-400' : 'text-cs-red-bright'}`}>
          {stats ? (stats.total_profit >= 0 ? `+$${stats.total_profit.toFixed(2)}` : `-$${Math.abs(stats.total_profit).toFixed(2)}`) : '$0.00'}
        </span>
      </div>

      <div className="cs-card p-5 group hover:shadow-glow-red transition-shadow duration-500 animate-slide-up" style={{ animationDelay: '300ms' }}>
        <p className="cs-stat-label">Average Edge</p>
        <span className="cs-stat text-3xl mt-1.5 block">
          {stats?.avg_edge ? `+${(stats.avg_edge * 100).toFixed(1)}%` : '0.0%'}
        </span>
      </div>
    </div>
  );
}
