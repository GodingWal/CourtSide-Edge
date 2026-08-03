# ADR-0001: Use VPS PostgreSQL as the operational store

**Status:** Accepted  
**Date:** 2026-08-03  
**Deciders:** Project owner

## Context

The original plan proposed Supabase because Docker was unavailable on the development machine.
The production VPS already runs PostgreSQL, binds it to loopback, has ample disk and memory, and
hosts the ingestion and web processes. The project must remain free to operate.

## Decision

Use the existing VPS PostgreSQL instance with a dedicated `wnba` schema. Keep analytical bulk
files outside PostgreSQL when appropriate. Apply append-only bitemporal migrations from source
control and back up the schema nightly.

## Options considered

| Option | Complexity | Cost | Operational fit |
|---|---:|---:|---|
| VPS PostgreSQL | Medium | $0 incremental | Best locality; we own backup and recovery |
| Supabase free tier | Low | $0 | Storage/idle limits and another network dependency |
| DuckDB only | Low | $0 | Excellent analytics, poor concurrent operational store |

## Consequences

- Ingestion and API reads stay local to the VPS and do not expose port 5432 publicly.
- We own upgrades, least-privilege roles, monitoring, backups and restore drills.
- A VPS loss is a database loss unless off-host replication is healthy.
- DuckDB/Parquet remains appropriate for model matrices and large play-by-play datasets.

## Follow-up

1. Automate encrypted off-host backup replication.
2. Create separate migration, writer and reader roles.
3. Run and document a restore drill on a disposable database.
4. Monitor database size, backup age and failed archive polls.

