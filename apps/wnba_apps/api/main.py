"""Analyst console API.

The console serves the live market archive and shadow baseline forecasts. Its advanced pricing
lab runs the same correlation and payout engines against hand-entered probabilities, clearly
separated from model output. Shadow forecasts remain paper-only until validation gates pass.

The engines behind it are real: the same copula simulator and payout math the batch pipeline
will use, with no separate "web" implementation to drift out of sync.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
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
from wnba_sim import correct_count_pmf, effective_leg_correlation

STATIC_DIR = Path(__file__).parent / "static"

# Tables are unverified defaults; this is when they were last bundled, not when they were
# confirmed against the live product. The console surfaces that distinction prominently.
BUNDLED_AT = datetime(2026, 8, 3, tzinfo=UTC)

app = FastAPI(
    title="WNBA Prop Intelligence -- Analyst Console",
    description="Analysis only. This system never places a wager or moves money.",
    version="0.1.0",
)


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


class PriceRequest(BaseModel):
    source: Literal["prizepicks", "underdog"] = "prizepicks"
    entry_type: Literal["power", "flex"] = "power"
    leg_probabilities: Annotated[list[float], Field(min_length=2, max_length=8)]
    correlation: Annotated[float, Field(gt=-1.0, lt=1.0)] = 0.0
    simulations: Annotated[int, Field(ge=1_000, le=500_000)] = 100_000
    seed: Annotated[int, Field(ge=0)] = 0
    already_staked_this_game: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0
    already_staked_today: Annotated[float, Field(ge=0.0, le=1.0)] = 0.0


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
        "phase": "2 -- shadow forecasting",
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

    if request.correlation < 0.0 and leg_count > 2:
        raise HTTPException(
            status_code=422,
            detail=(
                "a one-factor model cannot represent negative correlation across more than "
                "two legs -- the implied joint distribution does not exist"
            ),
        )

    correlated = correct_count_pmf(
        request.leg_probabilities,
        correlation=request.correlation,
        simulations=request.simulations,
        seed=request.seed,
    )
    independent = independent_correct_count_pmf(request.leg_probabilities)

    ev = entry_expected_value(correlated, rule)
    ev_independent = entry_expected_value(independent, rule)

    policy = RiskPolicy()
    stake = staked_fraction(
        correlated,
        rule,
        policy,
        expected_value=ev,
        already_staked_this_game=request.already_staked_this_game,
        already_staked_today=request.already_staked_today,
    )

    breakeven = breakeven_uniform_leg_probability(rule)
    gates: list[str] = []
    if ev < policy.min_expected_value:
        gates.append(
            f"edge {ev:.1%} is below the {policy.min_expected_value:.0%} minimum -- thin edges "
            "on unvalidated probabilities are noise wearing a decimal point"
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
    gates.append("market data is live, but these probabilities are hand-entered—not model output")

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
    )


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
        "phase": "2 -- shadow forecasting",
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
            {"name": "baseline forecasting", "state": "built -- challenger/shadow only"},
            {"name": "research agents", "state": "not started"},
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
                """SELECT DISTINCT ON (f.quote_id) f.projection_id,p.full_name,f.prop_type,
                          f.line,f.mean,f.median,f.stddev,f.probability_over,
                          f.probability_under,f.projected_minutes,f.sample_size,
                          f.data_quality_score,f.confidence,f.generated_at,f.expires_at,
                          d.side,d.predicted_probability,d.system_recommendation,q.source,
                          g.scheduled_tipoff,i.designation AS injury_designation,
                          i.detail AS injury_detail,r.availability_probability,
                          r.start_probability,r.closing_lineup_probability,
                          r.minutes_std AS projected_minutes_std,r.model_version AS role_model,
                          e.teammate_effect_count,e.rate_multiplier AS teammate_rate_multiplier,
                          e.minutes_delta AS teammate_minutes_delta,e.effect_confidence
                   FROM wnba.stat_forecasts f
                   JOIN wnba.players p ON p.player_id=f.player_id
                   JOIN wnba.decision_episodes d ON d.model_run_id=f.model_run_id
                     AND d.quote_id=f.quote_id
                   JOIN wnba.prop_quotes q ON q.quote_id=f.quote_id
                   JOIN wnba.games g ON g.game_id=f.game_id
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
                   WHERE f.expires_at>now() AND coalesce(i.designation,'available')<>'out'
                   ORDER BY f.quote_id,f.generated_at DESC"""
            )
            rows = [dict(row) for row in cur.fetchall()]
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200], "forecasts": []}
    rows.sort(key=lambda row: float(str(row["predicted_probability"])), reverse=True)
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
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:200]}
    return {
        "available": True,
        "analysis_only": True,
        "minimum_validation_sample": 500,
        "summary": summary,
        "calibration": calibration,
        "by_market": by_market,
    }
