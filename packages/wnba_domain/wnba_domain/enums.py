"""Closed vocabularies for the ontology.

Every one of these is a deliberate constraint. A free-text ``status`` column is where a data
model goes to die: it accumulates ``"Out"``, ``"out"``, ``"OUT"``, ``"Out (rest)"`` and then
somebody writes a ``.lower().startswith("out")`` and calls it a feature.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "ActionType",
    "AgentRole",
    "ClaimStatus",
    "DriftResponse",
    "EntryType",
    "ErrorCategory",
    "ErrorCode",
    "FeedbackType",
    "GameStatus",
    "HypothesisStatus",
    "InjuryDesignation",
    "MarketKind",
    "ModelStage",
    "Position",
    "PropType",
    "RecommendationStatus",
    "Severity",
    "Side",
    "ValidationLevel",
    "Venue",
]


# --------------------------------------------------------------------------------------
# Basketball
# --------------------------------------------------------------------------------------
class Position(StrEnum):
    GUARD = "G"
    FORWARD = "F"
    CENTER = "C"
    GUARD_FORWARD = "G-F"
    FORWARD_CENTER = "F-C"
    UNKNOWN = "UNKNOWN"


class Venue(StrEnum):
    HOME = "home"
    AWAY = "away"
    NEUTRAL = "neutral"


class GameStatus(StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    HALFTIME = "halftime"
    FINAL = "final"
    POSTPONED = "postponed"
    CANCELED = "canceled"


class InjuryDesignation(StrEnum):
    """Official-ish availability language, normalised.

    Note the absence of anything resembling ``CONFIRMED_STARTER``. The WNBA does not require
    pre-tip lineup submission, so starter status is never a fact we possess -- only a
    probability we estimate. See :mod:`wnba_domain.basketball`.
    """

    AVAILABLE = "available"
    PROBABLE = "probable"
    QUESTIONABLE = "questionable"
    DOUBTFUL = "doubtful"
    OUT = "out"
    GAME_TIME_DECISION = "game_time_decision"
    NOT_WITH_TEAM = "not_with_team"
    SEASON_ENDING = "season_ending"
    UNKNOWN = "unknown"


# --------------------------------------------------------------------------------------
# Markets
# --------------------------------------------------------------------------------------
class PropType(StrEnum):
    POINTS = "points"
    REBOUNDS = "rebounds"
    ASSISTS = "assists"
    THREE_POINTERS = "three_pointers"
    STEALS = "steals"
    BLOCKS = "blocks"
    TURNOVERS = "turnovers"
    MINUTES = "minutes"
    FIELD_GOALS_MADE = "field_goals_made"
    FREE_THROWS_MADE = "free_throws_made"
    POINTS_REBOUNDS_ASSISTS = "points_rebounds_assists"
    POINTS_REBOUNDS = "points_rebounds"
    POINTS_ASSISTS = "points_assists"
    REBOUNDS_ASSISTS = "rebounds_assists"
    STEALS_BLOCKS = "steals_blocks"
    FANTASY_POINTS = "fantasy_points"


class Side(StrEnum):
    OVER = "over"
    UNDER = "under"


class MarketKind(StrEnum):
    """How a price is expressed -- which determines how expected value is computed."""

    SPORTSBOOK_TWO_SIDED = "sportsbook_two_sided"
    """Two American prices. Fair probability is recoverable by removing vig."""

    PICKEM = "pickem"
    """DFS pick'em. No two-sided price; value comes from a fixed payout table over N legs."""


class EntryType(StrEnum):
    """Pick'em entry shapes. Flex pays partial credit, so its EV needs the full distribution
    over the number of correct legs -- not just the all-correct probability."""

    POWER = "power"
    FLEX = "flex"


# --------------------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------------------
class ValidationLevel(StrEnum):
    SCHEMA = "schema"
    REFERENTIAL = "referential"
    TEMPORAL = "temporal"
    BASKETBALL = "basketball"
    MARKET = "market"
    CROSS_SOURCE = "cross_source"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    BLOCKING = "blocking"


# --------------------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------------------
class RecommendationStatus(StrEnum):
    CANDIDATE = "candidate"
    RECOMMENDED = "recommended"
    BLOCKED_DATA_QUALITY = "blocked_data_quality"
    BLOCKED_STALE_QUOTE = "blocked_stale_quote"
    BLOCKED_EXPOSURE = "blocked_exposure"
    BLOCKED_CALIBRATION = "blocked_calibration"
    BLOCKED_BY_SKEPTIC = "blocked_by_skeptic"
    DECLINED_NO_EDGE = "declined_no_edge"
    EXPIRED = "expired"


# --------------------------------------------------------------------------------------
# Learning loop
# --------------------------------------------------------------------------------------
class ErrorCategory(StrEnum):
    """Top-level attribution buckets. A losing bet is not automatically a bad forecast --
    39% outcomes occur roughly 39% of the time, whatever the gambler's sense of injustice."""

    DATA = "data"
    MINUTES = "minutes"
    OPPORTUNITY = "opportunity"
    EFFICIENCY = "efficiency"
    MARKET = "market"
    MODELING = "modeling"
    REASONING = "reasoning"
    IRREDUCIBLE_VARIANCE = "irreducible_variance"


