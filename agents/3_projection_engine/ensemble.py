import numpy as np
from scipy.stats import poisson, nbinom, norm
import xgboost as xgb
import logging

logger = logging.getLogger("EnsembleMathCore")

class EnsembleMathCore:
    def __init__(self):
        logger.info("Initializing 5-Layer Ensemble Stack (layers 1-4 + XGBoost meta-model)...")
        # Mocking the loaded XGBoost meta-model
        self.meta_model = xgb.XGBRegressor()
        self.is_cold_start = True

    def _layer1_bayesian_minutes(self, player_id):
        # Hierarchical regression: Normal likelihood, priors over season/recent/blowout factors.
        # Outputs full minutes distribution
        return np.random.normal(loc=32.0, scale=3.5, size=1000)

    def _layer2_usage_redistribution(self, team_id, missing_players):
        # Apply historical redistribution matrix with Bayesian shrinkage
        return {"usage_multiplier": 1.15} # Mock: usage increases if star is out

    def _layer3_regression_game_total(self, game_context):
        # Linear regression model for projected score total
        # projected_total = f(team_off, team_def, pace, injury_adj, ref_bias)
        return 165.5

    def _layer4_poisson_distributions(self, stat, rate, minutes_dist):
        # Per-stat Poisson (assists, steals, blocks, 3PM) and NegativeBinomial (rebounds)
        if stat in ['assists', 'steals', 'blocks', '3PM']:
            return poisson.rvs(mu=rate * (minutes_dist.mean() / 40.0), size=1000)
        elif stat == 'rebounds':
            return nbinom.rvs(n=5, p=0.5, size=1000)
        else: # points
            return norm.rvs(loc=rate * (minutes_dist.mean() / 40.0), scale=4.0, size=1000)

    def _layer6_xgboost_stacking(self, layer_outputs):
        # Trained on historical outputs of the preceding layers vs actual outcomes.
        if self.is_cold_start:
            # Fallback to weighted average if XGBoost isn't trained
            return sum(layer_outputs) / len(layer_outputs)
        # return self.meta_model.predict(layer_outputs)
        return layer_outputs[0] * 1.05 # Mocked prediction

    def run_projection(self, player_id, team_id, game_context):
        logger.info(f"Running ensemble for player {player_id}...")
        
        # 1. Minutes
        minutes_dist = self._layer1_bayesian_minutes(player_id)
        
        # 2. Usage
        usage_adj = self._layer2_usage_redistribution(team_id, game_context.get('missing_players', []))
        
        # 3. Game Total Baseline
        self._layer3_regression_game_total(game_context)
        
        # 4. Stat Distributions
        points_dist = self._layer4_poisson_distributions('points', 25.0 * usage_adj['usage_multiplier'], minutes_dist)
        
        # 5. Meta-model final weighting
        final_points_projection = self._layer6_xgboost_stacking([points_dist.mean()])
        
        return {
            "player_id": player_id,
            "projected_minutes": float(minutes_dist.mean()),
            "projected_points": float(final_points_projection),
            "confidence_interval_95": [float(np.percentile(points_dist, 2.5)), float(np.percentile(points_dist, 97.5))]
        }
