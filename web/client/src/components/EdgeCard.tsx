import { TrendingUp, TrendingDown, Percent } from 'lucide-react';

interface EdgeCardProps {
  player: string;
  team: string;
  stat: string;
  line: number;
  projection: number;
  bookOdds: number;
  trueOdds: number;
  edge: number;
  isOver: boolean;
}

export function EdgeCard({ player, team, stat, line, projection, bookOdds, trueOdds, edge, isOver }: EdgeCardProps) {
  const edgeColor = edge > 5 ? 'text-success' : 'text-primary';
  const glowClass = edge > 5 ? 'shadow-[0_0_15px_rgba(34,197,94,0.15)]' : '';

  return (
    <div className={`glass-card p-5 flex flex-col gap-4 ${glowClass}`}>
      <div className="flex justify-between items-start">
        <div>
          <h3 className="text-lg font-bold text-white">{player}</h3>
          <p className="text-sm text-slate-400">{team}</p>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-xs uppercase tracking-wider text-slate-500 font-semibold mb-1">Book Line</span>
          <span className="text-xl font-bold text-white flex items-center gap-1">
            {isOver ? 'O' : 'U'} {line}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2 py-3 border-y border-slate-700/50">
        <div className="flex flex-col">
          <span className="text-xs text-slate-500">Projection</span>
          <span className="font-semibold">{projection.toFixed(1)}</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="text-xs text-slate-500">True Prob</span>
          <span className="font-semibold text-white">{trueOdds}%</span>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-xs text-slate-500">Stat</span>
          <span className="font-semibold text-slate-300">{stat}</span>
        </div>
      </div>

      <div className="flex justify-between items-center mt-1">
        <div className="flex items-center gap-2">
          {isOver ? <TrendingUp className="w-5 h-5 text-success" /> : <TrendingDown className="w-5 h-5 text-danger" />}
          <span className="text-sm font-medium">Bet {isOver ? 'Over' : 'Under'} @ {bookOdds > 0 ? '+' : ''}{bookOdds}</span>
        </div>
        <div className={`flex items-center gap-1 font-bold ${edgeColor}`}>
          <Percent className="w-4 h-4" />
          <span>{edge.toFixed(1)} Edge</span>
        </div>
      </div>
    </div>
  );
}
