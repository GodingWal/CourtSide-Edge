"""Analyst console API.

The console serves the live market archive and shadow baseline forecasts. Its advanced pricing
lab runs the same correlation and payout engines against hand-entered probabilities, clearly
separated from model output. Shadow forecasts remain paper-only until validation gates pass.

The engines behind it are real: the same copula simulator and payout math the batch pipeline
will use, with no separate "web" implementation to drift out of sync.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from wnba_domain.enums import EntryType
from wnba_domain.market import PayoutRule, PayoutTable
from wnba_marketmath import (
    PAYOUT_TABLES_ARE_UNVERIFIED,
    RiskPolicy,
    breakeven_uniform_leg_probability,
    entry_expected_value,
    implied_break_even_american,
    independent_correct_count_pmf,
    prizepicks_payout_table,
    staked_fraction,
    underdog_payout_table,
)
from wnba_services.market_engine.correlation import EntryCorrelation
from wnba_sim import correct_count_pmf, effective_leg_correlation

from wnba_apps.api.auth import configured_owner, decode_basic_authorization, verify_password

STATIC_DIR = Path(__file__).parent / "static"

# Tables are unverified defaults; this is when they were last bundled, not when they were
# confirmed against the live product. The console surfaces that distinction prominently.
BUNDLED_AT = datetime(2026, 8, 3, tzinfo=UTC)

app = FastAPI(
    title="WNBA Prop Intelligence -- Analyst Console",
    description="Analysis only. This system never places a wager or moves money.",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def private_owner_auth(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Protect the console when owner credentials are configured on the VPS."""
    owner = configured_owner()
    if owner is None or request.url.path in {"/api/health", "/api/auth/status"}:
        return await call_next(request)
    supplied = decode_basic_authorization(request.headers.get("authorization"))
    authenticated = (
        supplied is not None
        and hmac.compare_digest(supplied[0], owner[0])
        and verify_password(supplied[1], owner[1])
    )
    if not authenticated:
        return JSONResponse(
            status_code=401,
            content={"detail": "Owner authentication required"},
            headers={"WWW-Authenticate": 'Basic realm="CourtSide Edge", charset="UTF-8"'},
        )
    return await call_next(request)


@app.get("/api/auth/status")
def auth_status() -> dict[str, object]:
    return {"private": configured_owner() is not None, "mode": "single_owner"}


def _table_for(source: str) -> PayoutTable:
    if source == "prizepicks":
        return prizepicks_payout_table(BUNDLED_AT)
    if source == "underdog":
        return underdog_payout_table(BUNDLED_AT)
    raise HTTPException(status_code=404, detail=f"unknown source {source!r}")


# --------------------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------------------
class RuleView(BaseModel):
    entry_type: str
    leg_count: int
    payouts_by_correct: dict[int, float]
    breakeven_leg_probability: float
    breakeven_american: int


class PayoutTableView(BaseModel):
    source: str
    bundled_at: datetime
    verified: bool
    warning: str
    rules: list[RuleView]


class LegDescriptor(BaseModel):
    """What a leg is, as far as correlation is concerned."""

    prop_type: Annotated[str, Field(min_length=1, max_length=80)]
    side: Literal["over", "under"]
    player_id: UUID | None = None
    team: Annotated[str, Field(max_length=10)] | None = None
    game_id: UUID | None = None


class PriceRequest(BaseModel):
    source: Literal["prizepicks", "underdog"] = "prizepicks"
    entry_type: Literal["power", "flex"] = "power"
    leg_probabilities: Annotated[list[float], Field(min_length=2, max_length=8)]
    correlation: Annotated[float, Field(gt=-1.0, lt=1.0)] | None = None
    """Left unset, correlation is estimated from ``legs`` and the fitted table rather than
    assumed to be zero. Zero is the one value that is definitely wrong for a same-player pair,
    and it was the old default."""

    legs: Annotated[list[LegDescriptor], Field(max_length=8)] = Field(default_factory=list)
    simulations: Annotated[int, Field(ge=1_000, le=500_000)] = 100_000
    seed: Annotated[int, Field(ge=0)] = 0
    already_staked_this_game: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    already_staked_today: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0


class EntrySuggestionRequest(BaseModel):
    source: Literal["prizepicks", "underdog"] = "prizepicks"
    max_legs: Annotated[int, Field(ge=2, le=6)] = 5
    max_per_game: Annotated[int, Field(ge=1, le=6)] = 2
    limit: Annotated[int, Field(ge=1, le=10)] = 5
    pool: Annotated[int, Field(ge=2, le=20)] = 12
    seed: Annotated[int, Field(ge=0)] = 0


class PriceResponse(BaseModel):
    rule: RuleView
    correlated_pmf: list[float]
    independent_pmf: list[float]
    expected_value: float
    independent_expected_value: float
    correlation_swing: float
    realised_outcome_correlation: float
    stake_fraction: float
    gates: list[str]
    verdict: Literal["recommend", "decline"]
    seed: int
    simulations: int
    correlation_used: float
    correlation_source: Literal["supplied", "fitted", "prior", "assumed_zero"]
    correlation_low: float
    correlation_high: float
    expected_value_low: float
    expected_value_high: float
    worst_case_expected_value: float
    correlation_pairs: list[dict[str, object]]


class FeedbackRequest(BaseModel):
    feedback_type: Literal[
        "accepted",
        "rejected_bad_data",
        "rejected_minutes",
        "rejected_matchup",
        "rejected_price",
        "rejected_uncertainty",
        "missing_evidence",
        "explanation_unclear",
    ]
    projection_useful: Annotated[float, Field(ge=0, le=1)]
    evidence_relevant: Annotated[float, Field(ge=0, le=1)]
    confidence_appropriate: Annotated[float, Field(ge=0, le=1)]
    weakest_assumption: Annotated[str, Field(max_length=500)] | None = None
    missing_context: Annotated[str, Field(max_length=1000)] | None = None
    would_repeat: bool
    evidence_ids_useful: list[UUID] = Field(default_factory=list, max_length=50)
    evidence_ids_misleading: list[UUID] = Field(default_factory=list, max_length=50)


class PickLegDraft(BaseModel):
    player_name: Annotated[str, Field(min_length=1, max_length=120)]
    prop_type: Annotated[str, Field(min_length=1, max_length=80)]
    side: Literal["over", "under"]
    line: Annotated[float, Field(ge=0, le=200)]
    offered_odds: Annotated[int, Field(ge=-5000, le=5000)] | None = None
    projection_id: UUID | None = None
    model_probability: Annotated[float, Field(ge=0, le=1)] | None = None
    extraction_confidence: Annotated[float, Field(ge=0, le=1)] | None = None


class PickSlipDraft(BaseModel):
    title: Annotated[str, Field(max_length=200)] = ""
    source: Literal["manual", "board", "ai_text", "screenshot"] = "manual"
    entry_type: Literal["power", "flex", "sportsbook"] = "power"
    platform: Annotated[str, Field(max_length=100)] = ""
    stake: Annotated[float, Field(ge=0, le=1_000_000)] | None = None
    potential_payout: Annotated[float, Field(ge=0, le=10_000_000)] | None = None
    notes: Annotated[str, Field(max_length=2000)] = ""
    legs: Annotated[list[PickLegDraft], Field(min_length=1, max_length=12)]


class PickTextRequest(BaseModel):
    text: Annotated[str, Field(min_length=3, max_length=5000)]


