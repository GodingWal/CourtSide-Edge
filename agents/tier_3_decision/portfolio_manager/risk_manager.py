import json
from shared.odds_math import kelly_criterion
from agents.tier_2_ai.sentiment_agent.langchain_parser import PlayerStatus

class AgenticRiskManager:
    def __init__(self, max_wager_pct: float = 0.05):
        self.max_wager_pct = max_wager_pct

    def evaluate_and_size_bet(self, quant_win_prob: float, american_odds: int, news_context: PlayerStatus) -> dict:
        """
        Combines the baseline quant probability with the LangChain news sentiment,
        then calculates the exact bet size using the Kelly Criterion.
        """
        print(f"Baseline Win Probability: {quant_win_prob}")
        
        # 1. Adjust probability based on the LangChain News Agent's structured output
        adjusted_prob = quant_win_prob
        if news_context.impact_score > 0:
            print(f"News Alert: {news_context.player_name} is {news_context.status}.")
            print(f"Applying penalty of -{news_context.impact_score}")
            adjusted_prob -= news_context.impact_score
            
        # Bound the probability between 0 and 1
        adjusted_prob = max(0.0, min(1.0, adjusted_prob))
        
        # 2. Use the imported math module to calculate sizing
        # Using a conservative Quarter Kelly (0.25)
        recommended_size = kelly_criterion(
            win_prob=adjusted_prob,
            american_odds=american_odds,
            kelly_multiplier=0.25,
            max_wager_pct=self.max_wager_pct
        )
        
        return {
            "final_win_prob": adjusted_prob,
            "odds": american_odds,
            "recommended_bankroll_pct": recommended_size,
            "action": "EXECUTE" if recommended_size > 0 else "PASS",
            "reasoning": f"Adjusted prob {adjusted_prob:.2f} against odds {american_odds}. News impact: {news_context.impact_score}"
        }

if __name__ == "__main__":
    # Mock data from the Quant pipeline and LangChain News Agent
    risk_manager = AgenticRiskManager()
    
    mock_news_output = PlayerStatus(
        player_name="LeBron James",
        status="Minutes Restriction",
        impact_score=0.15,
        is_confirmed=True
    )
    
    # The quant model loved the over, but the news agent caught the restriction
    decision = risk_manager.evaluate_and_size_bet(
        quant_win_prob=0.62, 
        american_odds=-110,
        news_context=mock_news_output
    )
    
    print(json.dumps(decision, indent=2))
