"""The ontology must stay identical to the code.

A YAML file describing an object model is useful right up until the moment it is wrong, at
which point it is actively harmful: it is the thing a reviewer reads instead of the code. These
tests make silent divergence impossible.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from wnba_ontology import (
    ActionSpec,
    ObjectSpec,
    Ontology,
    OntologyDriftError,
    check_action_coverage,
    check_domain_drift,
    load_ontology,
)
from wnba_ontology.loader import GroupSpec


@pytest.fixture(scope="module")
def ontology() -> Ontology:
    return load_ontology()


# --------------------------------------------------------------------------------------
# The live check
# --------------------------------------------------------------------------------------
def test_ontology_loads_and_is_non_trivial(ontology: Ontology) -> None:
    assert ontology.version >= 1
    assert len(ontology.objects) >= 40
    assert len(ontology.links) >= 20
    assert set(ontology.groups) == {
        "basketball",
        "identity",
        "market",
        "forecast",
        "decision",
        "research",
        "governance",
    }


def test_declared_ontology_matches_the_domain(ontology: Ontology) -> None:
    """The whole point of the file. If this fails, one of the two is lying."""
    check_domain_drift(ontology)


def test_action_layer_matches_the_action_enum(ontology: Ontology) -> None:
    check_action_coverage(ontology)


def test_every_object_has_a_description(ontology: Ontology) -> None:
    undocumented = [o.model for o in ontology.objects if len(o.description.strip()) < 10]
    assert not undocumented, f"ontology objects with no real description: {undocumented}"


# --------------------------------------------------------------------------------------
# The check must actually be capable of failing
# --------------------------------------------------------------------------------------
def _ontology_with(objects: list[ObjectSpec], base: Ontology) -> Ontology:
    return Ontology(
        version=base.version,
        groups={"only": GroupSpec(description="synthetic", objects=objects)},
        links=[],
        actions=[],
        policies={},
    )


def test_a_model_missing_from_the_yaml_is_caught(ontology: Ontology) -> None:
    """A guard that cannot fail is decoration. This proves it bites."""
    truncated = _ontology_with(list(ontology.objects)[:5], ontology)
    with pytest.raises(OntologyDriftError, match=r"absent from ontology/objects\.yaml"):
        check_domain_drift(truncated)


def test_a_yaml_object_naming_a_nonexistent_model_is_caught(ontology: Ontology) -> None:
    phantom = [
        *ontology.objects,
        ObjectSpec(
            name="ImaginaryThing",
            model="ImaginaryThing",
            temporal="none",
            description="a model that exists only in documentation",
        ),
    ]
    with pytest.raises(OntologyDriftError, match="do not exist in wnba_domain"):
        check_domain_drift(_ontology_with(phantom, ontology))


def test_downgrading_a_bitemporal_record_is_caught(ontology: Ontology) -> None:
    """The failure mode that matters most.

    A record quietly demoted from bitemporal to immutable would keep passing every other test
    in the suite while reopening the look-ahead hole the platform exists to close.
    """
    tampered = [
        ObjectSpec(
            name=o.name,
            model=o.model,
            temporal="immutable" if o.model == "InjuryStatusRecord" else o.temporal,
            description=o.description,
        )
        for o in ontology.objects
    ]
    with pytest.raises(OntologyDriftError, match="InjuryStatusRecord"):
        check_domain_drift(_ontology_with(tampered, ontology))


# --------------------------------------------------------------------------------------
# Asymmetric autonomy, declared as well as coded
# --------------------------------------------------------------------------------------
def test_no_declared_action_both_expands_exposure_and_automates(ontology: Ontology) -> None:
    offenders = [a.name for a in ontology.actions if a.automatable and a.expands_exposure]
    assert not offenders, f"actions that would let software expand exposure: {offenders}"


def test_the_declaration_itself_refuses_an_automatable_expansion() -> None:
    """Stated twice -- in the validator and in the YAML -- so that editing one is not enough.

    Pydantic wraps the raised ``OntologyDriftError`` (an ``AssertionError`` subclass) into a
    ``ValidationError``; the message is what carries through, so that is what is asserted.
    """
    with pytest.raises(ValidationError, match="only a human may expand exposure"):
        ActionSpec(
            name="promote_model",
            subject="ModelVersion",
            automatable=True,
            expands_exposure=True,
        )


@pytest.mark.parametrize("action_name", ["approve_candidate", "override_minutes", "promote_model"])
def test_exposure_expanding_actions_are_declared_human_only(
    ontology: Ontology, action_name: str
) -> None:
    spec = next(a for a in ontology.actions if a.name == action_name)
    assert spec.expands_exposure
    assert not spec.automatable
    assert "reason" in spec.requires


def test_promotion_requires_an_experiment_in_the_declaration(ontology: Ontology) -> None:
    spec = next(a for a in ontology.actions if a.name == "promote_model")
    assert "experiment_id" in spec.requires, (
        "promotion must cite the experiment that justified it; software does not grade its "
        "own homework"
    )


# --------------------------------------------------------------------------------------
# Policies
# --------------------------------------------------------------------------------------
def test_policy_invariants_name_the_test_that_enforces_them(ontology: Ontology) -> None:
    invariants = ontology.policies["invariants"]
    assert isinstance(invariants, list)
    assert len(invariants) == 7

    for inv in invariants:
        assert inv["enforced_by"], f"{inv['id']} names no enforcing module"
        assert inv["tested_by"].startswith("tests/"), (
            f"{inv['id']} claims to be an invariant but names no test; an invariant nothing "
            "checks is an aspiration"
        )


def test_real_money_gate_still_has_all_ten_criteria(ontology: Ontology) -> None:
    gate = ontology.policies["before_real_money"]
    assert len(gate["criteria"]) == 10


def test_source_etiquette_is_declared_and_polite(ontology: Ontology) -> None:
    etiquette = ontology.policies["source_etiquette"]
    assert etiquette["min_poll_interval_seconds"] >= 60
    assert etiquette["backoff_on_429"] is True