class PickScreenshotRequest(BaseModel):
    filename: Annotated[str, Field(min_length=1, max_length=200)]
    content_type: Literal["image/png", "image/jpeg", "image/webp"]
    data_base64: Annotated[str, Field(min_length=20, max_length=14_000_000)]


def _parse_pick_text(text: str, source: str) -> PickSlipDraft:
    """Use DeepSeek only to structure user-supplied text; the owner must confirm the draft."""
    import httpx

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=503, detail="DeepSeek is not configured")
    shape = {
        "title": "short string",
        "source": source,
        "entry_type": "power|flex|sportsbook",
        "platform": "string",
        "stake": None,
        "potential_payout": None,
        "notes": "string",
        "legs": [
            {
                "player_name": "string",
                "prop_type": "points",
                "side": "over|under",
                "line": 0.0,
                "offered_odds": None,
                "extraction_confidence": 0.0,
            }
        ],
    }
    payload = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "Extract a sports pick slip from supplied text. Return JSON only. Never "
                    "invent a missing player, line, side, platform, odds, stake, or payout. "
                    "Use confidence below 0.7 for ambiguous OCR. This creates an unconfirmed "
                    "paper draft, not a wager."
                ),
            },
            {"role": "user", "content": json.dumps({"text": text, "json_shape": shape})},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2500,
        "stream": False,
    }
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    with httpx.Client(timeout=90, headers={"Authorization": f"Bearer {api_key}"}) as client:
        response = client.post(f"{base_url}/chat/completions", json=payload)
        response.raise_for_status()
    envelope = response.json()
    content = envelope["choices"][0]["message"]["content"]
    draft = PickSlipDraft.model_validate_json(content)
    return draft.model_copy(update={"source": source})


def _edge_over_breakeven(row: dict[str, object]) -> float | None:
    """Shrunk probability less the break-even the payout table demanded, or ``None``.

    The board used to rank on ``predicted_probability``, which is the number before shrinkage
    and before any payout rule is consulted. Both corrections already exist and were already
    stored per episode; this is the arithmetic that puts them on screen.
    """
    shrunk, breakeven = row.get("shrunk_probability"), row.get("breakeven_probability")
    if shrunk is None or breakeven is None:
        return None
    return float(str(shrunk)) - float(str(breakeven))


def _entry_correlation_for(request: PriceRequest) -> EntryCorrelation:
    """Estimate the entry's correlation from its leg descriptors and the fitted table."""
    from wnba_services.market_engine.correlation import LegKey, entry_correlation, load_correlations
    from wnba_store.db import connect

    if not request.legs:
        return EntryCorrelation(0.0, 0.0, 0.0, (), 0)
    try:
        with connect() as conn, conn.cursor() as cur:
            fitted = load_correlations(cur)
    except Exception:
        # An unreachable table means the priors apply, and the response says so through
        # `correlation_source`. It does not mean the entry is uncorrelated.
        fitted = {}
    keys = [
        LegKey(
            prop_type=leg.prop_type,
            side=leg.side,
            player_id=None if leg.player_id is None else str(leg.player_id),
            team=leg.team,
            game_id=None if leg.game_id is None else str(leg.game_id),
        )
        for leg in request.legs
    ]
    return entry_correlation(keys, fitted)


# --------------------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def console() -> HTMLResponse:
    index = STATIC_DIR / "index.html"
    if not index.exists():  # pragma: no cover - only if the package is mis-installed
        raise HTTPException(status_code=500, detail="console assets missing")
    return HTMLResponse(index.read_text(encoding="utf-8"))


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "status": "ok",
        "phase": "3 -- private shadow analysis",
        "analysis_only": True,
        "archiving_market_data": True,
        "has_forecasting_models": True,
        "checked_at": datetime.now(UTC),
    }


def _rule_view(rule: PayoutRule) -> RuleView:
    return RuleView(
        entry_type=rule.entry_type.value,
        leg_count=rule.leg_count,
        payouts_by_correct={k: float(v) for k, v in sorted(rule.payouts_by_correct.items())},
        breakeven_leg_probability=breakeven_uniform_leg_probability(rule),
        breakeven_american=implied_break_even_american(rule),
    )


@app.get("/api/payouts/{source}", response_model=PayoutTableView)
def payouts(source: str) -> PayoutTableView:
    table = _table_for(source)
    return PayoutTableView(
        source=table.source.value,
        bundled_at=table.effective_from,
        verified=False,
        warning=PAYOUT_TABLES_ARE_UNVERIFIED,
        rules=[_rule_view(r) for r in table.rules],
    )


@app.post("/api/price", response_model=PriceResponse)
def price(request: PriceRequest) -> PriceResponse:
    """Price one entry, showing the correlated and independent answers side by side.

    Both are returned deliberately. The gap between them is the single most informative number
    on the page: an entry whose sign flips across plausible correlation is not an opportunity,
    it is an unpriced position.
    """
    table = _table_for(request.source)
    entry_type = EntryType(request.entry_type)
    leg_count = len(request.leg_probabilities)
    try:
        rule = table.rule_for(entry_type, leg_count)
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if request.legs and len(request.legs) != leg_count:
        raise HTTPException(
            status_code=422,
            detail="legs must describe exactly one entry per supplied probability",
        )

    estimate = _entry_correlation_for(request)
    correlation = request.correlation if request.correlation is not None else estimate.mean
    source: Literal["supplied", "fitted", "prior", "assumed_zero"]
    if request.correlation is not None:
        source = "supplied"
    elif not request.legs:
        source = "assumed_zero"
    else:
        source = "fitted" if estimate.is_fitted else "prior"

    if request.correlation is not None and request.correlation < 0.0 and leg_count > 2:
        raise HTTPException(
            status_code=422,
            detail=(
                "a one-factor model cannot represent negative correlation across more than "
                "two legs -- the implied joint distribution does not exist"
            ),
        )
    # An estimated band may reach below zero where a supplied value may not. The estimate is not
    # a user error to reject, so the unrepresentable part of the band is clipped and the entry is
    # priced across what the one-factor model can actually express.
    floor = 0.0 if leg_count > 2 else -0.99
    correlation = max(floor, correlation)
    band = sorted(
        {max(floor, value) for value in (correlation, estimate.low, estimate.high)}
        if request.legs
        else {correlation}
    )

    correlated = correct_count_pmf(
        request.leg_probabilities,
        correlation=correlation,
        simulations=request.simulations,
        seed=request.seed,
    )
    independent = independent_correct_count_pmf(request.leg_probabilities)

    ev = entry_expected_value(correlated, rule)
    ev_independent = entry_expected_value(independent, rule)
    band_values = [
        entry_expected_value(
            correct_count_pmf(
                request.leg_probabilities,
                correlation=value,
                simulations=request.simulations,
                seed=request.seed,
            ),
            rule,
        )
        for value in band
    ]
    ev_low, ev_high = min(band_values), max(band_values)
    # Sizing and gating both take the least favourable end of the band. Being wrong about
    # correlation is the expected case here, not the tail: most pairs still price off a prior.
    worst = min(ev, ev_low)

    policy = RiskPolicy()
    stake = staked_fraction(
        correlated,
        rule,
        policy,
        expected_value=worst,
        already_staked_this_game=request.already_staked_this_game,
        already_staked_today=request.already_staked_today,
    )

    breakeven = breakeven_uniform_leg_probability(rule)
    gates: list[str] = []
    if source == "assumed_zero" and leg_count > 1:
        gates.append(
            "no leg descriptors supplied, so correlation is assumed zero -- describe the legs "
            "to price the structure instead"
        )
    if source == "prior":
        gates.append(
            f"{len(estimate.pairs) - estimate.fitted_pairs} of {len(estimate.pairs)} leg pairs "
            "use a stated prior rather than a measured correlation"
        )
    if ev_low < 0 <= ev_high:
        gates.append(
            "expected value changes sign inside the correlation band -- unpriced, not an "
            "opportunity"
        )
    if worst < policy.min_expected_value:
        gates.append(
            f"worst-case edge {worst:.1%} is below the {policy.min_expected_value:.0%} minimum "
            "-- thin edges on unvalidated probabilities are noise wearing a decimal point"
        )
    if min(request.leg_probabilities) < breakeven:
        gates.append(
            f"at least one leg is below the {breakeven:.1%} per-leg breakeven for this rule"
        )
    if (ev > 0) != (ev_independent > 0):
        gates.append(
            "the sign of this edge depends on the correlation assumption -- unpriced, not an "
            "opportunity"
        )

    return PriceResponse(
        rule=_rule_view(rule),
        correlated_pmf=list(correlated),
        independent_pmf=list(independent),
        expected_value=ev,
        independent_expected_value=ev_independent,
        correlation_swing=ev - ev_independent,
        realised_outcome_correlation=effective_leg_correlation(correlated),
        stake_fraction=stake,
        gates=gates,
        verdict="recommend" if stake > 0 else "decline",
        seed=request.seed,
        simulations=request.simulations,
        correlation_used=correlation,
        correlation_source=source,
        correlation_low=min(band),
        correlation_high=max(band),
        expected_value_low=ev_low,
        expected_value_high=ev_high,
        worst_case_expected_value=worst,
        correlation_pairs=[pair.to_payload() for pair in estimate.pairs],
    )


