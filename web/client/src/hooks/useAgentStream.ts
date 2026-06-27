import { useState, useCallback, useRef } from 'react';
import type { AgentLogEntry, AgentResult, AgentStreamPayload, AgentNode } from '../types/agent';

interface UseAgentStreamReturn {
  logs: AgentLogEntry[];
  result: AgentResult | null;
  isProcessing: boolean;
  activeNode: AgentNode | null;
  startAnalysis: (player: string, line: number, odds: number, bankroll: number) => void;
  reset: () => void;
}

export const useAgentStream = (): UseAgentStreamReturn => {
  const [logs, setLogs] = useState<AgentLogEntry[]>([]);
  const [result, setResult] = useState<AgentResult | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [activeNode, setActiveNode] = useState<AgentNode | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const reset = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
    setLogs([]);
    setResult(null);
    setIsProcessing(false);
    setActiveNode(null);
  }, []);

  const startAnalysis = useCallback((player: string, line: number, odds: number, bankroll: number) => {
    // Clean up any existing connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    setIsProcessing(true);
    setLogs([]);
    setResult(null);
    setActiveNode('system');

    // Add system initialization log
    const initLog: AgentLogEntry = {
      node: 'system',
      message: `Initializing agent network for ${player} — Line: ${line}, Odds: ${odds > 0 ? '+' : ''}${odds}`,
      timestamp: new Date().toISOString().split('T')[1].slice(0, 12),
    };
    setLogs([initLog]);

    const params = new URLSearchParams({
      player,
      line: line.toString(),
      odds: odds.toString(),
      bankroll: bankroll.toString(),
    });

    const url = `/api/analyze-prop?${params.toString()}`;
    const eventSource = new EventSource(url);
    eventSourceRef.current = eventSource;

    eventSource.onmessage = (event) => {
      try {
        const payload: AgentStreamPayload = JSON.parse(event.data);

        setActiveNode(payload.node);

        // Accumulate log entries
        if (payload.data.messages) {
          const newEntries: AgentLogEntry[] = payload.data.messages.map((msg) => ({
            node: payload.node,
            message: msg,
            timestamp: new Date(payload.timestamp).toISOString().split('T')[1].slice(0, 12),
          }));
          setLogs((prev) => [...prev, ...newEntries]);
        }

        // Extract final result from execution_agent (Tier 4: Fulfillment)
        if (payload.node === 'execution_agent' && payload.data.final_decision) {
          setResult(payload.data.final_decision);
          setIsProcessing(false);
          setActiveNode(null);
          eventSource.close();
          eventSourceRef.current = null;
        }
      } catch {
        // Silently handle malformed SSE chunks
      }
    };

    eventSource.onerror = () => {
      const errorLog: AgentLogEntry = {
        node: 'system',
        message: '❌ Connection to agent network lost. Retry analysis.',
        timestamp: new Date().toISOString().split('T')[1].slice(0, 12),
      };
      setLogs((prev) => [...prev, errorLog]);
      setIsProcessing(false);
      setActiveNode(null);
      eventSource.close();
      eventSourceRef.current = null;
    };
  }, []);

  return { logs, result, isProcessing, activeNode, startAnalysis, reset };
};
