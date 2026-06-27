import { Zap, RotateCcw } from 'lucide-react';
import { useAgentStream } from '../hooks/useAgentStream';
import { PropSearchPanel } from '../components/agents/PropSearchPanel';
import { AgentAuditTrail } from '../components/agents/AgentAuditTrail';
import { KellySlipCard } from '../components/agents/KellySlipCard';
import { SystemStatus } from '../components/agents/SystemStatus';

export default function EdgeDashboard() {
  const { logs, result, isProcessing, activeNode, startAnalysis, reset } = useAgentStream();

  const handleSearch = (player: string, line: number, odds: number, bankroll: number) => {
    startAnalysis(player, line, odds, bankroll);
  };

  return (
    <div className="flex flex-col h-full w-full animate-fade-in">
      {/* ── Top Bar ── */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cs-emerald/10 border border-cs-emerald/20">
            <Zap className="h-4 w-4 text-cs-emerald" />
          </div>
          <div>
            <span className="cs-badge !bg-cs-emerald/10 !text-cs-emerald-bright !border-cs-emerald/20">
              Multi-Agent Analysis Pipeline
            </span>
          </div>
        </div>

        {(logs.length > 0 || result) && (
          <button
            onClick={reset}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] text-cs-muted font-medium uppercase tracking-wider
                       hover:bg-cs-dark hover:text-white border border-cs-border/30 hover:border-cs-border transition-all"
          >
            <RotateCcw className="h-3 w-3" />
            New Analysis
          </button>
        )}
      </div>

      {/* ── Main Layout ── */}
      <div className="flex-1 flex flex-col lg:flex-row gap-4 px-6 pb-6 min-h-0">
        {/* ── Left Column: Primary Flow ── */}
        <div className="flex-1 flex flex-col gap-4 min-h-0 min-w-0">
          {/* Prop Search Panel */}
          <div className="shrink-0">
            <PropSearchPanel onSearch={handleSearch} isProcessing={isProcessing} />
          </div>

          {/* Agent Audit Trail — grows to fill available space */}
          <div className="flex-1 min-h-[250px]">
            <AgentAuditTrail logs={logs} activeNode={activeNode} isProcessing={isProcessing} />
          </div>

          {/* Kelly Slip Card — conditional render */}
          {result && (
            <div className="shrink-0">
              <KellySlipCard result={result} />
            </div>
          )}
        </div>

        {/* ── Right Column: System Status ── */}
        <div className="w-full lg:w-[280px] xl:w-[320px] shrink-0">
          <div className="lg:sticky lg:top-[72px]">
            <SystemStatus />
          </div>
        </div>
      </div>
    </div>
  );
}