@app.post("/api/entries/suggest")
def suggest_entries(request: EntrySuggestionRequest) -> dict[str, object]:
    """Search the live candidate board for entries worth a human's attention.

    This is the question the console never answered. Every screen up to now ranked legs; the
    decision is which legs go on one ticket, and that depends on the payout table's leg-count
    curve and on how correlated the legs are -- neither of which a per-leg ranking can express.

    Suggestions inherit every upstream restriction: only forecasts the gate already called
    candidates are eligible, probabilities are the shrunk ones, and an entry with any open gate
    is returned as a decline with its reasons rather than withheld or promoted.
    """
    from wnba_services.market_engine.correlation import load_correlations
    from wnba_services.market_engine.entries import CandidateLeg, construct_entries
    from wnba_store.db import connect

    board = forecasts()
    if not board.get("available"):
        return {
            "available": False,
            "reason": board.get("reason", "board unavailable"),
            "entries": [],
        }
    board_rows = board.get("forecasts", [])
    rows: list[dict[str, object]] = [
        row
        for row in (board_rows if isinstance(board_rows, list) else [])
        if isinstance(row, dict) and str(row.get("system_recommendation")) == "candidate"
    ]
    candidates = [
        CandidateLeg(
            projection_id=str(row["projection_id"]),
            player_name=str(row["full_name"]),
            prop_type=str(row["prop_type"]),
            side=str(row["side"]),
            line=float(str(row["line"])),
            # The shrunk probability where the gate recorded one. Falling back to the raw
            # probability would compound selection bias once per leg, so a row without a shrunk
            # value is skipped instead.
            probability=float(str(row["shrunk_probability"])),
            player_id=None if row.get("player_id") is None else str(row["player_id"]),
            team=None if row.get("team") is None else str(row["team"]),
            game_id=None if row.get("game_id") is None else str(row["game_id"]),
            breakeven=(
                None
                if row.get("breakeven_probability") is None
                else float(str(row["breakeven_probability"]))
            ),
        )
        for row in rows
        if row.get("shrunk_probability") is not None
    ]
    try:
        with connect() as conn, conn.cursor() as cur:
            fitted = load_correlations(cur)
    except Exception:  # pragma: no cover - a missing table must not hide the suggestions
        fitted = {}
    entries = construct_entries(
        candidates,
        _table_for(request.source),
        fitted=fitted,
        max_legs=request.max_legs,
        max_per_game=request.max_per_game,
        limit=request.limit,
        pool=request.pool,
        seed=request.seed,
    )
    return {
        "available": True,
        "analysis_only": True,
        "source": request.source,
        "candidates_considered": len(candidates),
        "entries": [entry.to_payload() for entry in entries],
    }


