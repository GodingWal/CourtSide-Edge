import React, { useEffect, useRef } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Terminal } from 'lucide-react';
import type { AgentLogEntry, AgentNode } from '../../types/agent';
import { NODE_LABELS, NODE_COLORS } from '../../types/agent';

interface Props {
  logs: AgentLogEntry[];
  activeNode: AgentNode | null;
  isProcessing: boolean;
}

const nodeBadgeClass: Record<AgentNode, string> = {
  quant_agent: 'cs-node-badge cs-node-badge--quant',
  sentiment_agent: 'cs-node-badge cs-node-badge--news',
  line_agent: 'cs-node-badge cs-node-badge--line',
  portfolio_manager: 'cs-node-badge cs-node-badge--risk',
  execution_agent: 'cs-node-badge cs-node-badge--exec',
  system: 'cs-node-badge cs-node-badge--sys',
};

export const AgentAuditTrail: React.FC<Props> = ({ logs, activeNode, isProcessing }) => {
  const scrollRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as new thoughts stream in
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  return (
    <div className="cs-console flex flex-col h-full">
      {/* Console Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-cs-console-border shrink-0 relative z-10">
        <div className="flex items-center gap-2.5">
          <Terminal className="h-4 w-4 text-cs-emerald" />
          <span className="text-xs font-semibold text-white/70 uppercase tracking-wider">
            Agent Audit Trail
          </span>
        </div>
        <div className="flex items-center gap-3">
          {activeNode && (
            <div className="flex items-center gap-2">
              <span className="relative flex h-2 w-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-cs-emerald opacity-75" />
                <span className="relative inline-flex h-2 w-2 rounded-full bg-cs-emerald" />
              </span>
              <span className={nodeBadgeClass[activeNode]}>
                {NODE_LABELS[activeNode]}
              </span>
            </div>
          )}
          <span className="text-[10px] text-cs-muted font-mono">
            {logs.length} entries
          </span>
        </div>
      </div>

      {/* Console Body */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-4 py-3 relative z-10 min-h-0"
      >
        {/* Initial empty state */}
        {logs.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-cs-muted">
            <Terminal className="h-8 w-8 opacity-30" />
            <p className="text-xs uppercase tracking-widest">Awaiting analysis request…</p>
          </div>
        )}

        {/* Log Entries */}
        <AnimatePresence initial={false}>
          {logs.map((log, i) => (
            <motion.div
              key={`${i}-${log.timestamp}`}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              className="flex items-start gap-3 py-1.5 border-b border-cs-console-border/40 last:border-b-0"
            >
              {/* Timestamp */}
              <span className="text-[10px] text-cs-muted font-mono shrink-0 pt-0.5 tabular-nums">
                {log.timestamp}
              </span>

              {/* Node Badge */}
              <span className={`${nodeBadgeClass[log.node]} shrink-0`}>
                {NODE_LABELS[log.node]}
              </span>

              {/* Message */}
              <span className={`text-xs leading-relaxed ${NODE_COLORS[log.node]}`}>
                {log.message}
              </span>
            </motion.div>
          ))}
        </AnimatePresence>

        {/* Typing cursor */}
        {isProcessing && (
          <div className="flex items-center gap-2 py-2">
            <span className="h-3.5 w-1.5 bg-cs-emerald animate-typing rounded-sm" />
          </div>
        )}

        <div ref={endRef} />
      </div>
    </div>
  );
};
