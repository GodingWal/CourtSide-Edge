/*
  TrustGauge — horizontal bar with label + value
  Thin, compact, designed for the SystemCortexBar
*/

interface TrustGaugeProps {
  label: string;
  value: number; // 0–1
  color?: 'emerald' | 'blue' | 'amber' | 'red';
}

const colorMap = {
  emerald: { fill: 'bg-emerald-500', bg: 'bg-emerald-500/10', text: 'text-emerald-400' },
  blue:    { fill: 'bg-blue-500',    bg: 'bg-blue-500/10',    text: 'text-blue-400' },
  amber:   { fill: 'bg-amber-500',   bg: 'bg-amber-500/10',   text: 'text-amber-400' },
  red:     { fill: 'bg-red-500',     bg: 'bg-red-500/10',     text: 'text-red-400' },
};

export default function TrustGauge({ label, value, color = 'emerald' }: TrustGaugeProps) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  const c = colorMap[color];

  return (
    <div className="flex items-center gap-2.5 min-w-0">
      <span className={`text-[9px] font-bold uppercase tracking-wider shrink-0 w-8 text-right ${c.text}`}>
        {label}
      </span>
      <div className={`cs-trust-bar-bg flex-1 min-w-[40px] ${c.bg}`}>
        <div
          className={`cs-trust-bar-fill ${c.fill}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-[9px] font-mono font-bold tabular-nums shrink-0 w-5 ${c.text}`}>
        {pct}
      </span>
    </div>
  );
}
