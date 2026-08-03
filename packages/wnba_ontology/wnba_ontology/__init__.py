"""The declared ontology, and the check that keeps it honest.

``ontology/*.yaml`` is the readable contract; ``wnba_domain`` is the executable one.
:func:`check_domain_drift` fails the build the moment they disagree.
"""

from __future__ import annotations

from wnba_ontology.loader import (
    ONTOLOGY_DIR,
    ActionSpec,
    LinkSpec,
    ObjectSpec,
    Ontology,
    OntologyDriftError,
    check_action_coverage,
    check_domain_drift,
    load_ontology,
)

__all__ = [
    "ONTOLOGY_DIR",
    "ActionSpec",
    "LinkSpec",
    "ObjectSpec",
    "Ontology",
    "OntologyDriftError",
    "check_action_coverage",
    "check_domain_drift",
    "load_ontology",
]

__version__ = "0.1.0"
