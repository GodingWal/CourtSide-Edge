import React, { useState, useEffect } from 'react';
import { Cpu, ChevronDown, ChevronUp, Database, BrainCircuit, Scale, Send } from 'lucide-react';
import type { AgentHealthEntry, AgentTier } from '../../types/agent';
import { TIER_DEFINITIONS } from '../../types/agent';

const TIER_ICONS: Record<AgentTier, React.ReactNode> = {
  1: <Database className="h-3 w-3" />,
  2: <BrainCircuit className="h-3 w-3" />,
  3: <Scale className="h-3 w-3" />,
  4: <Send className="h-3 w-3" />,
};

export const SystemStatus: React.FC = () => {
  const [agents, setAgents] = useState<AgentHealthEntry[]>([]);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function fetchHealth() {
      try {
        const res = await fetch('/api/agents/health');
        if (!res.ok) return;
        const data: AgentHealthEntry[] = await res.json();
        if (!cancelled) {
          setAgents(data);
          setIsLoading(false);
        }
      } catch {
        if (!cancelled) setIsLoading(false);
      }
    }

    fetchHealth();
    const interval = setInterval(fetchHealth, 30000);

    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const onlineCount = agents.filter((a) => a.status === 'online').length;
  const totalCount = agents.length;

  // Group agents by tier
  const agentsByTier = TIER_DEFINITIONS.map((tierDef) => ({
    ...tierDef,
    agents: agents.filter((a) => a.tier === tierDef.tier),
  }));

  return (
    <div className="cs-card overflow-hidden">
      {/* Header */}
      <button
        onClick={() => setIsCollapsed((prev) => !prev)}
        className="flex items-center justify-between w-full px-4 py-3 border-b border-cs-border/30 hover:bg-cs-dark/30 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <Cpu className="h-4 w-4 text-cs-emerald" />
          <span className="text-xs font-semibold text-white/70 uppercase tracking-wider">
            Pipeline Status
          </span>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-1.5">
            <span className="relative flex h-2 w-2">
              <span className="absolute inline-flex h-full w-full animate-pulse-slow rounded-full bg-cs-emerald opacity-60" />
              <span className="relative inline-flex h-2 w-2 rounded-full bg-cs-emerald" />
            </span>
            <span className="text-[10px] font-mono text-cs-muted">
              <span className="text-cs-emerald font-semibold">{onlineCount}</span>/{totalCount}
            </span>
          </div>
          {isCollapsed ? (
            <ChevronDown className="h-3.5 w-3.5 text-cs-muted" />
          ) : (
            <ChevronUp className="h-3.5 w-3.5 text-cs-muted" />
          )}
        </div>
      </button>

      {/* Tiered Architecture */}
      {!isCollapsed && (
        <div className="p-3 space-y-2">
          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-16 bg-cs-dark/50 rounded-xl animate-pulse" />
              ))}
            </div>
          ) : (
            agentsByTier.map(({ tier, label, color, borderColor, bgColor, agents: tierAgents }) => (
              <div
                key={tier}
                className={`rounded-xl border ${borderColor} ${bgColor} p-2.5`}
              >
                {/* Tier Header */}
                <div className="flex items-center gap-2 mb-2">
                  <div className={`flex items-center justify-center h-5 w-5 rounded ${bgColor} ${color}`}>
                    {TIER_ICONS[tier]}
                  </div>
                  <div className="flex items-center gap-2 flex-1 min-w-0">
                    <span className={`text-[9px] font-black uppercase tracking-[0.2em] ${color}`}>
                      Tier {tier}
                    </span>
                    <span className="text-[9px] text-cs-muted font-medium truncate">
                      {label}
                    </span>
                  </div>
                  <span className="text-[9px] text-cs-muted font-mono">
                    {tierAgents.filter((a) => a.status === 'online').length}/{tierAgents.length}
                  </span>
                </div>

                {/* Agents in this tier */}
                <div className="space-y-1">
                  {tierAgents.map((agent) => (
                    <div
                      key={agent.id}
                      className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-cs-black/30 border border-cs-border/10"
                    >
                      <span
                        className={`inline-flex h-1.5 w-1.5 rounded-full shrink-0 ${
                          agent.status === 'online'
                            ? 'bg-cs-emerald shadow-glow-emerald-sm'
                            : 'bg-red-500 shadow-glow-red-sm'
                        }`}
                      />
                      <div className="flex-1 min-w-0">
                        <span className="text-[10px] text-white/80 font-medium block truncate leading-none">
                          {agent.name}
                        </span>
                        <span className="text-[8px] text-cs-muted block truncate leading-tight mt-0.5">
                          {agent.description}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          )}

          {/* Flow indicator */}
          {!isLoading && (
            <div className="flex items-center justify-center gap-1.5 py-1">
              {[1, 2, 3, 4].map((tier, i) => {
                const tierDef = TIER_DEFINITIONS[i];
                return (
                  <React.Fragment key={tier}>
                    <span className={`text-[8px] font-bold uppercase tracking-wider ${tierDef.color}`}>
                      T{tier}
                    </span>
                    {i < 3 && (
                      <span className="text-cs-muted text-[8px]">→</span>
                    )}
                  </React.Fragment>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
};