@app.get("/api/archive")
def archive() -> dict[str, object]:
    """Live state of the market archive.

    The most honest number on the site. Historical prop odds cannot be bought retroactively,
    so this is the only measure of how close we are to being able to evaluate anything -- and
    it will read "thin" for months, which is the truth rather than a defect to be styled away.
    """
    try:
        from wnba_store.db import connect
    except ImportError:  # pragma: no cover - store extras absent
        return {"available": False, "reason": "store package unavailable"}

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT source,
                       count(*)                                        AS snapshots,
                       count(DISTINCT player_id)                       AS players,
                       count(DISTINCT prop_type)                       AS markets,
                       count(*) FILTER (WHERE over_american_odds IS NOT NULL
                                          AND under_american_odds IS NOT NULL) AS devigable,
                       min(system_from)                                AS first_seen,
                       max(system_from)                                AS last_seen
                FROM wnba.prop_quotes GROUP BY source ORDER BY snapshots DESC
            """)
            by_source = [dict(r) for r in cur.fetchall()]
            cur.execute("""
                SELECT prop_type, count(*) AS snapshots, count(DISTINCT player_id) AS players
                FROM wnba.prop_quotes GROUP BY prop_type ORDER BY snapshots DESC LIMIT 20
            """)
            by_market = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT count(*) AS n FROM wnba.quarantine")
            row = cur.fetchone()
            quarantined = int(str(row["n"])) if row else 0
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200]}

    total = sum(int(str(s["snapshots"])) for s in by_source)
    return {
        "available": True,
        "total_snapshots": total,
        "quarantined_payloads": quarantined,
        "by_source": by_source,
        "by_market": by_market,
        "note": (
            "Record-only. These are archived market observations, not forecasts. "
            "Evaluation needs roughly a season of this before any claim can be tested."
        ),
    }


@app.get("/api/status")
def status() -> dict[str, object]:
    """Honest build status. Every claim here is backed by a test that fails the build."""
    return {
        "analysis_only": True,
        "phase": "3 -- private shadow analysis",
        "invariants": [
            {"name": "Bitemporality on every stored fact", "enforced": True},
            {"name": "No look-ahead (point-in-time reads)", "enforced": True},
            {"name": "Starter status is a probability, never a boolean", "enforced": True},
            {"name": "Strict validation at every boundary", "enforced": True},
            {"name": "Probability first, profit second", "enforced": True},
            {"name": "Automation may restrict, never expand exposure", "enforced": True},
            {"name": "No wager placement object exists in the domain", "enforced": True},
        ],
        "components": [
            {"name": "wnba_domain (ontology)", "state": "built"},
            {"name": "wnba_store.temporal (as_of reads)", "state": "built"},
            {"name": "wnba_marketmath (pricing, Kelly)", "state": "built"},
            {"name": "wnba_sim (correlated simulator)", "state": "built"},
            {"name": "ontology YAML + drift test", "state": "built"},
            {"name": "Postgres bitemporal schema (VPS)", "state": "built"},
            {"name": "line archiver (record-only)", "state": "built -- running every 15min"},
            {"name": "five-component forecasting", "state": "built -- challenger/shadow"},
            {"name": "point-in-time backtesting", "state": "built -- 5 replay snapshots"},
            {"name": "research agents", "state": "built -- DeepSeek key required"},
            {"name": "learning loop", "state": "built -- collecting outcomes"},
            {"name": "video intelligence", "state": "not started"},
        ],
        "blocking_real_money": [
            "500+ out-of-sample recommendations",
            "positive line value",
            "calibration within tolerance",
            "no single player or market dominating profit",
            "stability across rolling windows",
            "verified payout tables",
        ],
    }


@app.get("/api/forecasts")
def forecasts() -> dict[str, object]:
    """Latest shadow forecasts. They are paper decisions, never wagering instructions."""
    try:
        from wnba_store.db import connect

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                # One row per player/game/market/line. prop_quotes snapshot on every poll,
                # so deduping on quote_id repeated the same pick once per afternoon snapshot;
                # the freshest generated forecast stands for the market.
                """SELECT DISTINCT ON (f.player_id,f.game_id,f.prop_type,f.line)
                          f.projection_id,p.full_name,f.prop_type,f.player_id,f.game_id,
                          f.line,f.mean,f.median,f.stddev,f.probability_over,
                          f.probability_under,f.projected_minutes,f.sample_size,
                          f.data_quality_score,f.confidence,f.generated_at,f.expires_at,
                          d.episode_id,d.side,d.predicted_probability,d.model_disagreement,
                          d.system_recommendation,d.shrunk_probability,d.breakeven_probability,
                          d.decision_reason,q.source,
                          g.scheduled_tipoff,i.designation AS injury_designation,
                          i.detail AS injury_detail,r.availability_probability,
                          r.start_probability,r.closing_lineup_probability,
                          r.minutes_std AS projected_minutes_std,r.model_version AS role_model,
                          e.teammate_effect_count,e.rate_multiplier AS teammate_rate_multiplier,
                          e.minutes_delta AS teammate_minutes_delta,e.effect_confidence,
                          c.expected_possessions,c.pace_multiplier,c.defense_multiplier,
                          c.expected_margin,c.blowout_probability,c.team_rest_days,
                          c.opponent_rest_days,c.confidence AS matchup_confidence,
                          c.method_version AS matchup_model,fc.components,
                          ht.abbreviation AS home_team,at.abbreviation AS away_team,
                          lt.abbreviation AS team,
                          CASE
                            WHEN lt.abbreviation = ht.abbreviation THEN at.abbreviation
                            WHEN lt.abbreviation = at.abbreviation THEN ht.abbreviation
                          END AS opponent
                   FROM wnba.stat_forecasts f
                   JOIN wnba.players p ON p.player_id=f.player_id
                   JOIN wnba.decision_episodes d ON d.model_run_id=f.model_run_id
                     AND d.quote_id=f.quote_id
                   JOIN wnba.prop_quotes q ON q.quote_id=f.quote_id
                   JOIN wnba.games g ON g.game_id=f.game_id
                   JOIN wnba.teams ht ON ht.team_id=g.home_team_id
                   JOIN wnba.teams at ON at.team_id=g.away_team_id
                   -- Player's team, taken from their most recent completed box score. There
                   -- is no pre-tip roster feed, so this is the best available mapping; it
                   -- reads only settled history, so it introduces no look-ahead. A player
                   -- traded since their last appearance will show the old team until they
                   -- next play, which is why `opponent` is NULL rather than guessed when the
                   -- team does not match either side of this game.
                   LEFT JOIN LATERAL (
                     SELECT t.abbreviation
                     FROM wnba.player_game_lines l
                     JOIN wnba.games pg ON pg.game_id=l.game_id
                     JOIN wnba.teams t ON t.team_id=l.team_id
                     WHERE l.player_id=f.player_id
                       AND pg.scheduled_tipoff < g.scheduled_tipoff
                     ORDER BY pg.scheduled_tipoff DESC LIMIT 1
                   ) lt ON true
                   LEFT JOIN LATERAL (
                     SELECT designation,detail FROM wnba.injury_status
                     WHERE player_id=f.player_id AND game_id=f.game_id AND system_to IS NULL
                     ORDER BY system_from DESC LIMIT 1
                   ) i ON true
                   LEFT JOIN LATERAL (
                     SELECT availability_probability,start_probability,
                            closing_lineup_probability,minutes_std,model_version
                     FROM wnba.projected_roles
                     WHERE player_id=f.player_id AND game_id=f.game_id AND system_to IS NULL
                     ORDER BY system_from DESC LIMIT 1
                   ) r ON true
                   LEFT JOIN LATERAL (
                     SELECT count(*) AS teammate_effect_count,
                            coalesce(exp(sum(ln(rate_multiplier))),1.0) AS rate_multiplier,
                            coalesce(sum(minutes_delta),0.0) AS minutes_delta,
                            min(confidence) AS effect_confidence
                     FROM wnba.teammate_role_effects
                     WHERE player_id=f.player_id AND game_id=f.game_id
                       AND prop_type=f.prop_type AND system_to IS NULL
                   ) e ON true
                   LEFT JOIN LATERAL (
                     SELECT * FROM wnba.matchup_contexts
                     WHERE game_id=f.game_id AND prop_type=f.prop_type AND system_to IS NULL
                       AND team_id=(SELECT team_id FROM wnba.player_game_lines
                         WHERE player_id=f.player_id AND system_to IS NULL
                         ORDER BY ingested_at DESC LIMIT 1)
                     ORDER BY system_from DESC LIMIT 1
                   ) c ON true
                   LEFT JOIN LATERAL (
                     SELECT jsonb_agg(jsonb_build_object(
                       'name',component_name,'version',component_version,'weight',weight,
                       'mean',mean,'probability_over',probability_over,
                       'probability_push',probability_push,'probability_under',probability_under
                     ) ORDER BY weight DESC) AS components
                     FROM wnba.forecast_components
                     WHERE projection_id=f.projection_id
                   ) fc ON true
                   WHERE f.expires_at>now() AND coalesce(i.designation,'available')<>'out'
                   ORDER BY f.player_id,f.game_id,f.prop_type,f.line,f.generated_at DESC"""
            )
            rows = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200], "forecasts": []}
    for row in rows:
        row["edge"] = _edge_over_breakeven(row)
    # Ranked by edge over the payout table's break-even, not by raw probability. The two orders
    # disagree constantly and only one of them is a ranking by value: a 0.72 on a product that
    # needs three legs correct and a 0.61 on one that needs two are not comparable quantities.
    # Rows the gate never priced (no shrunk probability yet) sort last rather than first.
    rows.sort(
        key=lambda row: (
            row["edge"] is not None,
            row["edge"] if row["edge"] is not None else 0.0,
            float(str(row["predicted_probability"])),
        ),
        reverse=True,
    )
    return {
        "available": True,
        "analysis_only": True,
        "model_stage": "challenger",
        "validated_for_real_money": False,
        "forecasts": rows,
    }


@app.get("/api/injuries")
def injuries() -> dict[str, object]:
    """Current official availability designations for games on the market board."""
    try:
        from wnba_store.db import connect

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT p.full_name,i.designation,i.detail,i.system_from,i.source_ref,
                          g.scheduled_tipoff,a.abbreviation AS away,h.abbreviation AS home
                   FROM wnba.injury_status i
                   JOIN wnba.players p ON p.player_id=i.player_id
                   JOIN wnba.games g ON g.game_id=i.game_id
                   JOIN wnba.teams a ON a.team_id=g.away_team_id
                   JOIN wnba.teams h ON h.team_id=g.home_team_id
                   WHERE i.system_to IS NULL AND g.scheduled_tipoff>now()-interval '6 hours'
                   ORDER BY g.scheduled_tipoff,p.full_name"""
            )
            rows = [dict(row) for row in cur.fetchall()]
            cur.execute("SELECT max(retrieved_at) AS latest FROM wnba.official_injury_reports")
            latest = cur.fetchone()
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200], "injuries": []}
    return {
        "available": True,
        "source": "WNBA official injury report",
        "latest_report": None if latest is None else latest["latest"],
        "injuries": rows,
    }


