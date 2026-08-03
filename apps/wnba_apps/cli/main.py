"""The `wnba` command line.

The primary interface for Phase 0/1. Deliberately boring: poll, migrate, report.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from wnba_services.ingestion.archiver import run_archiver
from wnba_store.db import connect, migrate

app = typer.Typer(
    help="WNBA player-prop intelligence. Analysis only -- never places a wager.",
    no_args_is_help=True,
    add_completion=False,
)
lines_app = typer.Typer(help="Market line archive.", no_args_is_help=True)
db_app = typer.Typer(help="Database schema.", no_args_is_help=True)
app.add_typer(lines_app, name="lines")
app.add_typer(db_app, name="db")

console = Console()


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


if __name__ == "__main__":
    app()