class ErrorCode(StrEnum):
    # data
    STALE_INJURY_STATUS = "stale_injury_status"
    STALE_QUOTE = "stale_quote"
    DUPLICATE_PLAYER_IDENTITY = "duplicate_player_identity"
    UNRESOLVED_ALIAS = "unresolved_alias"
    MISSING_ROSTER_TRANSACTION = "missing_roster_transaction"
    GAME_MAPPING_MISMATCH = "game_mapping_mismatch"
    SOURCE_UNAVAILABLE = "source_unavailable"
    # minutes
    UNEXPECTED_BENCHING = "unexpected_benching"
    FOUL_TROUBLE = "foul_trouble"
    IN_GAME_INJURY = "in_game_injury"
    BLOWOUT_MINUTES_LOST = "blowout_minutes_lost"
    OVERTIME_MINUTES_GAINED = "overtime_minutes_gained"
    ROTATION_CHANGE_NOT_DETECTED = "rotation_change_not_detected"
    MINUTES_RESTRICTION_MISSED = "minutes_restriction_missed"
    # opportunity
    USAGE_PROJECTION_WRONG = "usage_projection_wrong"
    TEAMMATE_ROLE_CHANGED = "teammate_role_changed"
    SCHEME_CHANGED = "scheme_changed"
    PACE_PROJECTION_WRONG = "pace_projection_wrong"
    # efficiency
    SHOOTING_VARIANCE = "shooting_variance"
    DEFENDER_ASSIGNMENT_CHANGED = "defender_assignment_changed"
    SHOT_PROFILE_CHANGED = "shot_profile_changed"
    # market
    EDGE_GONE_BEFORE_LOCK = "edge_gone_before_lock"
    PAYOUT_TABLE_MISREAD = "payout_table_misread"
    LINE_MOVED_AGAINST_THESIS = "line_moved_against_thesis"
    # modeling
    POOR_CALIBRATION = "poor_calibration"
    FEATURE_DRIFT = "feature_drift"
    OVERFITTING = "overfitting"
    WRONG_DISTRIBUTION_FAMILY = "wrong_distribution_family"
    CORRELATION_UNDERESTIMATED = "correlation_underestimated"
    INTERVAL_TOO_NARROW = "interval_too_narrow"
    # reasoning
    UNSUPPORTED_CLAIM = "unsupported_claim"
    STRONG_EVIDENCE_IGNORED = "strong_evidence_ignored"
    WEAK_EVIDENCE_OVERWEIGHTED = "weak_evidence_overweighted"
    CAUSAL_DIRECTION_REVERSED = "causal_direction_reversed"
    AGENT_ANCHORING = "agent_anchoring"
    UNJUSTIFIED_OVERRIDE = "unjustified_override"


class ClaimStatus(StrEnum):
    PROPOSED = "proposed"
    VERIFIED = "verified"
    REJECTED_UNCITED = "rejected_uncited"
    REJECTED_CONTRADICTED = "rejected_contradicted"
    EXPIRED = "expired"


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    TESTING = "testing"
    SUPPORTED = "supported"
    CONDITIONAL = "conditional"
    CONTRADICTED = "contradicted"
    DEPRECATED = "deprecated"


class FeedbackType(StrEnum):
    ACCEPTED = "accepted"
    REJECTED_BAD_DATA = "rejected_bad_data"
    REJECTED_MINUTES = "rejected_minutes"
    REJECTED_MATCHUP = "rejected_matchup"
    REJECTED_PRICE = "rejected_price"
    REJECTED_UNCERTAINTY = "rejected_uncertainty"
    OVERRIDE_HIGHER = "override_higher"
    OVERRIDE_LOWER = "override_lower"
    MISSING_EVIDENCE = "missing_evidence"
    EXPLANATION_UNCLEAR = "explanation_unclear"


class AgentRole(StrEnum):
    COORDINATOR = "coordinator"
    AVAILABILITY = "availability"
    ROTATION = "rotation"
    MATCHUP = "matchup"
    MARKET = "market"
    SKEPTIC = "skeptic"
    DATA_AUDITOR = "data_auditor"
    SYNTHESIZER = "synthesizer"
    EPISODE_REVIEWER = "episode_reviewer"
    RESEARCH_DIRECTOR = "research_director"


# --------------------------------------------------------------------------------------
# Governance
# --------------------------------------------------------------------------------------
class ModelStage(StrEnum):
    CHAMPION = "champion"
    CHALLENGER = "challenger"
    SHADOW = "shadow"
    DEPRECATED = "deprecated"


class DriftResponse(StrEnum):
    """Escalation ladder. Note that every rung reduces exposure. There is deliberately no
    automatic path that increases it -- see the asymmetric-autonomy invariant."""

    REDUCE_CONFIDENCE = "reduce_confidence"
    REDUCE_STAKE = "reduce_stake"
    DISABLE_MARKET = "disable_market"
    REQUIRE_MANUAL_REVIEW = "require_manual_review"


class ActionType(StrEnum):
    """Palantir-style first-class actions. Every one is recorded with actor, prior state, new
    state, reason and evidence."""

    APPROVE_CANDIDATE = "approve_candidate"
    REJECT_CANDIDATE = "reject_candidate"
    MARK_SOURCE_STALE = "mark_source_stale"
    OVERRIDE_MINUTES = "override_minutes"
    FREEZE_GAME = "freeze_game"
    RECOMPUTE_AFTER_NEWS = "recompute_after_news"
    DISABLE_SOURCE = "disable_source"
    PROMOTE_MODEL = "promote_model"
    ROLLBACK_MODEL = "rollback_model"
    RECORD_DISAGREEMENT = "record_disagreement"
    SETTLE_EPISODE = "settle_episode"
