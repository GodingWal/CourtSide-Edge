# WNBA Player-Prop Intelligence Platform

A decision-support platform for WNBA player props. It is **not** a pick generator. It is a
calibrated probability engine with an auditable evidence trail, priced against the market and
wrapped in hard risk limits.

> **Analysis only.** This system never logs into a sportsbook, places a wager, or moves money.
> It produces probabilities, evidence, and sizing math for a human to evaluate.

## Design influences

| Influence | What we use |
|---|---|
| Bridgewater PAT | Codified analyst reasoning, causal hypotheses, evidence trails, adversarial review, and a closed learning loop |
| Palantir Ontology | One canonical object/link/action model over fragmented sources, with audited actions |
| Pydantic v2 strict | Typed and validated contracts at every boundary; rejected data goes to quarantine |
| `abdullahtarek/basketball_analysis` | An experimental computer-vision sensor, never a production dependency |

## Seven enforced invariants

1. **Bitemporality.** Facts carry both world time and system-observation time.
2. **No look-ahead.** Historical features may use only records knowable at forecast time.
3. **Starter status is a probability.** The WNBA does not require pre-tip lineup submission.
4. **Strict validation.** Boundary models forbid extra fields and silent coercion.
5. **Probability first.** Log loss, Brier score, and calibration precede profit.
6. **Asymmetric autonomy.** Automation may reduce exposure, never expand it.
7. **Analysis only.** No book automation, wager execution, or fund movement.

These rules are backed by tests and database constraints rather than documentation alone.

## Data stack

The project is free to operate. Historical prop prices cannot be reconstructed later, so the
market archiver runs before any model exists.

| Layer | Source |
|---|---|
| Operational data | PostgreSQL on the VPS, schema `wnba` |
| Market lines | Underdog public endpoint; changed source states only |
| Historical stats to normalize | Rescued legacy SQLite plus ESPN/wehoop backfill |
| Planned play-by-play | wehoop / SportsDataverse and pbpstats |
| Analytics | DuckDB/Parquet where bulk matrices do not belong in PostgreSQL |

Underdog currently exposes American prices on both directions for most WNBA lines, allowing
vig removal. PrizePicks presents bot defence to automated clients and is not accessed or worked
around. The Underdog adapter uses an honest user agent, a minimum polling interval, and backoff.

## Layout

```text
packages/       domain, ontology, store, quality, market math, simulation, observability
services/       ingestion, feature engine, forecasting, market engine, research, monitoring
apps/           CLI and FastAPI analyst console
ontology/       objects, links, actions, and policies
infrastructure/ migrations, systemd units, and backup scripts
tests/          unit, integration, data quality, leakage, and backtest gates
```

`services/video_intelligence/` remains isolated from the production dependency graph.

## Local setup

```bash
uv sync --all-groups
```

Copy `.env.example` to `.env` and set `WNBA_DATABASE_URL` for PostgreSQL.

## Common commands

```bash
# Full quality gate
uv run python tasks.py check

# Apply migrations
uv run wnba db migrate

# Poll the market once
uv run wnba lines poll

# Report archive coverage
uv run wnba lines coverage
```

## Status

**Phase 1—data foundation.** The PostgreSQL archive, nightly local backup, pricing libraries,
and public console are operational. Historical normalization, baseline forecasts, and paper
decision settlement are next. See [docs/ROADMAP.md](docs/ROADMAP.md).

A profitable backtest is not evidence. It is a suspect awaiting questioning.
