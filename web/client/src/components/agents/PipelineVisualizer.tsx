import React from 'react';
import type { AgentLogEntry, AgentNode, AgentResult } from '../../types/agent';
import { NODE_LABELS, NODE_COLORS } from '../../types/agent';

interface Props {
  logs: AgentLogEntry[];
  activeNode: AgentNode | null;
  result: AgentResult | null;
}

export const PipelineVisualizer: React.FC<Props> = ({ logs, activeNode }) => {
  // Helper to determine if a tier is currently active
  const isTierActive = (tier: number) => {
    if (!activeNode) return false;
    if (tier === 1 && activeNode === 'system') return true;
    if (tier === 2 && ['quant_agent', 'sentiment_agent', 'line_agent'].includes(activeNode)) return true;
    if (tier === 3 && activeNode === 'portfolio_manager') return true;
    if (tier === 4 && activeNode === 'execution_agent') return true;
    return false;
  };

  // Helper to filter logs for a specific tier
  const getLogsForTier = (tier: number) => {
    return logs.filter(log => {
      if (tier === 1) return log.node === 'system';
      if (tier === 2) return ['quant_agent', 'sentiment_agent', 'line_agent'].includes(log.node);
      if (tier === 3) return log.node === 'portfolio_manager';
      if (tier === 4) return log.node === 'execution_agent';
      return false;
    });
  };

  const renderTierColumn = (
    tier: number,
    title: string,
    glowColor: string,
    borderColor: string,
    textColor: string
  ) => {
    const active = isTierActive(tier);
    const tierLogs = getLogsForTier(tier);

    return (
      <div 
        className={`flex-1 flex flex-col cs-card overflow-hidden transition-all duration-500
          ${active ? `border-${borderColor} shadow-${glowColor}` : 'border-cs-console-border/50'}
        `}
      >
        <div className={`px-4 py-3 border-b border-cs-console-border/50 bg-cs-dark/40 ${active ? `bg-${glowColor}/5` : ''}`}>
          <div className="flex items-center justify-between">
            <span className={`text-[10px] font-black uppercase tracking-[0.2em] ${active ? `text-${textColor}` : 'text-cs-muted'}`}>
              Tier {tier}
            </span>
            {active && (
              <span className="relative flex h-2 w-2">
                <span className={`absolute inline-flex h-full w-full animate-ping rounded-full bg-${textColor} opacity-75`} />
                <span className={`relative inline-flex h-2 w-2 rounded-full bg-${textColor}`} />
              </span>
            )}
          </div>
          <h3 className={`text-xs font-semibold mt-1 uppercase tracking-wider ${active ? 'text-white' : 'text-white/70'}`}>
            {title}
          </h3>
        </div>

        <div className="flex-1 p-3 overflow-y-auto min-h-[250px] font-mono text-[10px] space-y-2 relative">
          {tierLogs.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center text-cs-muted/30">
              Awaiting signal...
            </div>
          )}
          {tierLogs.map((log, idx) => (
            <div key={idx} className="animate-fade-in flex flex-col gap-1 border-l-2 border-cs-border/30 pl-2 py-0.5">
              <span className={`${NODE_COLORS[log.node]} font-bold uppercase tracking-widest`}>
                {NODE_LABELS[log.node]} <span className="text-cs-muted ml-1 font-normal">{log.timestamp}</span>
              </span>
              <span className="text-white/80 leading-relaxed break-words">{log.message}</span>
            </div>
          ))}
          {active && (
            <div className="flex items-center gap-2 py-1">
              <span className={`h-3 w-1.5 bg-${textColor} animate-typing rounded-sm`} />
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="flex flex-col lg:flex-row w-full gap-4 h-full min-h-0">
      {renderTierColumn(1, 'Data Ingestion', 'glow-blue', 'blue-500', 'blue-400')}
      
      {/* Connector Arrow */}
      <div className="hidden lg:flex items-center justify-center shrink-0">
        <span className="text-cs-border font-black text-xl animate-pulse-slow">→</span>
      </div>

      {renderTierColumn(2, 'Specialized AI', 'glow-purple', 'purple-500', 'purple-400')}
      
      {/* Connector Arrow */}
      <div className="hidden lg:flex items-center justify-center shrink-0">
        <span className="text-cs-border font-black text-xl animate-pulse-slow">→</span>
      </div>

      {renderTierColumn(3, 'Decision Engine', 'glow-emerald', 'emerald-500', 'emerald-400')}
      
      {/* Connector Arrow */}
      <div className="hidden lg:flex items-center justify-center shrink-0">
        <span className="text-cs-border font-black text-xl animate-pulse-slow">→</span>
      </div>

      {renderTierColumn(4, 'Fulfillment', 'glow-cyan', 'cyan-500', 'cyan-400')}
    </div>
  );
};
