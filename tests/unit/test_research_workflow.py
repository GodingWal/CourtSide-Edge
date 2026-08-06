"""The contract between the research workflow and its store.

Two properties here are worth more than the rest. The first is ordering: the skeptic must be
called after the analysts and must be handed their conclusions, because a skeptic that sees only
the raw evidence is a fifth analyst with a gloomy prompt. The second is that nothing is committed
after the provider is dialled -- the frozen evidence and the ``running`` row go in first, so a run
that dies over the wire leaves a record instead of a gap, and a second caller can see that money
is already being spent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from wnba_services.research_agents import workflow
from wnba_services.research_agents.deepseek import AgentAnalysis, AgentResponse, Usage
from wnba_services.research_agents.workflow import (
    ANALYST_QUESTIONS,
    SKEPTIC_ROLE,
    run_projection_research,
)

from tests.fixtures.fake_db import FakeDatabase

NOW = datetime(2026, 8, 5, 18, 0, tzinfo=UTC)
PROJECTION_ID = uuid4()
PLAYER_ID = uuid4()
GAME_ID = uuid4()


def projection_row() -> dict[str, Any]:
    return {
        "projection_id": PROJECTION_ID,
        "player_id": PLAYER_ID,
        "full_name": "A Player",
        "prop_type": "points",
        "line": 15.5,
        "source": "underdog",
        "observed_at": NOW - timedelta(hours=2),
        "locks_at": NOW + timedelta(hours=3),
        "mean": 16.1,
        "median": 16.0,
        "stddev": 5.2,
        "model_disagreement": 0.11,
        "data_quality_score": 0.82,
        # The shared evidence builder also reads these; a projection row without them is not a
        # row this pipeline ever sees.
        "game_id": GAME_ID,
        "generated_at": NOW - timedelta(minutes=5),
        "probability_over": 0.54,
        "over_multiplier": 1.9,
        "under_multiplier": 1.9,
        "features": {
            "injury_designation": "questionable",
            "expected_minutes": 28.4,
            "start_probability": 0.7,
            "expected_possessions": 96.0,
        },
    }


def database(**overrides: list[dict[str, Any]]) -> FakeDatabase:
    db = FakeDatabase().when("FROM wnba.stat_forecasts f", [projection_row()])
    for match, rows in overrides.items():
        db.when(match.replace("__", " "), rows)
    return db


class ScriptedProvider:
    """Stands in for DeepSeek, recording the evidence each role was actually shown."""

    model = "deepseek-test"
    timeout_seconds = 1.0
    max_attempts = 1

    def __init__(self, skeptic_contradicts: list[UUID] | None = None) -> None:
        self.seen: list[tuple[str, set[UUID]]] = []
        self._skeptic_contradicts = skeptic_contradicts or []
        # The advisory recorder reads the client's last-call usage when it writes its row.
        self.usage = Usage(prompt_tokens=100, completion_tokens=20, attempts=1)

    def analyze(
        self,
        *,
        role: str,
        question: str,
        evidence: dict[UUID, str],
        directive: str = "",
    ) -> AgentResponse:
        self.seen.append((role, set(evidence)))
        contradicts = self._skeptic_contradicts if role == SKEPTIC_ROLE else []
        cited = next(v for v in sorted(evidence, key=str) if v not in contradicts)
        analysis = AgentAnalysis.model_validate(
            {
                "conclusion": f"{role} conclusion.",
                "confidence": 0.6,
                "claims": [
                    {
                        "predicate": f"{role}_finding",
                        "value": "a value",
                        "confidence": 0.6,
                        "evidence_ids": [cited],
                        "contradicting_evidence_ids": list(contradicts),
                    }
                ],
                "risk_flags": [],
            }
        )
        return AgentResponse(
            analysis=analysis,
            prompt_sha256="a" * 64,
            response_sha256="b" * 64,
            usage=Usage(prompt_tokens=100, completion_tokens=20),
            attempts=1,
        )


def run(db: FakeDatabase, provider: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(workflow, "connect", db.connect)
    return run_projection_research(PROJECTION_ID, client=provider, now=NOW)


# --------------------------------------------------------------------------------------
# Two-stage ordering
# --------------------------------------------------------------------------------------
def test_the_skeptic_runs_last_and_sees_the_analyst_conclusions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider()
    run(database(), provider, monkeypatch)

    roles = [role for role, _ in provider.seen]
    assert sorted(roles[:-1]) == sorted(ANALYST_QUESTIONS)
    assert roles[-1] == SKEPTIC_ROLE

    analyst_evidence = provider.seen[0][1]
    skeptic_evidence = provider.seen[-1][1]
    assert analyst_evidence < skeptic_evidence
    assert len(skeptic_evidence - analyst_evidence) == len(ANALYST_QUESTIONS)


def test_every_analyst_sees_the_same_frozen_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    """Independence means identical inputs, not shared ones -- no analyst reads another."""
    provider = ScriptedProvider()
    run(database(), provider, monkeypatch)
    analyst_views = [seen for role, seen in provider.seen if role != SKEPTIC_ROLE]
    assert all(view == analyst_views[0] for view in analyst_views)


def test_the_conclusions_shown_to_the_skeptic_are_stored_as_citable_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ScriptedProvider()
    db = database()
    run(db, provider, monkeypatch)

    stored = {params[0] for params in db.params_for("INSERT INTO wnba.evidence")}
    skeptic_evidence = provider.seen[-1][1]
    assert skeptic_evidence <= stored, "the skeptic cited evidence that was never recorded"


# --------------------------------------------------------------------------------------
# Transaction boundaries
# --------------------------------------------------------------------------------------
def test_evidence_and_the_running_row_are_committed_before_the_provider_is_called(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = database()

    class CommitWatcher(ScriptedProvider):
        def analyze(self, **kwargs: Any) -> AgentResponse:
            assert db.connections[0].committed, "the provider was called inside an open transaction"
            return super().analyze(**kwargs)

    run(db, CommitWatcher(), monkeypatch)
    header = db.executed("INSERT INTO wnba.research_runs")
    assert len(header) == 1
    assert "'running'" in header[0][0]


def test_a_run_already_in_flight_is_not_paid_for_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    db = database()
    db.when(
        "AND status='running'",
        [{"research_run_id": uuid4(), "started_at": NOW - timedelta(seconds=30)}],
    )
    provider = ScriptedProvider()
    with pytest.raises(ValueError, match="already running"):
        run(db, provider, monkeypatch)
    assert provider.seen == []


def test_an_abandoned_run_is_failed_and_the_projection_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    abandoned = uuid4()
    db = database()
    db.when(
        "AND status='running'",
        [{"research_run_id": abandoned, "started_at": NOW - timedelta(hours=6)}],
    )
    provider = ScriptedProvider()
    run(db, provider, monkeypatch)
    failures = [p for p in db.params_for("SET status='failed'") if p[1] == abandoned]
    assert failures, "the abandoned run was left claiming to be running"
    assert provider.seen != []


def test_a_total_provider_failure_marks_the_run_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken(ScriptedProvider):
        def analyze(self, **kwargs: Any) -> AgentResponse:
            raise RuntimeError("DeepSeek did not respond after 3 attempts")

    db = database()
    with pytest.raises(RuntimeError, match="every research analyst failed"):
        run(db, Broken(), monkeypatch)
    assert db.ran("SET status='failed'")
    assert not db.ran("SET status='complete'")


def test_one_failed_analyst_is_a_missing_voice_not_a_lost_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other three answers are already paid for; discarding them buys nothing."""

    class OneDown(ScriptedProvider):
        def analyze(self, **kwargs: Any) -> AgentResponse:
            if kwargs["role"] == "rotation":
                raise RuntimeError("DeepSeek did not respond after 3 attempts")
            return super().analyze(**kwargs)

    db = database()
    batch = run(db, OneDown(), monkeypatch)
    assert db.ran("SET status='complete'")
    # Three analysts plus the skeptic; the absent one is recorded, not silently dropped.
    assert batch.analyses == len(ANALYST_QUESTIONS)
    advisories = db.params_for("INSERT INTO wnba.model_advisories")
    assert len(advisories) == len(ANALYST_QUESTIONS) + 1
    assert any("rotation" in str(p[2]) and p[7] == "fallback" for p in advisories)


