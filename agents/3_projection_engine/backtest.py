"""Walk-forward validation for the Agent 3 ensemble (v3.1 testing protocol).

Replays stored player-games in date order: each one is projected using ONLY
history from before that game (as_of_date cut in the ensemble — no lookahead
bias), then compared to what actually happened. Produces per-stat calibration
metrics:

  mae / rmse / bias      point-projection accuracy
  coverage95             share of actuals inside the 95% interval (target 0.95)

The summary is cached in Redis (stats:model_validation) for the dashboard and
logged. Run standalone (python backtest.py) or via Agent 3's weekly thread.
"""
import json
import logging
import time

import numpy as np

from ensemble import EnsembleMathCore, MIN_GAMES, STAT_COLUMNS

logger = logging.getLogger("Agent3_Backtest")

# Cap the replay so a full run stays in the minutes range on the GPU box.
MAX_SAMPLES_PER_STAT = 150
BACKTEST_SIMS = 1500
VALIDATION_KEY = "stats:model_validation"
VALIDATION_TTL = 8 * 24 * 3600  # refreshed weekly


def _eval_rows(core: EnsembleMathCore, stat: str, stat_col: str):
    """(date, player, actual) rows in date order, newest first."""
    return core._query(
        f"""SELECT date, player_name, {stat_col} FROM player_box_scores
            WHERE minutes IS NOT NULL AND minutes > 0 AND {stat_col} IS NOT NULL
            ORDER BY date DESC LIMIT ?""",
        (MAX_SAMPLES_PER_STAT * 3,),
    )


def run_backtest(core: EnsembleMathCore | None = None, stats=None) -> dict:
    """Walk-forward backtest over stored history. Returns the metrics summary."""
    core = core or EnsembleMathCore()
    stats = stats or list(STAT_COLUMNS.keys())
    summary = {}

    for stat in stats:
        stat_col = STAT_COLUMNS[stat]
        errors, covered = [], []
        for game_date, player, actual in _eval_rows(core, stat, stat_col):
            if len(errors) >= MAX_SAMPLES_PER_STAT:
                break
            proj = core.run_projection(
                player, stat, {}, as_of_date=game_date, n_sims=BACKTEST_SIMS
            )
            if proj is None or proj["games_sampled"] < MIN_GAMES:
                continue
            err = proj["projected_value"] - float(actual)
            errors.append(err)
            lo, hi = proj["confidence_interval_95"]
            covered.append(1.0 if lo <= float(actual) <= hi else 0.0)

        if not errors:
            continue
        arr = np.array(errors)
        summary[stat] = {
            "n": len(errors),
            "mae": round(float(np.abs(arr).mean()), 3),
            "rmse": round(float(np.sqrt((arr**2).mean())), 3),
            "bias": round(float(arr.mean()), 3),
            "coverage95": round(float(np.mean(covered)), 3),
        }
        logger.info(f"Backtest {stat}: {summary[stat]}")

    return {"updated": time.time(), "per_stat": summary}


def publish_validation(result: dict):
    """Cache the validation summary in Redis for the web tier."""
    from shared.redis_client import RedisPubSub

    pubsub = RedisPubSub()
    try:
        pubsub.client.set(VALIDATION_KEY, json.dumps(result), ex=VALIDATION_TTL)
        logger.info(f"Published model validation summary ({len(result['per_stat'])} stats).")
    finally:
        pubsub.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    outcome = run_backtest()
    print(json.dumps(outcome, indent=2))
    if outcome["per_stat"]:
        try:
            publish_validation(outcome)
        except Exception as e:
            logger.warning(f"Could not publish to Redis: {e}")
