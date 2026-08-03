"""Load and validate the ontology YAML, and bind it to the executable domain.

The YAML files are the readable contract; ``wnba_domain`` is the executable one. Documentation
that can silently disagree with the code is worse than no documentation, because it gets
trusted. :func:`check_domain_drift` is what makes disagreement impossible: it fails the build
the moment a model is added, renamed or removed without the ontology following.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal, Self

import wnba_domain
import yaml
from pydantic import Field, model_validator
from wnba_domain.base import StrictModel
from wnba_domain.enums import ActionType

__all__ = [
    "ONTOLOGY_DIR",
    "ActionSpec",
    "LinkSpec",
    "ObjectSpec",
    "Ontology",
    "OntologyDriftError",
    "check_domain_drift",
    "load_ontology",
]

ONTOLOGY_DIR = Path(__file__).resolve().parents[3] / "ontology"

Temporal = Literal["bitemporal", "immutable", "none"]


class OntologyDriftError(AssertionError):
    """Raised when the declared ontology and the Pydantic domain disagree."""


class ObjectSpec(StrictModel):
    name: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temporal: Temporal
    description: str = ""


class GroupSpec(StrictModel):
    description: str = ""
    objects: list[ObjectSpec] = Field(min_length=1)


class LinkSpec(StrictModel):
    name: str = Field(min_length=1)
    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    cardinality: str = Field(min_length=1)
    via: str | None = None
    field: list[str] | None = None
    note: str = ""


class ActionSpec(StrictModel):
    name: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    automatable: bool
    expands_exposure: bool
    requires: list[str] = Field(default_factory=list)
    description: str = ""

    @model_validator(mode="after")
    def _autonomy_is_asymmetric(self) -> Self:
        """The invariant, checked in the declaration as well as in the code.

        Two independent statements of the same rule that must agree is the cheapest possible
        guard against one of them being quietly edited.
        """
        if self.automatable and self.expands_exposure:
            raise OntologyDriftError(
                f"action {self.name!r} both expands exposure and claims to be automatable; "
                "only a human may expand exposure"
            )
        return self


class Ontology(StrictModel):
    version: Annotated[int, Field(ge=1)]
    groups: dict[str, GroupSpec]
    links: list[LinkSpec]
    actions: list[ActionSpec]
    policies: dict[str, Any]

    @property
    def objects(self) -> list[ObjectSpec]:
        return [obj for group in self.groups.values() for obj in group.objects]

    def object_for(self, model_name: str) -> ObjectSpec:
        for obj in self.objects:
            if obj.model == model_name:
                return obj
        raise LookupError(f"no ontology object declares model {model_name!r}")


def _read(name: str, directory: Path) -> dict[str, Any]:
    path = directory / name
    if not path.exists():
        raise FileNotFoundError(f"ontology file missing: {path}")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a mapping at the top level")
    return loaded


def load_ontology(directory: Path | None = None) -> Ontology:
    """Parse the four ontology files into one validated object."""
    root = directory or ONTOLOGY_DIR
    objects_doc = _read("objects.yaml", root)
    links_doc = _read("links.yaml", root)
    actions_doc = _read("actions.yaml", root)
    policies_doc = _read("policies.yaml", root)

    return Ontology(
        version=int(objects_doc["version"]),
        groups={
            key: GroupSpec.model_validate(value) for key, value in objects_doc["groups"].items()
        },
        links=[LinkSpec.model_validate(item) for item in links_doc["links"]],
        actions=[ActionSpec.model_validate(item) for item in actions_doc["actions"]],
        policies=policies_doc,
    )


def _declared_models(ontology: Ontology) -> set[str]:
    return {obj.model for obj in ontology.objects}


def _domain_models() -> set[str]:
    """Every Pydantic model exported by ``wnba_domain``, excluding shared base classes.

    Base classes are machinery, not ontology objects: ``StrictModel`` is how objects are
    expressed, not a thing that exists in basketball.
    """
    bases = {"StrictModel", "FrozenModel", "BitemporalRecord"}
    found: set[str] = set()
    for name in wnba_domain.__all__:
        obj = getattr(wnba_domain, name)
        if isinstance(obj, type) and issubclass(obj, StrictModel) and name not in bases:
            found.add(name)
    return found


def check_domain_drift(ontology: Ontology | None = None) -> None:
    """Assert the declared ontology and the Pydantic domain are the same thing.

    Checks three ways they can diverge:

    1. A model exists in code but no ontology object declares it.
    2. An ontology object names a model that does not exist.
    3. An object's declared ``temporal`` kind contradicts what the class actually inherits.

    The third is the one that matters most in practice. A record silently downgraded from
    bitemporal to immutable would keep passing every other test in the suite while quietly
    reopening the look-ahead hole the whole platform is built to close.
    """
    resolved = ontology or load_ontology()
    declared = _declared_models(resolved)
    actual = _domain_models()

    undeclared = actual - declared
    if undeclared:
        raise OntologyDriftError(
            f"models exist in wnba_domain but are absent from ontology/objects.yaml: "
            f"{sorted(undeclared)}"
        )

    phantom = declared - actual
    if phantom:
        raise OntologyDriftError(
            f"ontology/objects.yaml declares models that do not exist in wnba_domain: "
            f"{sorted(phantom)}"
        )

    mismatched: list[str] = []
    for obj in resolved.objects:
        model = getattr(wnba_domain, obj.model)
        is_bitemporal = issubclass(model, wnba_domain.BitemporalRecord)
        is_frozen = bool(model.model_config.get("frozen", False))

        if obj.temporal == "bitemporal" and not is_bitemporal:
            mismatched.append(f"{obj.model}: declared bitemporal but does not inherit both axes")
        elif obj.temporal == "immutable" and (is_bitemporal or not is_frozen):
            mismatched.append(f"{obj.model}: declared immutable but is not a plain frozen model")
        elif obj.temporal == "none" and is_bitemporal:
            mismatched.append(f"{obj.model}: declared non-temporal but is bitemporal")

    if mismatched:
        raise OntologyDriftError(
            "temporal declarations disagree with the code: " + "; ".join(mismatched)
        )


def check_action_coverage(ontology: Ontology | None = None) -> None:
    """Every declared action must exist in the ``ActionType`` enum, and vice versa."""
    resolved = ontology or load_ontology()
    declared = {a.name for a in resolved.actions}
    actual = {a.value for a in ActionType}

    if declared != actual:
        raise OntologyDriftError(
            f"action layer drift -- only in YAML: {sorted(declared - actual)}; "
            f"only in ActionType: {sorted(actual - declared)}"
        )
