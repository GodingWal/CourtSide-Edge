"""Isolated video-intelligence sandbox. Never a production dependency.

Excluded from the uv workspace and imported by nothing in the forecasting path. That
isolation is the design: computer vision on broadcast footage is speculative, and a
speculative sensor must never be able to break, block or silently bias a forecast.

Each candidate feature earns its way in one at a time, against a metric and threshold fixed
in advance in ``feature_registry.yaml``.
"""

from wnba_video_intelligence.contracts import (
    CourtCalibration,
    Detection,
    ReviewItem,
    TrackPoint,
    VideoEvent,
)
from wnba_video_intelligence.evaluation import (
    Association,
    TrackingReport,
    evaluate_tracking,
)
from wnba_video_intelligence.registry import REGISTRY_PATH, FeatureSpec, load_registry

__all__ = [
    "REGISTRY_PATH",
    "Association",
    "CourtCalibration",
    "Detection",
    "FeatureSpec",
    "ReviewItem",
    "TrackPoint",
    "TrackingReport",
    "VideoEvent",
    "evaluate_tracking",
    "load_registry",
]

__version__ = "0.1.0"