def test_a_failed_skeptic_leaves_no_verdict_rather_than_a_flattering_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An uncontested verdict and an unreviewed one are indistinguishable once written down."""

    class NoSkeptic(ScriptedProvider):
        def analyze(self, **kwargs: Any) -> AgentResponse:
            if kwargs["role"] == SKEPTIC_ROLE:
                raise RuntimeError("DeepSeek did not respond after 3 attempts")
            return super().analyze(**kwargs)

    db = database()
    batch = run(db, NoSkeptic(), monkeypatch)
    assert db.ran("SET status='complete'")
    assert batch.verdict is None
    assert not db.ran("INSERT INTO wnba.research_verdicts")


def test_research_is_refused_once_the_market_has_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    locked = projection_row() | {"locks_at": NOW - timedelta(minutes=1)}
    db = FakeDatabase().when("FROM wnba.stat_forecasts f", [locked])
    provider = ScriptedProvider()
    with pytest.raises(ValueError, match="after the market locks"):
        run(db, provider, monkeypatch)
    assert provider.seen == []


# --------------------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------------------
def test_the_verdict_is_computed_and_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider()
    db = database()
    batch = run(db, provider, monkeypatch)

    assert batch.analyses == len(ANALYST_QUESTIONS) + 1
    assert batch.verdict is not None
    assert batch.verdict.caution == "low"
    stored = db.params_for("INSERT INTO wnba.research_verdicts")
    assert len(stored) == 1
    assert stored[0][2] == "low"


def test_a_contested_file_is_recorded_as_contested(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = ScriptedProvider()
    db = database()
    # First pass: learn which evidence id the analysts actually cited, then contradict it.
    run(db, provider, monkeypatch)
    cited = sorted(provider.seen[0][1], key=str)[0]

    disputing = ScriptedProvider(skeptic_contradicts=[cited])
    fresh = database()
    batch = run(fresh, disputing, monkeypatch)
    assert batch.verdict is not None
    assert batch.verdict.contested_claims == len(ANALYST_QUESTIONS)
    assert batch.verdict.caution == "high"


def test_token_spend_is_recorded_against_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    db = database()
    run(db, ScriptedProvider(), monkeypatch)
    completion = db.params_for("SET status='complete'")[0]
    agents = len(ANALYST_QUESTIONS) + 1
    assert completion[1] == agents  # agent_calls
    assert completion[2] == agents  # provider_calls, one attempt each
    assert completion[3] == 100 * agents  # prompt_tokens
    assert completion[4] == 20 * agents  # completion_tokens


def test_unknown_token_counts_are_stored_as_null_rather_than_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Silent(ScriptedProvider):
        def analyze(self, **kwargs: Any) -> AgentResponse:
            response = super().analyze(**kwargs)
            role = kwargs["role"]
            return response if role != SKEPTIC_ROLE else _without_usage(response)

    db = database()
    run(db, Silent(), monkeypatch)
    completion = db.params_for("SET status='complete'")[0]
    assert completion[3] is None
    assert completion[4] is None


def _without_usage(response: AgentResponse) -> AgentResponse:
    return AgentResponse(
        analysis=response.analysis,
        prompt_sha256=response.prompt_sha256,
        response_sha256=response.response_sha256,
        usage=None,
        attempts=response.attempts,
    )


def test_claims_expire_when_the_market_locks(monkeypatch: pytest.MonkeyPatch) -> None:
    db = database()
    run(db, ScriptedProvider(), monkeypatch)
    for params in db.params_for("INSERT INTO wnba.research_claims"):
        assert params[9] == projection_row()["locks_at"]


def test_a_completed_run_is_returned_rather_than_repeated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = uuid4()
    db = database()
    db.when("AND status='complete'", [{"research_run_id": existing}])
    db.when("count(*) AS analyses", [{"analyses": 5, "claims": 9}])
    db.when(
        "FROM wnba.research_verdicts",
        [
            {
                "caution": "elevated",
                "agreement": 0.75,
                "total_claims": 8,
                "contested_claims": 2,
                "contested_predicates": ["matchup:pace"],
                "risk_flags": ["thin sample"],
                "skeptic_confidence": 0.5,
                "rationale": "Two of eight cited claims were contested.",
            }
        ],
    )
    provider = ScriptedProvider()
    batch = run(db, provider, monkeypatch)
    assert provider.seen == []
    assert batch.research_run_id == existing
    assert batch.verdict is not None
    assert batch.verdict.caution == "elevated"
    assert batch.verdict.contested_predicates == ("matchup:pace",)


def test_the_evidence_document_is_a_stable_hash_of_its_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two runs over an unchanged snapshot must reuse the same document, not fork the archive."""
    first, second = database(), database()
    run(first, ScriptedProvider(), monkeypatch)
    run(second, ScriptedProvider(), monkeypatch)
    forecast_documents = {
        db.params_for("INSERT INTO wnba.source_documents")[0][2] for db in (first, second)
    }
    assert len(forecast_documents) == 1
    excerpt = first.params_for("INSERT INTO wnba.source_documents")[0][3]
    assert json.loads(excerpt)["market"]["line"] == 15.5