@app.get("/api/operations")
def operations() -> dict[str, object]:
    """Freshness/readiness checks used by operators and external uptime monitoring."""
    try:
        from wnba_store.db import connect

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT
                     (SELECT max(system_from) FROM wnba.prop_quotes) AS latest_quote,
                     (SELECT max(completed_at) FROM wnba.ingested_dates) AS latest_stats_ingest,
                     (SELECT max(completed_at) FROM wnba.model_runs
                       WHERE status='complete') AS latest_model_run,
                     (SELECT count(*) FROM wnba.dq_incidents
                       WHERE blocks_recommendations AND resolved_at IS NULL) AS blockers"""
            )
            row = dict(cur.fetchone() or {})
    except Exception as exc:
        return {"status": "unhealthy", "database": False, "reason": str(exc)[:200]}
    now = datetime.now(UTC)
    quote = row.get("latest_quote")
    quote_stale = not isinstance(quote, datetime) or (now - quote).total_seconds() > 3600
    blockers = int(str(row.get("blockers", 0)))
    return {
        "status": "degraded" if quote_stale or blockers else "ok",
        "database": True,
        "market_archive_stale": quote_stale,
        "blocking_data_quality_incidents": blockers,
        "latest_quote": quote,
        "latest_stats_ingest": row.get("latest_stats_ingest"),
        "latest_model_run": row.get("latest_model_run"),
        "analysis_only": True,
    }


@app.get("/api/performance")
def performance() -> dict[str, object]:
    """Proper scores and calibration for settled paper episodes."""
    try:
        from wnba_store.db import connect

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT count(*) AS settled,
                          count(*) FILTER (WHERE o.was_voided) AS voided,
                          count(*) FILTER (WHERE o.was_push) AS pushed,
                          count(*) FILTER (WHERE o.hit AND NOT o.was_voided
                                            AND NOT o.was_push) AS hits,
                          count(*) FILTER (WHERE NOT o.was_voided
                                            AND NOT o.was_push) AS scored,
                          avg(o.brier) AS mean_brier,avg(o.log_loss) AS mean_log_loss,
                          avg(abs(o.actual_stat-d.projected_mean)) AS mean_absolute_error,
                          avg(CASE WHEN o.closing_line IS NULL THEN NULL
                               WHEN d.side='over' THEN o.closing_line-d.line
                               ELSE d.line-o.closing_line END) AS mean_line_value
                   FROM wnba.episode_outcomes o
                   JOIN wnba.decision_episodes d ON d.episode_id=o.episode_id"""
            )
            summary = dict(cur.fetchone() or {})
            cur.execute(
                """SELECT floor(d.predicted_probability*10)/10 AS bucket,
                          count(*) AS forecasts,avg(d.predicted_probability) AS predicted,
                          avg(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END) AS observed
                   FROM wnba.episode_outcomes o
                   JOIN wnba.decision_episodes d ON d.episode_id=o.episode_id
                   WHERE NOT o.was_voided AND NOT o.was_push
                   GROUP BY 1 ORDER BY 1"""
            )
            calibration = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT d.prop_type,count(*) AS forecasts,
                          avg(CASE WHEN o.hit THEN 1.0 ELSE 0.0 END) AS hit_rate,
                          avg(o.brier) AS brier,
                          avg(abs(o.actual_stat-d.projected_mean)) AS mae
                   FROM wnba.episode_outcomes o
                   JOIN wnba.decision_episodes d ON d.episode_id=o.episode_id
                   WHERE NOT o.was_voided AND NOT o.was_push
                   GROUP BY d.prop_type ORDER BY forecasts DESC"""
            )
            by_market = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT DISTINCT ON (component_name) component_name,evaluated_at,sample_size,
                          brier,log_loss,calibration_error,status
                   FROM wnba.model_evaluations
                   ORDER BY component_name,evaluated_at DESC"""
            )
            model_evaluations = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT component_name,metric,observed_value,threshold,severity,
                          automatic_response,opened_at
                   FROM wnba.drift_incidents WHERE resolved_at IS NULL
                   ORDER BY opened_at DESC LIMIT 25"""
            )
            drift_incidents = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT primary_error,count(*) AS episodes,
                          avg(CASE WHEN avoidable THEN 1.0 ELSE 0.0 END) AS avoidable_rate
                   FROM wnba.error_attributions GROUP BY primary_error ORDER BY episodes DESC"""
            )
            error_attributions = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200]}
    return {
        "available": True,
        "analysis_only": True,
        "minimum_validation_sample": 500,
        "summary": summary,
        "calibration": calibration,
        "by_market": by_market,
        "model_evaluations": model_evaluations,
        "drift_incidents": drift_incidents,
        "error_attributions": error_attributions,
    }


@app.get("/api/backtests/latest")
def latest_backtest() -> dict[str, object]:
    """Latest completed point-in-time benchmark comparison."""
    try:
        from wnba_store.db import connect

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM wnba.backtest_runs WHERE status='complete'
                   ORDER BY completed_at DESC LIMIT 1"""
            )
            run = cur.fetchone()
            if run is None:
                return {"available": True, "run": None, "models": [], "by_snapshot": []}
            cur.execute(
                """SELECT model_name,count(*) AS forecasts,avg(brier) AS brier,
                          avg(log_loss) AS log_loss,
                          avg(abs(actual_stat-projected_mean)) AS mean_absolute_error
                   FROM wnba.backtest_results WHERE backtest_run_id=%s
                   GROUP BY model_name ORDER BY brier""",
                (run["backtest_run_id"],),
            )
            models = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT snapshot_label,model_name,count(*) AS forecasts,avg(brier) AS brier,
                          avg(log_loss) AS log_loss
                   FROM wnba.backtest_results WHERE backtest_run_id=%s
                   GROUP BY snapshot_label,model_name ORDER BY snapshot_label,brier""",
                (run["backtest_run_id"],),
            )
            by_snapshot = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200]}
    return {
        "available": True,
        "point_in_time": True,
        "run": dict(run),
        "models": models,
        "by_snapshot": by_snapshot,
    }


