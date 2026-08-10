"""The unified memory endpoint (self-improvement plan section 17)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from wnba_apps.api.main import app


def test_procedural_memory_loads_the_checklists() -> None:
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / "ontology" / "procedures.yaml"
    procedures = yaml.safe_load(path.read_text())["procedures"]
    ids = {procedure["id"] for procedure in procedures}
    assert {"injury-review", "minutes-projection", "market-validation"} <= ids
    for procedure in procedures:
        assert procedure["steps"], f"{procedure['id']} has no steps"


def test_memory_endpoint_shape(monkeypatch) -> None:
    class _Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def execute(self, sql: str, params=None) -> None:
            self.sql = sql

        def fetchall(self):
            return []

        def fetchone(self):
            return {"precedents": 0}

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def cursor(self):
            return _Cursor()

    monkeypatch.setattr("wnba_store.db.connect", lambda: _Connection())
    client = TestClient(app)
    body = client.get("/api/learning/memory").json()
    assert body["procedural_memory"], "checklists should load from ontology/procedures.yaml"
    assert body["episodic_memory"]["stored_precedents"] == 0
    assert "causal_memory" in body and "failure_memory" in body
