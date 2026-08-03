# WNBA Player-Prop Intelligence Platform

A decision-support platform for WNBA player props. It is **not** a pick generator. It is a
calibrated probability engine with an auditable evidence trail, priced against the market and
wrapped in hard risk limits.

> **Analysis only.** This system never logs into a sportsbook, never places a wager, and never
> moves money. It produces probabilities, evidence and sizing math for a human to act on.

## Design influences

| Influence | What we actually take from it |
|---|---|
| Bridgewater **PAT** | Codified analyst reasoning, explicit causal hypotheses, evidence trails, adversarial review, and a closed loop that turns every outcome into a diagnosis. |
| Palantir **Ontology** | One canonical object/link/action model over fragmented sources; actions are first-class, audited objects. |
| **Pydantic v2 strict** | Typed, validated contracts at every boundary. Bad data is quarantined, never silently coerced. |
| `abdullahtarek/basketball_analysis` | A computer-vision *sensor* for features the box score cannot express. A sensor inside the system — not the system. |

## The seven invariants

These are enforced by tests that fail the build, not by documentation nobody reads.

1. **Bitemporality.** Every fact carries `valid_from/valid_to` (when it was true in the world) and
   `system_from/system_to` (when we learned it). All historical reads go through `as_of()`.
2. **No look-ahead.** `tests/leakage/` asserts every feature vector for a forecast at time *T* was
   computable from rows with `system_from <= T`. This is what stops a backtest from being fiction.
3. **Starter status is a probability, never a boolean.** The WNBA does not require pre-tip lineup
   submission, so "confirmed starter" is not an available input. There is deliberately no
   `is_starter: bool` field anywhere in the domain — the type system enforces the epistemics.
4. **Strict validation everywhere.** `strict=True, extra="forbid", validate_assignment=True`.
5. **Probability first, profit second.** Log loss and Brier are primary. A profit-only feedback
   loop rewards a lucky 51% and punishes an unlucky 75%.
6. **Asymmetric autonomy.** The system may automatically become *more* cautious — widen intervals,
   cut stakes, disable a market. Becoming *more* aggressive always requires a human.
7. **Analysis only.** No book automation, no wagering, no fund movement.

## Free-forever data stack

No paid feeds. That has one large consequence: historical two-sided prop odds are paywalled
everywhere, so **we are our own odds archive**. The line archiver runs from day one, before any
model exists, because every day it is off is evaluation data we can never recover.

| Layer | Source |
|---|---|
| Historical PBP / box scores | `sportsdataverse-py` (`.wnba`), `sportsdataverse/wehoop-wnba-data` parquet releases |
| Possession parsing | `pbpstats` |
| Schedule, rosters, injuries, game lines | ESPN public JSON endpoints |
| Prop lines | PrizePicks and Underdog public JSON endpoints |
| Research agents | Claude Code skills, run locally |

Because the free market is DFS pick'em rather than two-sided sportsbook prices, there is no
no-vig fair price to solve for. Entry expected value instead depends on the **joint** distribution
across 2–6 correlated legs under a fixed payout table. That promotes the Monte Carlo simulator
from optional refinement to load-bearing core, and makes correlation modeling the central edge.

**Source etiquette.** The PrizePicks/Underdog endpoints are undocumented. Adapters poll at
`WNBA_MIN_POLL_INTERVAL_SECONDS` (≥60s) from a single client with an honest user agent and back
off on 429. This is for personal analysis. Respect the terms of any source you point it at.

## Layout

```
packages/     wnba_domain  wnba_ontology  wnba_store  wnba_quality
              wnba_marketmath  wnba_sim  wnba_obs
services/     ingestion  feature_engine  forecasting  market_engine
              research_agents  learning_loop  monitoring  video_intelligence
apps/         cli (primary interface)  api (FastAPI)
ontology/     objects.yaml  links.yaml  actions.yaml  policies.yaml
tests/        unit  integration  data_quality  leakage  backtest
```

`services/video_intelligence/` is an isolated sandbox excluded from the uv workspace. It has its
own dependencies and **zero production dependency** — the statistical system never imports it.

## Setup

```bash
uv sync --all-groups
```

Copy `.env.example` to `.env` and fill in Supabase credentials.

## Common commands

Run the full check suite (ruff, mypy strict, pytest, leakage and data-quality gates):

```bash
uv run python tasks.py check
```

Poll and archive current prop lines:

```bash
uv run wnba lines poll
```

## Status

Phase 0 — foundation. See the build plan for phase gates and the ten criteria that must all pass
before any real-money use.

A profitable backtest is not evidence. It is a suspect awaiting questioning.
