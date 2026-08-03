"""Storage: bitemporal selection, Postgres repositories, and the local analytics lake.

Split by access pattern, and by the Supabase free tier's 500 MB ceiling:

* **Postgres (Supabase)** -- hot operational state and the learning loop: quotes, injuries,
  projected roles, decision episodes, outcomes, incidents. Small, transactional, queried by key.
* **DuckDB + Parquet (local)** -- bulk history: play-by-play, shots, possessions, backfilled box
  scores. Large, append-only, scanned analytically.

:mod:`wnba_store.temporal` is the chokepoint for every historical read, and has no database
dependency at all -- so the leakage rules can be tested on plain objects, fast, in CI.
"""

from __future__ import annotations

from wnba_store.temporal import (
    LeakageError,
    as_of,
    assert_no_leakage,
    latest_known,
    timeline,
)

__all__ = [
    "LeakageError",
    "as_of",
    "assert_no_leakage",
    "latest_known",
    "timeline",
]

__version__ = "0.1.0"
