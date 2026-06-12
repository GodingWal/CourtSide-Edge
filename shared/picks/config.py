"""Config for the pick validation pipeline.

Payout/breakeven tables, thresholds and adjustment parameters live in
`picks_config.json` (override path via PICKS_CONFIG_PATH), not in code, so
they can be refreshed when books change payouts without a deploy.
"""
import copy
import json
import os
from functools import lru_cache

DEFAULT_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "picks_config.json")


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_config(path: str | None = None) -> dict:
    """Load the picks config (cached per path). Returns a deep copy so
    callers can't mutate the cached instance."""
    resolved = path or os.getenv("PICKS_CONFIG_PATH") or DEFAULT_CONFIG_PATH
    return copy.deepcopy(_load(resolved))
