"""Transparent empirical baseline forecasts and paper-only decision episodes."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from psycopg.types.json import Jsonb
from wnba_store.db import connect

SUPPORTED = {
    "points": ("points",),
    "rebounds": ("rebounds_offensive", "rebounds_defensive"),
    "assists": ("assists",),
    "three_pointers": ("three_pointers_made",),
    "points_rebounds_assists": ("points", "rebounds_offensive", "rebounds_defensive", "assists"),
    "points_rebounds": ("points", "rebounds_offensive", "rebounds_defensive"),
    "points_assists": ("points", "assists"),
    "rebounds_assists": ("rebounds_offensive", "rebounds_defensive", "assists"),
}
MODEL_ID = uuid5(NAMESPACE_URL, "courtside-edge:empirical-minutes-rate:0.1.0")


@dataclass(frozen=True)
class ForecastBatch:
    model_run_id: UUID
    forecasts: int
    episodes: int
    skipped: int


def _weighted_mean(values: list[float]) -> float:
    weights = list(range(1, len(values) + 1))
    return sum(v * w for v, w in zip(values, weights, strict=True)) / sum(weights)


def _simulate(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    seed: int,
    *,
    projected_minutes: float | None = None,
    projected_minutes_std: float | None = None,
    rate_multiplier: float = 1.0,
) -> list[int]:
    rng = random.Random(seed)
    recent = rows[-20:]
    minutes = [float(r["minutes"]) for r in recent if float(r["minutes"]) > 0]
    expected_minutes = projected_minutes or _weighted_mean(minutes)
    minutes_std = projected_minutes_std or max(2.0, _std(minutes))
    rates = [
        sum(float(r[c]) for c in columns) / float(r["minutes"])
        for r in recent
        if float(r["minutes"]) > 0
    ]
    outcomes: list[int] = []
    for _ in range(10_000):
        sampled_minutes = max(0.0, min(45.0, rng.gauss(expected_minutes, minutes_std)))
        sampled_rate = max(0.0, rng.choice(rates) * rate_multiplier)
        expectation = sampled_minutes * sampled_rate
        # Poisson sampling preserves count support and naturally widens high-volume props.
        limit = math.exp(-expectation)
        k, product = 0, 1.0
        while product > limit and k < 200:
            k += 1
            product *= rng.random()
        outcomes.append(max(0, k - 1))
    return outcomes


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def run_baseline(*, now: datetime | None = None, seed: int = 20260803) -> ForecastBatch:
    """Forecast the current board. Every recommendation remains shadow/paper-only."""
    at = now or datetime.now(UTC)
    started = datetime.now(UTC)
    run_id = uuid4()
    forecasts = episodes = skipped = 0
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO wnba.model_versions
               (model_version_id,name,semver,target,stage,specification)
               VALUES (%s,'empirical-minutes-rate','0.1.0','multi','challenger',%s)
               ON CONFLICT DO NOTHING""",
            (MODEL_ID, Jsonb({"history_games": 20, "simulations": 10_000, "analysis_only": True})),
        )
        cur.execute(
            """INSERT INTO wnba.model_runs
               (model_run_id,model_version_id,started_at,completed_at,random_seed,status,detail)
               VALUES (%s,%s,%s,%s,%s,'running','')""",
            (run_id, MODEL_ID, started, started, seed),
        )
        cur.execute(
            """SELECT DISTINCT ON (q.source,q.source_quote_id) q.*,
                      coalesce(m.to_player_id,q.player_id) AS canonical_player_id
               FROM wnba.prop_quotes q
               LEFT JOIN wnba.player_merges m
                 ON m.from_player_id=q.player_id AND m.system_to IS NULL
               WHERE q.is_available AND q.game_id IS NOT NULL AND q.locks_at>%s
                 AND NOT EXISTS (
                     SELECT 1 FROM wnba.stat_forecasts f
                     WHERE f.quote_id=q.quote_id AND f.expires_at=q.locks_at
                       AND NOT EXISTS (
                           SELECT 1 FROM wnba.injury_status i
                           WHERE i.player_id=coalesce(m.to_player_id,q.player_id)
                             AND i.game_id=q.game_id AND i.system_to IS NULL
                             AND i.system_from>f.generated_at)
                       AND NOT EXISTS (
                           SELECT 1 FROM wnba.projected_roles r
                           WHERE r.player_id=coalesce(m.to_player_id,q.player_id)
                             AND r.game_id=q.game_id AND r.system_to IS NULL
                             AND r.system_from>f.generated_at)
                       AND NOT EXISTS (
                           SELECT 1 FROM wnba.teammate_role_effects e
                           WHERE e.player_id=coalesce(m.to_player_id,q.player_id)
                             AND e.game_id=q.game_id AND e.prop_type=q.prop_type
                             AND e.system_to IS NULL AND e.system_from>f.generated_at)
                       AND NOT EXISTS (
                           SELECT 1 FROM wnba.matchup_contexts c
                           WHERE c.game_id=q.game_id AND c.prop_type=q.prop_type
                             AND c.system_to IS NULL AND c.system_from>f.generated_at))
               ORDER BY q.source,q.source_quote_id,q.system_from DESC""",
            (at,),
        )
        quotes = cur.fetchall()
        for quote in quotes:
            prop = str(quote["prop_type"])
            columns = SUPPORTED.get(prop)
            if columns is None:
                skipped += 1
                continue
            player_id = UUID(str(quote["canonical_player_id"]))
            cur.execute(
                """SELECT l.*,g.scheduled_tipoff FROM wnba.player_game_lines l
                   JOIN wnba.games g ON g.game_id=l.game_id
                   WHERE l.player_id=%s AND l.system_to IS NULL AND g.status='final'
                     AND g.scheduled_tipoff<%s ORDER BY g.scheduled_tipoff DESC LIMIT 20""",
                (player_id, quote["locks_at"]),
            )
            history = list(reversed(cur.fetchall()))
            if len(history) < 10:
                skipped += 1
                continue
            cur.execute(
                """SELECT designation,detail FROM wnba.injury_status
                   WHERE player_id=%s AND game_id=%s AND system_to IS NULL
                   ORDER BY system_from DESC LIMIT 1""",
                (player_id, quote["game_id"]),
            )
            injury = cur.fetchone()
            designation = "available" if injury is None else str(injury["designation"])
            if designation == "out":
                skipped += 1
                continue
            cur.execute(
                """SELECT availability_probability,start_probability,
                          closing_lineup_probability,expected_minutes,minutes_std,
                          minutes_restriction_probability,model_version
                   FROM wnba.projected_roles
                   WHERE player_id=%s AND game_id=%s AND system_to IS NULL""",
                (player_id, quote["game_id"]),
            )
            role = cur.fetchone()
            cur.execute(
                """SELECT coalesce(exp(sum(ln(rate_multiplier))),1.0) AS rate_multiplier,
                          coalesce(sum(minutes_delta),0.0) AS minutes_delta,
                          count(*) AS effects,min(confidence) AS confidence
                   FROM wnba.teammate_role_effects
                   WHERE player_id=%s AND game_id=%s AND prop_type=%s AND system_to IS NULL""",
                (player_id, quote["game_id"], prop),
            )
            teammate_effect = cur.fetchone()
            team_id = history[-1]["team_id"]
            cur.execute(
                """SELECT expected_possessions,pace_multiplier,defense_multiplier,
                          expected_margin,blowout_probability,team_rest_days,
                          opponent_rest_days,confidence,method_version
                   FROM wnba.matchup_contexts
                   WHERE game_id=%s AND team_id=%s AND prop_type=%s AND system_to IS NULL""",
                (quote["game_id"], team_id, prop),
            )
            matchup = cur.fetchone()
            local_seed = seed ^ int(str(quote["quote_id"]).replace("-", "")[:8], 16)
            role_minutes = None if role is None else float(str(role["expected_minutes"]))
            role_std = None if role is None else float(str(role["minutes_std"]))
            effect_count = int(str(teammate_effect["effects"])) if teammate_effect else 0
            rate_multiplier = (
                1.0
                if not teammate_effect
                else max(0.75, min(1.35, float(str(teammate_effect["rate_multiplier"]))))
            )
            if role_minutes is not None and teammate_effect:
                role_minutes = max(
                    0.0,
                    min(45.0, role_minutes + float(str(teammate_effect["minutes_delta"]))),
                )
            if role_minutes is not None and matchup:
                role_minutes = max(
                    0.0,
                    role_minutes - 1.5 * float(str(matchup["blowout_probability"])),
                )
            matchup_multiplier = (
                1.0
                if not matchup
                else max(
                    0.8,
                    min(
                        1.25,
                        float(str(matchup["pace_multiplier"]))
                        * float(str(matchup["defense_multiplier"])),
                    ),
                )
            )
            sims = _simulate(
                history,
                columns,
                local_seed,
                projected_minutes=role_minutes,
                projected_minutes_std=role_std,
                rate_multiplier=rate_multiplier * matchup_multiplier,
            )
            line = Decimal(str(quote["line"]))
            over = sum(Decimal(x) > line for x in sims) / len(sims)
            push = sum(Decimal(x) == line for x in sims) / len(sims)
            under = 1.0 - over - push
            mean = sum(sims) / len(sims)
            ordered = sorted(sims)
            projected_minutes = role_minutes or _weighted_mean(
                [float(str(r["minutes"])) for r in history]
            )
            quality = min(1.0, 0.7 + len(history) / 100)
            confidence = min(
                0.75,
                quality * (1.0 - min(0.5, _std([float(x) for x in sims]) / max(1.0, mean) / 2)),
            )
            if designation in {"questionable", "doubtful", "unknown"}:
                confidence *= 0.7
            elif designation == "probable":
                confidence *= 0.9
            feature_id, projection_id = uuid4(), uuid4()
            cur.execute(
                """INSERT INTO wnba.feature_snapshots
                   (feature_snapshot_id,player_id,game_id,as_of,features,source_line_ids)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (
                    feature_id,
                    player_id,
                    quote["game_id"],
                    at,
                    Jsonb(
                        {
                            "history_games": len(history),
                            "expected_minutes": projected_minutes,
                            "columns": columns,
                            "injury_designation": designation,
                            "injury_detail": None if injury is None else injury["detail"],
                            "role_model": None if role is None else role["model_version"],
                            "start_probability": None
                            if role is None
                            else role["start_probability"],
                            "closing_lineup_probability": None
                            if role is None
                            else role["closing_lineup_probability"],
                            "teammate_effect_count": effect_count,
                            "teammate_rate_multiplier": rate_multiplier,
                            "teammate_effect_confidence": None
                            if not teammate_effect
                            else teammate_effect["confidence"],
                            "matchup_model": None if not matchup else matchup["method_version"],
                            "expected_possessions": None
                            if not matchup
                            else matchup["expected_possessions"],
                            "pace_multiplier": None if not matchup else matchup["pace_multiplier"],
                            "defense_multiplier": None
                            if not matchup
                            else matchup["defense_multiplier"],
                            "expected_margin": None if not matchup else matchup["expected_margin"],
                            "blowout_probability": None
                            if not matchup
                            else matchup["blowout_probability"],
                            "team_rest_days": None if not matchup else matchup["team_rest_days"],
                            "opponent_rest_days": None
                            if not matchup
                            else matchup["opponent_rest_days"],
                        }
                    ),
                    [r["line_id"] for r in history],
                ),
            )
            distribution = {str(k): sims.count(k) / len(sims) for k in set(sims)}
            cur.execute(
                """INSERT INTO wnba.stat_forecasts
                   (projection_id,model_run_id,feature_snapshot_id,quote_id,player_id,game_id,
                    prop_type,line,mean,median,stddev,probability_over,probability_push,
                    probability_under,projected_minutes,sample_size,data_quality_score,confidence,
                    distribution,generated_at,expires_at,is_shadow)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)""",
                (
                    projection_id,
                    run_id,
                    feature_id,
                    quote["quote_id"],
                    player_id,
                    quote["game_id"],
                    prop,
                    line,
                    mean,
                    ordered[len(ordered) // 2],
                    _std([float(x) for x in sims]),
                    over,
                    push,
                    under,
                    projected_minutes,
                    len(history),
                    quality,
                    confidence,
                    Jsonb(distribution),
                    at,
                    quote["locks_at"],
                ),
            )
            side, probability = ("over", over) if over >= under else ("under", under)
            status = "candidate" if probability >= 0.58 and quality >= 0.85 else "declined_no_edge"
            if designation in {"questionable", "doubtful", "unknown"}:
                status = "blocked_by_skeptic"
            cur.execute(
                """INSERT INTO wnba.decision_episodes
                   (episode_id,forecast_timestamp,player_id,game_id,prop_type,side,line,source,
                    quote_id,multiplier,projected_mean,projected_median,predicted_probability,
                    projected_minutes,confidence,data_quality_score,model_disagreement,
                    model_version_ids,model_run_id,feature_snapshot_id,system_recommendation,is_paper)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s,true)""",
                (
                    uuid4(),
                    at,
                    player_id,
                    quote["game_id"],
                    prop,
                    side,
                    line,
                    quote["source"],
                    quote["quote_id"],
                    mean,
                    ordered[len(ordered) // 2],
                    probability,
                    projected_minutes,
                    confidence,
                    quality,
                    [MODEL_ID],
                    run_id,
                    feature_id,
                    status,
                ),
            )
            forecasts += 1
            episodes += 1
        completed = datetime.now(UTC)
        cur.execute(
            """UPDATE wnba.model_runs SET completed_at=%s,status='complete',detail=%s
               WHERE model_run_id=%s""",
            (completed, f"forecasts={forecasts};skipped={skipped}", run_id),
        )
    return ForecastBatch(run_id, forecasts, episodes, skipped)
