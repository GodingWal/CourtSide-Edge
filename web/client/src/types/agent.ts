// ── Agent Stream Types ──────────────────────────────────────────────────────
// Defines the contract between the SSE backend and the frontend UI.
// Architecture: 4-Tier Multi-Agent Pipeline
//   Tier 1: Data Ingestion  → Stats API, News & Social, Bookmaker Odds
//   Tier 2: Specialized AI  → Quant Agent, Sentiment Agent, Line Agent
//   Tier 3: Decision Engine → Portfolio Manager (Kelly Criterion)
//   Tier 4: Fulfillment     → Execution Agent → Final Betting Slip

/** Which agent node is currently executing in the pipeline */
export type AgentNode =
  | 'quant_agent'
  | 'sentiment_agent'
  | 'line_agent'
  | 'portfolio_manager'
  | 'execution_agent'
  | 'system';

/** A single SSE chunk from the /api/analyze-prop endpoint */
export interface AgentStreamPayload {
  node: AgentNode;
  data: {
    messages?: string[];
    final_decision?: AgentResult;
  };
  timestamp: number;
}

/** A structured log entry rendered in the AgentAuditTrail console */
export interface AgentLogEntry {
  node: AgentNode;
  message: string;
  timestamp: string;
  confidence?: number;
}

/** The node label map for display */
export const NODE_LABELS: Record<AgentNode, string> = {
  quant_agent: 'QUANT',
  sentiment_agent: 'SENTIMENT',
  line_agent: 'LINE',
  portfolio_manager: 'PORTFOLIO',
  execution_agent: 'EXEC',
  system: 'SYS',
};

/** Color classes per node for the audit trail */
export const NODE_COLORS: Record<AgentNode, string> = {
  quant_agent: 'text-blue-400',
  sentiment_agent: 'text-amber-400',
  line_agent: 'text-purple-400',
  portfolio_manager: 'text-emerald-400',
  execution_agent: 'text-cyan-400',
  system: 'text-gray-500',
};

// ── 4-Tier Architecture ─────────────────────────────────────────────────────

export type AgentTier = 1 | 2 | 3 | 4;

export interface TierDefinition {
  tier: AgentTier;
  label: string;
  description: string;
  color: string;
  borderColor: string;
  bgColor: string;
}

export interface TierAgent {
  id: string;
  name: string;
  tier: AgentTier;
  description: string;
  status: 'online' | 'offline';
  icon: 'data' | 'ai' | 'decision' | 'execute';
}

export const TIER_DEFINITIONS: TierDefinition[] = [
  {
    tier: 1,
    label: 'Data Ingestion',
    description: 'Real-time data feeds',
    color: 'text-sky-400',
    borderColor: 'border-sky-500/20',
    bgColor: 'bg-sky-500/5',
  },
  {
    tier: 2,
    label: 'Specialized AI Agents',
    description: 'Analysis & signal generation',
    color: 'text-violet-400',
    borderColor: 'border-violet-500/20',
    bgColor: 'bg-violet-500/5',
  },
  {
    tier: 3,
    label: 'Decision Engine',
    description: 'Kelly Criterion sizing',
    color: 'text-emerald-400',
    borderColor: 'border-emerald-500/20',
    bgColor: 'bg-emerald-500/5',
  },
  {
    tier: 4,
    label: 'Fulfillment',
    description: 'Slip generation & routing',
    color: 'text-cyan-400',
    borderColor: 'border-cyan-500/20',
    bgColor: 'bg-cyan-500/5',
  },
];

/** The final decision output from the portfolio_manager agent */
export interface AgentResult {
  action: 'BET' | 'PASS';
  fraction: number;
  expected_value_pct: number;
  wager_amount: number;
  reason: string;
  confidence: number;
  player: string;
  line: number;
  odds: number;
}

/** Health status of an individual agent */
export interface AgentHealthEntry {
  id: string;
  name: string;
  tier: AgentTier;
  description: string;
  status: 'online' | 'offline';
}

/** Parameters for initiating an agent analysis */
export interface AnalysisParams {
  player: string;
  line: number;
  odds: number;
  bankroll: number;
}