@app.get("/api/research/{projection_id}")
def projection_research(projection_id: UUID) -> dict[str, object]:
    """Cited research state for one immutable projection."""
    try:
        from wnba_store.db import connect

        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM wnba.research_runs WHERE projection_id=%s
                   ORDER BY started_at DESC LIMIT 1""",
                (projection_id,),
            )
            run = cur.fetchone()
            if run is None:
                return {
                    "available": True,
                    "configured": False,
                    "run": None,
                    "analyses": [],
                    "verdict": None,
                }
            cur.execute(
                "SELECT * FROM wnba.research_verdicts WHERE research_run_id=%s",
                (run["research_run_id"],),
            )
            verdict_row = cur.fetchone()
            verdict = dict(verdict_row) if verdict_row is not None else None
            cur.execute(
                """SELECT a.*,
                          coalesce(jsonb_agg(jsonb_build_object(
                            'claim_id',c.claim_id,'predicate',c.predicate,'value',c.value,
                            'confidence',c.confidence,'evidence_ids',c.evidence_ids,
                            'expires_at',c.expires_at,'status',c.status)) FILTER (WHERE c.claim_id IS NOT NULL),'[]') AS claims
                   FROM wnba.agent_analyses a
                   LEFT JOIN wnba.research_claims c ON c.analysis_id=a.analysis_id
                   WHERE a.research_run_id=%s GROUP BY a.analysis_id ORDER BY a.created_at""",
                (run["research_run_id"],),
            )
            analyses = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT * FROM wnba.research_audits WHERE research_run_id=%s
                   ORDER BY audited_at DESC LIMIT 1""",
                (run["research_run_id"],),
            )
            audit = cur.fetchone()
            cur.execute(
                "SELECT * FROM wnba.decision_syntheses WHERE research_run_id=%s",
                (run["research_run_id"],),
            )
            synthesis = cur.fetchone()
            cur.execute(
                """SELECT * FROM wnba.agent_forecasts WHERE research_run_id=%s
                   ORDER BY round,agent_role""",
                (run["research_run_id"],),
            )
            agent_forecasts = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT rp.*,d.prop_type,d.line,o.actual_stat,o.hit
                   FROM wnba.research_precedents rp
                   JOIN wnba.decision_episodes d USING(episode_id)
                   JOIN wnba.episode_outcomes o USING(episode_id)
                   WHERE rp.research_run_id=%s ORDER BY rp.rank""",
                (run["research_run_id"],),
            )
            precedents = [dict(row) for row in cur.fetchall()]
            cur.execute(
                """SELECT d.episode_id FROM wnba.stat_forecasts f
                   JOIN wnba.decision_episodes d
                     ON d.quote_id=f.quote_id AND d.model_run_id=f.model_run_id
                   WHERE f.projection_id=%s LIMIT 1""",
                (projection_id,),
            )
            episode = cur.fetchone()
            if episode is not None:
                for analysis in analyses:
                    raw_evidence_ids = analysis["evidence_ids"]
                    evidence_ids = (
                        raw_evidence_ids if isinstance(raw_evidence_ids, list | tuple) else []
                    )
                    for evidence_id in evidence_ids:
                        cur.execute(
                            """INSERT INTO wnba.evidence_interactions
                               (interaction_id,evidence_id,episode_id,analyst,interaction,weight,
                                occurred_at)
                               SELECT %s,%s,%s,'owner','viewed',1,%s
                               WHERE NOT EXISTS (
                                 SELECT 1 FROM wnba.evidence_interactions
                                 WHERE evidence_id=%s AND episode_id=%s
                                   AND analyst='owner' AND interaction='viewed')""",
                            (
                                uuid4(),
                                evidence_id,
                                episode["episode_id"],
                                datetime.now(UTC),
                                evidence_id,
                                episode["episode_id"],
                            ),
                        )
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200]}
    return {
        "available": True,
        "configured": True,
        "run": dict(run),
        "analyses": analyses,
        "verdict": verdict,
        "audit": None if audit is None else dict(audit),
        "synthesis": None if synthesis is None else dict(synthesis),
        "agent_forecasts": agent_forecasts,
        "precedents": precedents,
    }


@app.post("/api/research/{projection_id}/run")
def launch_projection_research(projection_id: UUID) -> dict[str, object]:
    """Owner-triggered research only; no automatic API spending."""
    from wnba_services.research_agents.pat_workflow import run_pat_research

    try:
        result = run_pat_research(projection_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "research_run_id": result.research_run_id,
        "status": result.status,
        "round_one": result.round_one,
        "round_two": result.round_two,
        "precedents": result.precedents,
        "rule_proposed": result.rule_proposed,
    }


@app.post("/api/feedback/{episode_id}")
def submit_feedback(episode_id: UUID, feedback: FeedbackRequest) -> dict[str, object]:
    """Store structured owner feedback as a learning label."""
    from wnba_store.db import connect

    feedback_id = uuid4()
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM wnba.decision_episodes WHERE episode_id=%s", (episode_id,))
        if cur.fetchone() is None:
            raise HTTPException(status_code=404, detail="Unknown decision episode")
        cur.execute(
            """INSERT INTO wnba.analyst_feedback
               (feedback_id,episode_id,analyst,submitted_at,feedback_type,projection_useful,
                evidence_relevant,confidence_appropriate,weakest_assumption,missing_context,
                would_repeat,evidence_ids_useful,evidence_ids_misleading)
               VALUES (%s,%s,'owner',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                feedback_id,
                episode_id,
                datetime.now(UTC),
                feedback.feedback_type,
                feedback.projection_useful,
                feedback.evidence_relevant,
                feedback.confidence_appropriate,
                feedback.weakest_assumption,
                feedback.missing_context,
                feedback.would_repeat,
                feedback.evidence_ids_useful,
                feedback.evidence_ids_misleading,
            ),
        )
        at = datetime.now(UTC)
        for evidence_id in feedback.evidence_ids_useful:
            cur.execute(
                """INSERT INTO wnba.evidence_interactions
                   (interaction_id,evidence_id,episode_id,analyst,interaction,weight,occurred_at)
                   VALUES (%s,%s,%s,'owner','useful',2,%s)""",
                (uuid4(), evidence_id, episode_id, at),
            )
        for evidence_id in feedback.evidence_ids_misleading:
            cur.execute(
                """INSERT INTO wnba.evidence_interactions
                   (interaction_id,evidence_id,episode_id,analyst,interaction,weight,occurred_at)
                   VALUES (%s,%s,%s,'owner','misleading',-3,%s)""",
                (uuid4(), evidence_id, episode_id, at),
            )
        cur.execute(
            """UPDATE wnba.decision_episodes SET analyst_decision=%s
               WHERE episode_id=%s AND analyst_decision IS NULL""",
            (feedback.feedback_type, episode_id),
        )
    return {"feedback_id": feedback_id, "stored": True}


@app.get("/api/learning/proposals")
def learning_proposals() -> dict[str, object]:
    """Human-reviewable experiments generated from repeated errors."""
    from wnba_store.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM wnba.research_proposals ORDER BY proposed_at DESC LIMIT 100")
        proposals = [dict(row) for row in cur.fetchall()]
    return {"proposals": proposals, "automatic_approval": False}


