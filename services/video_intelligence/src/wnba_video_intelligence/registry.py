"""Load and validate the video feature registry.

Every candidate feature declares its metric and promotion threshold *before* anyone measures
it. That ordering is the entire point: a threshold chosen after seeing the result is not a
threshold, it is a rationalisation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = ["FeatureSpec", "REGISTRY_PATH", "load_registry"]

REGISTRY_PATH = Path(__file__).resolve().parents[2] / "feature_registry.yaml"


@dataclass(frozen=True)
class FeatureSpec:
    name: str
    phase: int
    labels: tuple[str, ...]
    metric: str
    promotion_threshold: float

    @property
    def is_experimental(self) -> bool:
        """Nothing here is production until a human promotes it. Always True by design."""
        return True


def load_registry(path: Path | None = None) -> list[FeatureSpec]:
    raw: dict[str, Any] = yaml.safe_load((path or REGISTRY_PATH).read_text(encoding="utf-8"))

    if raw.get("production_dependency") is not False:
        raise ValueError(
            "feature_registry.yaml must declare production_dependency: false -- this sandbox "
            "may never become a hard dependency of the forecasting path"
        )

    specs: list[FeatureSpec] = []
    for entry in raw.get("features", []):
        specs.append(
            FeatureSpec(
                name=str(entry["name"]),
                phase=int(entry["phase"]),
                labels=tuple(str(label) for label in entry["labels"]),
                metric=str(entry["metric"]),
                promotion_threshold=float(entry["promotion_threshold"]),
            )
        )
    if not specs:
        raise ValueError("feature registry declares no features")

    names = [s.name for s in specs]
    if len(set(names)) != len(names):
        raise ValueError("duplicate feature names in registry")
    return specs
