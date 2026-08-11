from __future__ import annotations

import pytest
from wnba_apps.api.main import learning_trust
from wnba_services.learning_loop.evaluation import causal_chain_payload, evaluate_models
from wnba_services.learning_loop.trust_fitting import refresh_joint_game_simulations
from wnba_store.db import connect

pytestmark = pytest.mark.postgres


def test_production_read_paths_parse_against_the_migrated_schema() -> None:
    # Empty tables are enough to catch ambiguous joins, missing columns, and malformed CTEs.
    assert refresh_joint_game_simulations() == 0
    assert evaluate_models().evaluations == 0
    payload = learning_trust()
    assert payload["available"] is True


def test_causal_chain_is_adapted_as_jsonb_not_a_postgres_array() -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT %s::jsonb AS payload",
            (causal_chain_payload(["projection too low", "cause: minutes"]),),
        )
        row = cur.fetchone()
    assert row is not None
    assert row["payload"] == ["projection too low", "cause: minutes"]