@app.get("/api/learning")
def learning() -> dict[str, object]:
    """One read-only view of the closed learning loop and its human review queue."""
    from wnba_store.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM wnba.research_proposals ORDER BY proposed_at DESC LIMIT 100")
        proposals = [dict(row) for row in cur.fetchall()]
        cur.execute("SELECT * FROM wnba.hypotheses ORDER BY created_at DESC LIMIT 100")
        hypotheses = [dict(row) for row in cur.fetchall()]
        # `e.*` already carries `challenger_name` since migration 025, so aliasing
        # `challenger.name` to the same label put two columns of that name in the result set and
        # left `dict(row)` to silently keep whichever came last. They hold identical strings today
        # only because `ensure_challenger_version` writes both from `challenger.name`; the
        # champion alias is the one that is actually needed.
        cur.execute(
            """SELECT e.*,champion.name AS champion_name
               FROM wnba.experiments e
               JOIN wnba.model_versions champion
                 ON champion.model_version_id=e.champion_model_version_id
               JOIN wnba.model_versions challenger
                 ON challenger.model_version_id=e.challenger_model_version_id
               ORDER BY e.started_at DESC LIMIT 100"""
        )
        experiments = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT agent_role,domain,sample_size,calibration,evidence_accuracy,
                      credibility,calculated_at
               FROM wnba.agent_credibility ORDER BY calculated_at DESC LIMIT 100"""
        )
        credibility = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT feedback_type,count(*) AS labels,avg(projection_useful) AS usefulness,
                      avg(evidence_relevant) AS evidence_relevance
               FROM wnba.analyst_feedback GROUP BY feedback_type ORDER BY labels DESC"""
        )
        feedback = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT rule_id,title,rationale,status,priority,proposed_by,proposed_at,
                      approved_by,approved_at,approval_reason,retired_by,retired_at,
                      retirement_reason,backtest,live_review,last_reviewed_at,suspended_at,
                      suspension_reason,authored_by_model
               FROM wnba.analyst_rules
               ORDER BY proposed_at DESC LIMIT 100"""
        )
        rules = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT rule_id,count(*) AS firings,
                      count(*) FILTER (WHERE shadow) AS shadow_firings,
                      max(fired_at) AS latest_firing
               FROM wnba.rule_firings GROUP BY rule_id ORDER BY firings DESC"""
        )
        rule_firings = [dict(row) for row in cur.fetchall()]
        # How much of the last month's learning the model actually authored, and how often the
        # provider failed. Without this, a loop quietly degraded to templates by an outage reads
        # exactly like a loop that is working.
        cur.execute(
            """SELECT task,
                      count(*) FILTER (WHERE disposition='used') AS used,
                      count(*) FILTER (WHERE disposition='fallback') AS fallback,
                      count(*) FILTER (WHERE disposition='rejected') AS rejected,
                      max(requested_at) AS latest
               FROM wnba.model_advisories
               WHERE requested_at > now() - interval '30 days'
               GROUP BY task ORDER BY count(*) DESC"""
        )
        advisories = [dict(row) for row in cur.fetchall()]
        # Forecasts the system widened on its own measured drift.
        cur.execute(
            """SELECT e.response,count(*) AS events,
                      avg(abs(e.probability_before-0.5)
                          -abs(e.probability_after-0.5)) AS mean_shrink,
                      max(e.applied_at) AS latest,i.component_name,i.severity
               FROM wnba.deescalation_events e
               JOIN wnba.drift_incidents i ON i.incident_id=e.incident_id
               WHERE e.applied_at > now() - interval '30 days'
               GROUP BY e.response,i.component_name,i.severity
               ORDER BY count(*) DESC"""
        )
        deescalations = [dict(row) for row in cur.fetchall()]
    return {
        "automatic_approval": False,
        "proposals": proposals,
        "hypotheses": hypotheses,
        "experiments": experiments,
        "agent_credibility": credibility,
        "feedback": feedback,
        "rules": rules,
        "rule_firings": rule_firings,
        "advisories": advisories,
        "deescalations": deescalations,
    }


@app.get("/api/validation")
def validation() -> dict[str, object]:
    """Historical replay diagnostics by week, market, probability bucket, and model.

    The model name here is ``production_ensemble``. It used to be ``ensemble``, which no row in
    ``backtest_results`` has ever carried, so every panel on this page rendered an empty list and
    read as "no replay has run" rather than as a broken filter.
    """
    from wnba_services.forecasting.challengers import challenger_names
    from wnba_store.db import connect

    champion = "production_ensemble"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT backtest_run_id FROM wnba.backtest_runs WHERE status='complete'
               ORDER BY completed_at DESC LIMIT 1"""
        )
        run = cur.fetchone()
        if run is None:
            return {
                "available": True,
                "weekly": [],
                "markets": [],
                "calibration": [],
                "model_comparison": [],
            }
        run_id = run["backtest_run_id"]
        cur.execute(
            """SELECT date_trunc('week',forecast_as_of) AS week,count(*) AS forecasts,
                      avg(brier) AS brier,avg(log_loss) AS log_loss,
                      avg(abs(actual_stat-projected_mean)) AS mae
               FROM wnba.backtest_results
               WHERE backtest_run_id=%s AND model_name=%s
               GROUP BY 1 ORDER BY 1""",
            (run_id, champion),
        )
        weekly = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT prop_type,count(*) AS forecasts,avg(brier) AS brier,
                      avg(log_loss) AS log_loss,
                      avg(abs(actual_stat-projected_mean)) AS mae
               FROM wnba.backtest_results
               WHERE backtest_run_id=%s AND model_name=%s
               GROUP BY prop_type ORDER BY forecasts DESC""",
            (run_id, champion),
        )
        markets = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT floor(predicted_probability*10)/10 AS bucket,count(*) AS forecasts,
                      avg(predicted_probability) AS predicted,
                      avg(CASE WHEN hit THEN 1.0 ELSE 0.0 END) AS observed
               FROM wnba.backtest_results
               WHERE backtest_run_id=%s AND model_name=%s
               GROUP BY 1 ORDER BY 1""",
            (run_id, champion),
        )
        calibration = [dict(row) for row in cur.fetchall()]
        # Every model the replay scored, side by side on identical snapshots: the naive
        # baselines production must beat, and the challengers being measured against it.
        cur.execute(
            """SELECT model_name,count(*) AS forecasts,avg(brier) AS brier,
                      avg(log_loss) AS log_loss,
                      avg(abs(actual_stat-projected_mean)) AS mae
               FROM wnba.backtest_results
               WHERE backtest_run_id=%s
               GROUP BY model_name ORDER BY avg(log_loss)""",
            (run_id,),
        )
        comparison = [
            {
                **dict(row),
                "role": (
                    "champion"
                    if str(row["model_name"]) == champion
                    else "challenger"
                    if str(row["model_name"]) in challenger_names()
                    else "baseline"
                ),
            }
            for row in cur.fetchall()
        ]
    return {
        "available": True,
        "backtest_run_id": run_id,
        "champion_model": champion,
        "weekly": weekly,
        "markets": markets,
        "calibration": calibration,
        "model_comparison": comparison,
        # Replay is fitted-period evidence about historical markets. It is not the live shadow
        # record, and the two must never be added together into one "sample size".
        "evidence_class": "historical_replay",
    }


@app.get("/api/operations/timeline")
def operations_timeline() -> dict[str, object]:
    """Recent database-backed pipeline events; infrastructure monitoring remains separate."""
    from wnba_store.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT event_type,status,occurred_at,detail FROM (
                 SELECT 'forecast'::text AS event_type,status,completed_at AS occurred_at,
                        detail FROM wnba.model_runs
                 UNION ALL
                 SELECT 'statistics ingestion', 'complete', completed_at,
                        source::text || ' / ' || feed || ' / ' || rows_written || ' rows'
                 FROM wnba.ingested_dates
                 UNION ALL
                 SELECT 'research',status,coalesce(completed_at,started_at),
                        provider || ' / ' || model FROM wnba.research_runs
               ) events ORDER BY occurred_at DESC LIMIT 50"""
        )
        events = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT incident_id,code,level,severity,source,detail,blocks_recommendations,
                      detected_at,resolved_at FROM wnba.dq_incidents
               ORDER BY detected_at DESC LIMIT 50"""
        )
        incidents = [dict(row) for row in cur.fetchall()]
    return {"events": events, "data_quality_incidents": incidents}


@app.get("/api/picks")
def picks() -> dict[str, object]:
    """Owner pick slips, kept distinct from system recommendations."""
    from wnba_store.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT s.*,coalesce(jsonb_agg(jsonb_build_object(
                        'pick_leg_id',l.pick_leg_id,'projection_id',l.projection_id,
                        'player_name',l.player_name,'prop_type',l.prop_type,'side',l.side,
                        'line',l.line,'offered_odds',l.offered_odds,
                        'model_probability',l.model_probability,'result',l.result,
                        'extraction_confidence',l.extraction_confidence,
                        'player_id',l.player_id,'settled_at',l.settled_at,
                        'actual_stat',l.actual_stat,'episode_id',l.episode_id
                      ) ORDER BY l.created_at) FILTER (WHERE l.pick_leg_id IS NOT NULL),'[]') legs
               FROM wnba.pick_slips s LEFT JOIN wnba.pick_legs l USING (pick_slip_id)
               GROUP BY s.pick_slip_id ORDER BY s.created_at DESC LIMIT 200"""
        )
        slips = [dict(row) for row in cur.fetchall()]
        # Settled paper record, which is the only part of this surface that carries information
        # about whether the owner's judgement was any good. Legs that never matched a settled
        # episode are counted separately rather than folded into losses.
        cur.execute(
            """SELECT count(*) FILTER (WHERE result='win') AS won,
                      count(*) FILTER (WHERE result='loss') AS lost,
                      count(*) FILTER (WHERE result IN ('push','void')) AS no_action,
                      count(*) FILTER (WHERE result='pending') AS pending,
                      avg(model_probability) FILTER (WHERE result='win') AS mean_probability_won,
                      avg(model_probability) FILTER (WHERE result='loss') AS mean_probability_lost
               FROM wnba.pick_legs"""
        )
        record = cur.fetchone()
    return {
        "picks": slips,
        "record": {} if record is None else dict(record),
        "analysis_only": True,
        "automatic_wagering": False,
    }


