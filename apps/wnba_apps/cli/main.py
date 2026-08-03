"""The `wnba` command line.

The primary interface for Phase 0/1. Deliberately boring: poll, migrate, report.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from wnba_services.feature_engine.matchup import project_matchup_contexts
from wnba_services.feature_engine.roles import project_current_roles
from wnba_services.feature_engine.teammate_effects import project_teammate_effects
from wnba_services.forecasting.baseline import run_baseline
from wnba_services.ingestion.archiver import run_archiver
from wnba_services.ingestion.espn import backfill_espn, ingest_espn_date
from wnba_services.ingestion.identity import approve_unique_exact_names
from wnba_services.ingestion.legacy import import_legacy_sqlite
from wnba_services.ingestion.wnba_injuries import ingest_official_injuries
from wnba_services.learning_loop.settlement import settle_paper_episodes
from wnba_store.db import connect, migrate

app = typer.Typer(
    help="WNBA player-prop intelligence. Analysis only -- never places a wager.",
    no_args_is_help=True,
    add_completion=False,
)
lines_app = typer.Typer(help="Market line archive.", no_args_is_help=True)
db_app = typer.Typer(help="Database schema.", no_args_is_help=True)
data_app = typer.Typer(help="Historical and reference data.", no_args_is_help=True)
stats_app = typer.Typer(help="Canonical WNBA schedule and box scores.", no_args_is_help=True)
forecast_app = typer.Typer(
    help="Versioned, paper-only probability forecasts.", no_args_is_help=True
)
identity_app = typer.Typer(help="Audited cross-source identity review.", no_args_is_help=True)
learning_app = typer.Typer(
    help="Paper settlement, scoring, and learning loop.", no_args_is_help=True
)
injuries_app = typer.Typer(help="Official bitemporal WNBA injury reports.", no_args_is_help=True)
roles_app = typer.Typer(help="Projected availability, starts, and minutes.", no_args_is_help=True)
effects_app = typer.Typer(help="Shrunk teammate-absence role effects.", no_args_is_help=True)
matchups_app = typer.Typer(help="Pace, defense, rest, and blowout context.", no_args_is_help=True)
app.add_typer(lines_app, name="lines")
app.add_typer(db_app, name="db")
app.add_typer(data_app, name="data")
app.add_typer(stats_app, name="stats")
app.add_typer(forecast_app, name="forecast")
app.add_typer(identity_app, name="identity")
app.add_typer(learning_app, name="learning")
app.add_typer(injuries_app, name="injuries")
app.add_typer(roles_app, name="roles")
app.add_typer(effects_app, name="effects")
app.add_typer(matchups_app, name="matchups")

console = Console()


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter("expected YYYY-MM-DD") from exc


@db_app.command("migrate")
def db_migrate(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show pending, apply nothing.")
    ] = False,
) -> None:
    """Apply pending migrations in filename order."""
    applied = migrate(dry_run=dry_run)
    if not applied:
        console.print("[green]schema up to date[/green]")
        return
    verb = "pending" if dry_run else "applied"
    for version in applied:
        console.print(f"  {verb}: {version}")


@db_app.command("status")
def db_status() -> None:
    """Row counts for the tables that matter."""
    with connect() as conn, conn.cursor() as cur:
        table = Table(title="wnba schema")
        table.add_column("table")
        table.add_column("rows", justify="right")
        for name in (
            "prop_quotes",
            "players",
            "player_aliases",
            "player_game_lines",
            "injury_status",
            "decision_episodes",
            "quarantine",
            "dq_incidents",
        ):
            cur.execute(f"SELECT count(*) AS n FROM wnba.{name}")
            row = cur.fetchone()
            table.add_row(name, f"{row['n']:,}" if row else "?")
        console.print(table)


@data_app.command("import-legacy")
def data_import_legacy(
    sqlite_path: Annotated[
        Path,
        typer.Argument(exists=True, file_okay=True, dir_okay=False, readable=True),
    ],
) -> None:
    """Preserve the rescued CourtSide Edge SQLite data in immutable staging tables."""
    result = import_legacy_sqlite(sqlite_path)
    console.print(
        f"[green]legacy import complete[/green] id={result.import_id} "
        f"players={result.player_rows:,} teams={result.team_rows:,} "
        f"quotes={result.quote_rows:,} sha256={result.source_sha256[:12]}…"
    )


@stats_app.command("ingest-date")
def stats_ingest_date(
    game_date: Annotated[str, typer.Argument(help="WNBA calendar date (YYYY-MM-DD).")],
    force: Annotated[bool, typer.Option(help="Re-fetch and append official corrections.")] = False,
) -> None:
    """Ingest complete ESPN final box scores for one WNBA calendar date."""
    result = ingest_espn_date(_iso_date(game_date), force=force)
    state = "skipped" if result.skipped else "complete"
    console.print(
        f"[green]{state}[/green] date={result.game_date} games={result.games} "
        f"player_lines={result.player_lines}"
    )


@stats_app.command("backfill")
def stats_backfill(
    start: Annotated[str, typer.Option(help="First date, YYYY-MM-DD.")],
    end: Annotated[str, typer.Option(help="Last date, YYYY-MM-DD.")],
    force: Annotated[bool, typer.Option(help="Re-fetch dates already marked complete.")] = False,
) -> None:
    """Backfill ESPN final box scores, resumably and with polite request pacing."""
    results = backfill_espn(_iso_date(start), _iso_date(end), force=force)
    games = sum(result.games for result in results)
    rows = sum(result.player_lines for result in results)
    skipped = sum(result.skipped for result in results)
    console.print(
        f"[green]backfill complete[/green] dates={len(results)} skipped={skipped} "
        f"games={games} player_lines={rows}"
    )


@lines_app.command("poll")
def lines_poll(
    quiet: Annotated[bool, typer.Option("--quiet", "-q", help="One line of output.")] = False,
) -> None:
    """Poll the market once and append every quote to the archive.

    Record-only: this forecasts nothing and recommends nothing. It exists so that a year from
    now there is a market history to evaluate against -- the one thing that cannot be bought
    retroactively at any price.
    """
    result = run_archiver()

    if quiet:
        console.print(result.summary())
    else:
        console.print(f"[bold]{result.summary()}[/bold]")
        if result.rejects:
            console.print("[yellow]rejected rows (quarantined):[/yellow]")
            for reject in result.rejects[:10]:
                console.print(f"  - {reject}")

    if not result.ok:
        console.print(f"[red]{result.error}[/red]")
        sys.exit(1)


@lines_app.command("coverage")
def lines_coverage() -> None:
    """What the archive currently holds. The honest picture of our evaluation runway."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT source,
                   count(*)                                  AS snapshots,
                   count(DISTINCT player_id)                 AS players,
                   count(DISTINCT prop_type)                 AS markets,
                   min(system_from)                          AS first_seen,
                   max(system_from)                          AS last_seen,
                   count(*) FILTER (WHERE over_american_odds IS NOT NULL
                                      AND under_american_odds IS NOT NULL) AS devigable
            FROM wnba.prop_quotes GROUP BY source ORDER BY snapshots DESC
        """)
        rows = cur.fetchall()

    if not rows:
        console.print("[yellow]archive is empty -- run `wnba lines poll`[/yellow]")
        return

    table = Table(title="market archive coverage")
    for column in (
        "source",
        "snapshots",
        "players",
        "markets",
        "devigable",
        "first seen",
        "last seen",
    ):
        table.add_column(column, justify="right" if column != "source" else "left")
    for r in rows:
        table.add_row(
            str(r["source"]),
            f"{r['snapshots']:,}",
            str(r["players"]),
            str(r["markets"]),
            f"{r['devigable']:,}",
            str(r["first_seen"])[:19],
            str(r["last_seen"])[:19],
        )
    console.print(table)


@forecast_app.command("run")
def forecast_run() -> None:
    """Run the transparent baseline challenger against the current board."""
    result = run_baseline()
    console.print(
        f"[green]forecast complete[/green] run={result.model_run_id} "
        f"forecasts={result.forecasts} paper_episodes={result.episodes} skipped={result.skipped}"
    )


@identity_app.command("approve-exact")
def identity_approve_exact(
    verified_by: Annotated[str, typer.Option(help="Named reviewer recorded in the audit trail.")],
) -> None:
    """Approve only unique exact normalized-name pairs; fuzzy matches remain blocked."""
    result = approve_unique_exact_names(verified_by=verified_by)
    console.print(
        f"[green]identity review complete[/green] approved={result.approved} "
        f"ambiguous_blocked={result.ambiguous}"
    )


@learning_app.command("settle")
def learning_settle() -> None:
    """Settle eligible paper episodes from canonical final box scores."""
    result = settle_paper_episodes()
    console.print(
        f"[green]settlement complete[/green] settled={result.settled} "
        f"voided={result.voided} pushed={result.pushed} unsupported={result.unsupported}"
    )


@injuries_app.command("poll")
def injuries_poll() -> None:
    """Fetch and ingest the latest official WNBA injury-report PDF."""
    result = ingest_official_injuries()
    console.print(
        f"[green]official injuries ingested[/green] parsed={result.parsed} "
        f"inserted={result.inserted} unchanged={result.unchanged} "
        f"unresolved={result.unresolved}"
    )


@roles_app.command("run")
def roles_run() -> None:
    """Refresh transparent role and minutes distributions for the current board."""
    result = project_current_roles()
    console.print(
        f"[green]role projection complete[/green] projected={result.projected} "
        f"unchanged={result.unchanged} skipped={result.skipped}"
    )


@effects_app.command("run")
def effects_run() -> None:
    """Refresh historical teammate-absence effects for current markets."""
    result = project_teammate_effects()
    console.print(
        f"[green]teammate effects complete[/green] projected={result.projected} "
        f"unchanged={result.unchanged} insufficient={result.insufficient}"
    )


@matchups_app.command("run")
def matchups_run() -> None:
    """Refresh point-in-time game and opponent context for the current board."""
    result = project_matchup_contexts()
    console.print(
        f"[green]matchup context complete[/green] projected={result.projected} "
        f"unchanged={result.unchanged} skipped={result.skipped}"
    )


if __name__ == "__main__":
    app()
