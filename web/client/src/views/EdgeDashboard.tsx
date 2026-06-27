import { Zap, RotateCcw } from 'lucide-react';
import { useAgentStream } from '../hooks/useAgentStream';
import { PropSearchPanel } from '../components/agents/PropSearchPanel';
import { PipelineVisualizer } from '../components/agents/PipelineVisualizer';
import { KellyMathBreakdown } from '../components/agents/KellyMathBreakdown';
import { KellySlipCard } from '../components/agents/KellySlipCard';

export default function EdgeDashboard() {
  const { logs, result, isProcessing, activeNode, startAnalysis, reset } = useAgentStream();

  const handleSearch = (player: string, line: number, odds: number, bankroll: number) => {
    startAnalysis(player, line, odds, bankroll);
  };

  return (
    <div className="flex flex-col h-full w-full animate-fade-in bg-cs-black">
      {/* ── Top Bar ── */}
      <div className="flex items-center justify-between px-6 py-4 shrink-0 border-b border-cs-console-border/50">
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-cs-neon-cyan-glow border border-cs-neon-cyan/20">
            <Zap className="h-4 w-4 text-cs-neon-cyan-bright" />
          </div>
          <div>
            <span className="cs-badge !bg-cs-neon-cyan-glow !text-cs-neon-cyan-bright !border-cs-neon-cyan/20 uppercase tracking-widest text-[10px]">
              4-Tier Agentic Pipeline
            </span>
            <h1 className="text-sm font-semibold text-white/90 mt-0.5">CourtSideEdge Terminal</h1>
          </div>
        </div>

        {(logs.length > 0 || result) && (
          <button
            onClick={reset}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] text-cs-neon-blue font-medium uppercase tracking-wider
                       hover:bg-cs-neon-blue-glow border border-cs-neon-blue/30 transition-all"
          >
            <RotateCcw className="h-3 w-3" />
            New Analysis
          </button>
        )}
      </div>

      {/* ── Main Layout ── */}
      <div className="flex-1 flex flex-col gap-6 px-6 py-6 overflow-y-auto">
        {/* Input Panel */}
        <div className="shrink-0 max-w-4xl">
          <PropSearchPanel onSearch={handleSearch} isProcessing={isProcessing} />
        </div>

        {/* Pipeline Chain of Thought */}
        <div className="flex-1 min-h-[300px] max-w-7xl">
          <PipelineVisualizer logs={logs} activeNode={activeNode} result={result} />
        </div>

        {/* Final Results Row */}
        {result && (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 max-w-7xl animate-slide-up">
            <KellyMathBreakdown result={result} />
            <KellySlipCard result={result} />
          </div>
        )}
      </div>
    </div>
  );
}