@app.post("/api/picks")
def create_pick(draft: PickSlipDraft) -> dict[str, object]:
    """Save an owner-confirmed pick slip; all entries remain paper records.

    Player names are resolved to identifiers here, while the person who typed the name is still
    present. An unresolved name is stored as-is with a null identifier: the leg is recorded, it
    simply will not settle, and the console shows which ones those are.
    """
    from wnba_services.learning_loop.pick_settlement import resolve_player_id
    from wnba_store.db import connect

    slip_id = uuid4()
    now = datetime.now(UTC)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.pick_slips
               (pick_slip_id,title,source,status,entry_type,platform,stake,potential_payout,
                notes,is_paper,created_at,updated_at)
               VALUES (%s,%s,%s,'confirmed',%s,%s,%s,%s,%s,true,%s,%s)""",
            (
                slip_id,
                draft.title,
                draft.source,
                draft.entry_type,
                draft.platform,
                draft.stake,
                draft.potential_payout,
                draft.notes,
                now,
                now,
            ),
        )
        unresolved: list[str] = []
        for leg in draft.legs:
            player_id = resolve_player_id(cur, leg.player_name)
            if player_id is None:
                unresolved.append(leg.player_name)
            cur.execute(
                """INSERT INTO wnba.pick_legs
                   (pick_leg_id,pick_slip_id,projection_id,player_name,prop_type,side,line,
                    offered_odds,model_probability,extraction_confidence,player_id)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (
                    uuid4(),
                    slip_id,
                    leg.projection_id,
                    leg.player_name,
                    leg.prop_type,
                    leg.side,
                    leg.line,
                    leg.offered_odds,
                    leg.model_probability,
                    leg.extraction_confidence,
                    player_id,
                ),
            )
    return {
        "pick_slip_id": slip_id,
        "stored": True,
        "is_paper": True,
        "unresolved_players": unresolved,
    }


@app.post("/api/picks/parse-text")
def parse_pick_text(request: PickTextRequest) -> dict[str, object]:
    draft = _parse_pick_text(request.text, "ai_text")
    return {"draft": draft, "requires_confirmation": True}


@app.post("/api/picks/parse-screenshot")
def parse_pick_screenshot(request: PickScreenshotRequest) -> dict[str, object]:
    """OCR an owner screenshot, then ask DeepSeek to structure—not approve—the extracted slip."""
    from wnba_store.db import connect

    try:
        content = base64.b64decode(request.data_base64, validate=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid base64 image") from exc
    if len(content) > 10_000_000:
        raise HTTPException(status_code=413, detail="Screenshot exceeds the 10 MB limit")
    signatures = {"image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}
    if not content.startswith(signatures[request.content_type]):
        raise HTTPException(status_code=422, detail="File content does not match its image type")
    upload_id = uuid4()
    extension = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[
        request.content_type
    ]
    upload_dir = Path(os.getenv("WNBA_UPLOAD_DIR", "/var/lib/wnba/uploads"))
    upload_dir.mkdir(parents=True, exist_ok=True)
    image_path = upload_dir / f"{upload_id}.{extension}"
    image_path.write_bytes(content)
    try:
        completed = subprocess.run(
            ["tesseract", str(image_path), "stdout", "--psm", "6"],
            check=True,
            capture_output=True,
            text=True,
            timeout=45,
        )
        ocr_text = completed.stdout.strip()
        if len(ocr_text) < 3:
            raise ValueError("OCR did not find enough text")
        draft = _parse_pick_text(ocr_text, "screenshot")
        status, error = "parsed", None
    except (FileNotFoundError, subprocess.SubprocessError, ValueError) as exc:
        ocr_text, draft, status, error = "", None, "failed", str(exc)[:500]
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.pick_uploads
               (upload_id,original_filename,content_type,storage_path,content_sha256,
                ocr_text,parse_status,error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                upload_id,
                request.filename,
                request.content_type,
                str(image_path),
                hashlib.sha256(content).hexdigest(),
                ocr_text,
                status,
                error,
            ),
        )
    if draft is None:
        raise HTTPException(status_code=422, detail=error or "Screenshot could not be parsed")
    return {
        "upload_id": upload_id,
        "ocr_text": ocr_text,
        "draft": draft,
        "requires_confirmation": True,
    }


@app.get("/api/readiness")
def readiness() -> dict[str, object]:
    """Latest fail-closed real-money gate evaluation."""
    from wnba_store.db import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT * FROM wnba.readiness_evaluations
               ORDER BY evaluated_at DESC LIMIT 1"""
        )
        evaluation = cur.fetchone()
        if evaluation is None:
            return {"available": True, "evaluation": None, "gates": []}
        cur.execute(
            """SELECT gate_id,status,evidence_source,observed_value,threshold_value,detail
               FROM wnba.readiness_gate_results WHERE readiness_evaluation_id=%s
               ORDER BY gate_id""",
            (evaluation["readiness_evaluation_id"],),
        )
        gates = [dict(row) for row in cur.fetchall()]
    return {"available": True, "evaluation": dict(evaluation), "gates": gates}
