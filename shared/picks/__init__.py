"""Pick validation & statistical rigor toolkit.

Lightweight exports only (stdlib + pydantic); numpy/scipy-backed modules
(`distributions`, `correlation`) are imported explicitly by callers that
need them so slim agent images don't drag in the scientific stack.
"""
from shared.picks.breakeven import breakeven_probability, publish_threshold
from shared.picks.config import load_config
from shared.picks.models import (
    NarrativePayload,
    Pick,
    PickStatus,
    Recommendation,
    ValidationResult,
)
from shared.picks.reason_codes import ReasonCode
from shared.picks.validation import validate_pick

__all__ = [
    "NarrativePayload",
    "Pick",
    "PickStatus",
    "ReasonCode",
    "Recommendation",
    "ValidationResult",
    "breakeven_probability",
    "load_config",
    "publish_threshold",
    "validate_pick",
]
