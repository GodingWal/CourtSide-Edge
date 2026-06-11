export interface Bet {
  id: number;
  parent_id: number | null;
  is_parlay: number | null;
  player: string | null;
  stat: string | null;
  line: number | null;
  over_under: 'OVER' | 'UNDER' | null;
  book_odds: number;
  true_odds: number | null;
  edge_pct: number | null;
  stake: number;
  result: 'WIN' | 'LOSS' | 'PUSH' | null;
  actual_value: number | null;
  profit_loss: number | null;
  placed_at: number;
  settled_at: number | null;
  opposing_team: string | null;
  notes: string | null;
  is_hedge: number | null;
}

export interface BetStats {
  total_bets: number;
  wins: number;
  losses: number;
  pushes: number;
  pending: number;
  total_profit: number;
  win_rate: number;
  avg_edge: number;
  avg_clv?: number;
}

// Draft leg edited in the ticket-upload confirm form.
export interface DraftLeg {
  player: string;
  stat: string;
  line: number;
  over_under?: 'OVER' | 'UNDER';
  book_odds?: number;
  opposing_team?: string;
}

export interface GeneratedParlayLeg {
  player: string;
  team?: string;
  stat: string;
  line: number;
  over_under: 'OVER' | 'UNDER';
  book_odds: number;
  true_odds?: number | null;
  edge_pct?: number | null;
  projected_value?: number | null;
  opposing_team?: string;
  book?: string | null;
}

export interface GeneratedParlay {
  legs: GeneratedParlayLeg[];
  parlay_odds: number;
  summary: string;
  platform?: string;
  payout_multiplier?: number;
}

export const formatOdds = (odds: number) => {
  return odds > 0 ? `+${odds}` : odds.toString();
};

export const formatDate = (timestamp: number) => {
  return new Date(timestamp).toLocaleDateString('en-US', {
    month: '2-digit',
    day: '2-digit',
    year: '2-digit'
  });
};
