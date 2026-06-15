/*
  ConfidenceRing — SVG circular progress gauge
  Shows 0-100% as a fill ring with color coding:
    >= 80% → emerald (hot)
    >= 55% → blue (normal)
    >= 35% → amber (cold)
    <  35% → red (halt)
*/

interface ConfidenceRingProps {
  value: number; // 0–1
  size?: number;
  strokeWidth?: number;
  mode?: string;
}

export default function ConfidenceRing({ value, size = 48, strokeWidth = 4, mode }: ConfidenceRingProps) {
  const pct = Math.max(0, Math.min(1, value));
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - pct);

  const ringColor =
    mode === 'halt' || pct < 0.35 ? '#ef4444' :
    mode === 'cold_streak' || pct < 0.55 ? '#eab308' :
    mode === 'hot_streak' && pct >= 0.80 ? '#22c55e' :
    '#3b82f6';

  const glowColor =
    mode === 'halt' ? 'rgba(239,68,68,0.3)' :
    mode === 'cold_streak' ? 'rgba(234,179,8,0.2)' :
    mode === 'hot_streak' ? 'rgba(34,197,94,0.2)' :
    'rgba(59,130,246,0.15)';

  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      {/* Glow behind */}
      <div
        className="absolute inset-0 rounded-full"
        style={{ boxShadow: `0 0 ${size * 0.3}px ${glowColor}` }}
      />
      <svg width={size} height={size} className="-rotate-90">
        {/* Background track */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="#1a1a1a"
          strokeWidth={strokeWidth}
        />
        {/* Fill arc */}
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={ringColor}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ transition: 'stroke-dashoffset 0.8s ease-out, stroke 0.4s ease' }}
        />
      </svg>
      {/* Center text */}
      <span
        className="absolute text-[10px] font-black tabular-nums"
        style={{ color: ringColor }}
      >
        {Math.round(pct * 100)}
      </span>
    </div>
  );
}
