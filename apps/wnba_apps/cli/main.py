"""The `wnba` command line.

The primary interface for Phase 0/1. Deliberately boring: poll, migrate, report.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table
from wnba_services.feature_engine.matchup import project_matchup_contexts
from wnba_services.feature_engine.roles import project_current_roles
from wnba_services.feature_engine.teammate_effects import project_teammate_effects
from wnba_services.forecasting.backtest import run_walk_forward_backtest
from wnba_services.forecasting.baseline import run_baseline
from wnba_services.forecasting.challengers import challenger_names
from wnba_services.forecasting.fitting import fit_model_parameters
from wnba_services.ingestion.archiver import run_archiver
from wnba_services.ingestion.espn import backfill_espn, ingest_espn_date
from wnba_services.ingestion.historical_markets import normalize_historical_markets
from wnba_services.ingestion.identity import approve_unique_exact_names
from wnba_services.ingestion.legacy import import_legacy_sqlite
from wnba_services.ingestion.wnba_injuries import ingest_official_injuries
from wnba_services.learning_loop.analyst_expertise import (
    score_analyst_expertise,
    unlabelled_settled_episodes,
)
from wnba_services.learning_loop.evaluation import evaluate_models
from wnba_services.learning_loop.experiments import (
    MINIMUM_INDEPENDENT_SAMPLE,
    abandon_experiment,
    evaluate_experiments,
    list_experiments,
    open_experiment,
    promote_challenger,
    rollback_promotion,
)
from wnba_services.learning_loop.proposals import generate_research_proposals
from wnba_services.learning_loop.readiness import evaluate_readiness
from wnba_services.learning_loop.rule_authoring import author_rules_from_findings
from wnba_services.learning_loop.rule_lifecycle import approve_rule, retire_rule, run_rule_backtests
from wnba_services.learning_loop.rule_proposals import propose_from_measured_errors
from wnba_services.learning_loop.settlement import settle_paper_episodes
from wnba_services.monitoring.liveness import run_liveness_checks
from wnba_services.research_agents.coordinator import pending_plans, plan_investigations
from wnba_services.research_agents.credibility import score_research_credibility
from wnba_services.research_agents.workflow import (
    execute_plan,
    run_projection_research,
    run_queue,
)
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
backtest_app = typer.Typer(help="Point-in-time rolling-origin evaluation.", no_args_is_help=True)
identity_app = typer.Typer(help="Audited cross-source identity review.", no_args_is_help=True)
learning_app = typer.Typer(
    help="Paper settlement, scoring, and learning loop.", no_args_is_help=True
)
experiments_app = typer.Typer(
    help="Champion/challenger experiments. Promotion is a named human act.", no_args_is_help=True
)
injuries_app = typer.Typer(help="Official bitemporal WNBA injury reports.", no_args_is_help=True)
roles_app = typer.Typer(help="Projected availability, starts, and minutes.", no_args_is_help=True)
effects_app = typer.Typer(help="Shrunk teammate-absence role effects.", no_args_is_help=True)
matchups_app = typer.Typer(help="Pace, defense, rest, and blowout context.", no_args_is_help=True)
research_app = typer.Typer(help="Evidence-grounded DeepSeek research.", no_args_is_help=True)
monitor_app = typer.Typer(
    help="Pipeline liveness: did the work actually happen?", no_args_is_help=True
)
app.add_typer(lines_app, name="lines")
app.add_typer(db_app, name="db")
app.add_typer(data_app, name="data")
app.add_typer(stats_app, name="stats")
app.add_typer(forecast_app, name="forecast")
app.add_typer(backtest_app, name="backtest")
app.add_typer(identity_app, name="identity")
app.add_typer(learning_app, name="learning")
learning_app.add_typer(experiments_app, name="experiments")
app.add_typer(injuries_app, name="injuries")
app.add_typer(roles_app, name="roles")
app.add_typer(effects_app, name="effects")
app.add_typer(matchups_app, name="matchups")
app.add_typer(research_app, name="research")
app.add_typer(monitor_app, name="monitor")

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


@data_app.command("normalize-historical-markets")
def data_normalize_historical_markets() -> None:
    """Conservatively map rescued sportsbook quotes to canonical players and games."""
    result = normalize_historical_markets()
    console.print(
        f"[green]historical market normalization complete[/green] "
        f"events_resolved={result.events_resolved} ambiguous={result.events_ambiguous} "
        f"unresolved={result.events_unresolved} quotes={result.quotes_normalized} "
        f"unresolved_players={result.players_unresolved}"
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
        f"forecasts={result.forecasts} paper_episodes={result.episodes} "
        f"candidates={result.candidates} rule_firings={result.rule_firings} "
        f"skipped={result.skipped}"
    )


@backtest_app.command("run")
def backtest_run() -> None:
    """Replay timestamped historical lines without look-ahead leakage."""
    result = run_walk_forward_backtest()
    console.print(
        f"[green]backtest complete[/green] run={result.backtest_run_id} "
        f"markets={result.markets} results={result.results} skipped={result.skipped}"
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
    evaluation = evaluate_models()
    # Refitting here rather than in a separate timer: settlement is the only event that adds
    # evidence, so it is the only moment at which the fitted parameters can have changed.
    fitted = fit_model_parameters()
    # Settlement is the only event that creates outcomes, so it is the only moment an override
    # can be scored or an analyst's label can be compared against what actually happened.
    expertise = score_analyst_expertise()
    console.print(
        f"[green]settlement complete[/green] settled={result.settled} "
        f"voided={result.voided} pushed={result.pushed} unsupported={result.unsupported} "
        f"evaluations={evaluation.evaluations} attributions={evaluation.attributions} "
        f"drift_incidents={evaluation.drift_incidents} "
        f"calibration_fitted={fitted.fitted_calibration}/{fitted.calibration_maps} "
        f"weights_fitted={fitted.fitted_weights}/{fitted.weight_sets} "
        f"overrides_evaluated={expertise.overrides_evaluated}"
    )


@learning_app.command("propose-rules")
def learning_propose_rules() -> None:
    """Turn repeated measured errors into candidate rules and hypotheses. Nothing goes live."""
    result = propose_from_measured_errors()
    console.print(
        f"[green]proposals complete[/green] categories={result.categories_reviewed} "
        f"hypotheses={result.hypotheses_created} rules_proposed={result.rules_proposed} "
        f"episodes_linked={result.episodes_linked}"
    )


@learning_app.command("backtest-rules")
def learning_backtest_rules() -> None:
    """Score every proposed rule against the settled record. Never activates anything."""
    result = run_rule_backtests()
    console.print(
        f"[green]rule backtests complete[/green] considered={result.rules_considered} "
        f"backtested={result.backtested} rejected={result.rejected} "
        f"inconclusive={result.inconclusive} episodes={result.episodes}"
    )
    if result.awaiting_approval:
        console.print(
            f"[yellow]{result.awaiting_approval} rule(s) have supporting evidence and await "
            "a named human approver; nothing activates on its own.[/yellow]"
        )


@learning_app.command("approve-rule")
def learning_approve_rule(
    rule_id: Annotated[str, typer.Argument(help="Backtested analyst-rule identifier.")],
    approved_by: Annotated[
        str, typer.Option(help="Named human approver stored in the audit trail.")
    ],
    reason: Annotated[str, typer.Option(help="Why the backtest evidence justifies activation.")],
) -> None:
    """Activate one helpful backtested rule. This is never called by automation."""
    result = approve_rule(rule_id, approved_by=approved_by, reason=reason)
    console.print(f"[green]rule activated[/green] rule={result.rule_id} approved_by={result.actor}")


@learning_app.command("retire-rule")
def learning_retire_rule(
    rule_id: Annotated[str, typer.Argument(help="Active analyst-rule identifier.")],
    retired_by: Annotated[
        str, typer.Option(help="Named human retiring the rule, retained in command logs.")
    ],
    reason: Annotated[str, typer.Option(help="Why the active rule is being retired.")],
) -> None:
    """Immediately retire an active rule; retired rules no longer enter forecasts."""
    result = retire_rule(rule_id, retired_by=retired_by, reason=reason)
    console.print(f"[yellow]rule retired[/yellow] rule={result.rule_id} retired_by={result.actor}")


@learning_app.command("fit")
def learning_fit() -> None:
    """Refit calibration maps, ensemble weights and edge shrinkage from settled episodes."""
    result = fit_model_parameters()
    console.print(
        f"[green]fitting complete[/green] episodes={result.episodes} "
        f"markets={result.independent_markets} games={result.independent_games} "
        f"calibration={result.fitted_calibration}/{result.calibration_maps} "
        f"weights={result.fitted_weights}/{result.weight_sets} "
        f"shrinkage={result.shrinkage_sets}"
    )


@learning_app.command("evaluate")
def learning_evaluate() -> None:
    """Persist calibration, challenger comparisons, errors, and drift diagnostics."""
    result = evaluate_models()
    console.print(
        f"[green]evaluation complete[/green] evaluations={result.evaluations} "
        f"buckets={result.calibration_buckets} attributions={result.attributions} "
        f"drift_incidents={result.drift_incidents}"
    )


@learning_app.command("propose")
def learning_propose() -> None:
    """Generate human-reviewable experiments from repeated avoidable errors."""
    result = generate_research_proposals()
    console.print(
        f"[green]proposal review complete[/green] proposed={result.proposed} "
        f"categories_reviewed={result.categories_reviewed}"
    )


@experiments_app.command("list")
def experiments_list() -> None:
    """Show every champion/challenger experiment and what it has measured so far."""
    for row in list_experiments():
        console.print(
            f"  {row['status']:11} {row['challenger_name'] or '-':20} "
            f"verdict={row['verdict'] or 'pending':22} "
            f"markets={row['independent_sample_size']}/{row['minimum_sample']} "
            f"id={row['experiment_id']}"
        )
    console.print(f"[dim]known challengers: {', '.join(challenger_names())}[/dim]")


@experiments_app.command("open")
def experiments_open(
    challenger: Annotated[str, typer.Argument(help="Challenger model family name.")],
    opened_by: Annotated[str, typer.Option(help="Named human opening the experiment.")],
    primary_metric: Annotated[
        str, typer.Option(help="log_loss, brier, mae or line_value.")
    ] = "log_loss",
    minimum_sample: Annotated[
        int, typer.Option(help="Independent markets required before promotion is considered.")
    ] = MINIMUM_INDEPENDENT_SAMPLE,
) -> None:
    """Start a live shadow comparison. The challenger reaches no recommendation."""
    experiment_id = open_experiment(
        challenger,
        opened_by=opened_by,
        primary_metric=primary_metric,
        minimum_sample=minimum_sample,
    )
    console.print(
        f"[green]experiment open[/green] id={experiment_id} challenger={challenger} "
        f"gate={minimum_sample} independent markets"
    )


@experiments_app.command("evaluate")
def experiments_evaluate() -> None:
    """Score open experiments against settled outcomes. Promotes nothing."""
    evaluations = evaluate_experiments()
    if not evaluations:
        console.print("[yellow]no open experiments[/yellow]")
        return
    for result in evaluations:
        champion = result.champion.log_loss
        challenger = result.challenger.log_loss
        console.print(
            f"  {result.challenger_name:20} verdict={result.verdict:22} "
            f"markets={result.independent_sample_size} "
            f"champion_log_loss={'-' if champion is None else f'{champion:.4f}'} "
            f"challenger_log_loss={'-' if challenger is None else f'{challenger:.4f}'}"
        )
        for group in result.subgroups:
            if group["degraded"]:
                console.print(
                    f"    [red]degraded[/red] {group['dimension']}={group['value']} "
                    f"n={group['sample_size']} gain={group['log_loss_gain']}"
                )
    console.print("[dim]promotion requires `wnba learning experiments promote`.[/dim]")


@experiments_app.command("promote")
def experiments_promote(
    experiment_id: Annotated[str, typer.Argument(help="Evaluated experiment UUID.")],
    approved_by: Annotated[str, typer.Option(help="Named human approver, stored permanently.")],
    reason: Annotated[str, typer.Option(help="Why the evidence justifies replacing the champion.")],
) -> None:
    """Replace the champion with a challenger. Never called by automation."""
    result = promote_challenger(UUID(experiment_id), approved_by=approved_by, reason=reason)
    console.print(
        f"[green]challenger promoted[/green] {result.challenger_name} "
        f"approved_by={result.actor} at={result.changed_at.isoformat()}"
    )


@experiments_app.command("rollback")
def experiments_rollback(
    experiment_id: Annotated[str, typer.Argument(help="Promoted experiment UUID.")],
    rolled_back_by: Annotated[str, typer.Option(help="Named human rolling the promotion back.")],
    reason: Annotated[str, typer.Option(help="Why the promoted challenger is being withdrawn.")],
) -> None:
    """Restore the previous champion, keeping the promotion in the record."""
    result = rollback_promotion(UUID(experiment_id), rolled_back_by=rolled_back_by, reason=reason)
    console.print(
        f"[yellow]promotion rolled back[/yellow] {result.challenger_name} by={result.actor}"
    )


@experiments_app.command("abandon")
def experiments_abandon(
    experiment_id: Annotated[str, typer.Argument(help="Running experiment UUID.")],
    actor: Annotated[str, typer.Option(help="Named human closing the experiment.")],
    reason: Annotated[str, typer.Option(help="Why the experiment is being stopped.")],
) -> None:
    """Stop collecting shadow predictions without reaching a verdict."""
    result = abandon_experiment(UUID(experiment_id), actor=actor, reason=reason)
    console.print(
        f"[yellow]experiment abandoned[/yellow] {result.challenger_name} by={result.actor}"
    )


@learning_app.command("score-expertise")
def learning_score_expertise() -> None:
    """Evaluate settled overrides and refresh analyst expertise by domain. Gates nothing."""
    result = score_analyst_expertise()
    console.print(
        f"[green]expertise scored[/green] overrides_evaluated={result.overrides_evaluated} "
        f"domains={result.domains_scored} labels={result.labels_scored} "
        f"analysts={result.analysts}"
    )


@learning_app.command("label-queue")
def learning_label_queue(
    limit: Annotated[int, typer.Option(help="Maximum unlabelled decisions to show.")] = 20,
) -> None:
    """Settled decisions nobody has labelled yet, candidates first."""
    with connect() as conn, conn.cursor() as cur:
        rows = unlabelled_settled_episodes(cur, limit=limit)
    if not rows:
        console.print("[green]every settled decision has been labelled[/green]")
        return
    for row in rows:
        landed = "hit " if row["hit"] else "miss"
        console.print(
            f"  {landed} {row['system_recommendation']:11} {row['full_name']:22} "
            f"{row['prop_type']} {row['side']} {row['line']} "
            f"attributed={row['primary_error'] or '-'} episode={row['episode_id']}"
        )
    console.print(
        "[dim]Label these in the console: the weakest assumption and the misleading evidence "
        "are the two fields nothing else can reconstruct.[/dim]"
    )


@learning_app.command("readiness")
def learning_readiness() -> None:
    """Evaluate every real-money gate and fail closed on provisional evidence."""
    result = evaluate_readiness()
    console.print(
        f"[green]readiness evaluated[/green] id={result.evaluation_id} "
        f"overall_ready={result.overall_ready}"
    )
    for gate in result.gates:
        console.print(f"  {gate.status:16} {gate.gate_id}: {gate.detail}")


@research_app.command("run")
def research_run(
    projection_id: Annotated[str, typer.Argument(help="Forecast projection UUID.")],
) -> None:
    """Run cited availability, rotation, matchup, market, and skeptic analyses."""
    try:
        parsed_id = UUID(projection_id)
    except ValueError as exc:
        raise typer.BadParameter("expected a projection UUID") from exc
    result = run_projection_research(parsed_id)
    if result.status == "blocked":
        console.print(
            "[yellow]data audit blocked the investigation[/yellow] "
            f"findings={', '.join(result.blocked_by)}"
        )
        return
    console.print(
        f"[green]research complete[/green] run={result.research_run_id} "
        f"analyses={result.analyses} claims={result.claims} evidence={result.evidence} "
        f"posture={result.posture}"
    )


@research_app.command("plan")
def research_plan(
    limit: Annotated[int, typer.Option(help="Maximum investigations to queue.")] = 25,
) -> None:
    """Queue investigations for open markets that have changed since they were forecast."""
    result = plan_investigations(limit=limit)
    console.print(
        f"[green]planning complete[/green] considered={result.considered} "
        f"planned={result.planned} expired={result.expired}"
    )
    for plan in result.plans:
        console.print(
            f"  [{plan.priority:3}] {plan.trigger_kind:24} roles={','.join(plan.roles)} "
            f"plan={plan.plan_id}"
        )


@research_app.command("queue")
def research_queue(
    limit: Annotated[int, typer.Option(help="Maximum queued plans to show.")] = 20,
) -> None:
    """Show what the coordinator wants investigated, highest priority first."""
    with connect() as conn, conn.cursor() as cur:
        rows = pending_plans(cur, limit=limit)
    if not rows:
        console.print("[yellow]no investigations queued[/yellow]")
        return
    for row in rows:
        console.print(
            f"  [{row['priority']:3}] {row['trigger_kind']:24} {row['full_name']} "
            f"{row['prop_type']} {row['line']} plan={row['plan_id']}"
        )


@research_app.command("execute")
def research_execute(
    plan_id: Annotated[str, typer.Argument(help="Queued research-plan UUID.")],
) -> None:
    """Audit, investigate in two rounds, and synthesise one queued plan."""
    try:
        parsed_id = UUID(plan_id)
    except ValueError as exc:
        raise typer.BadParameter("expected a plan UUID") from exc
    result = execute_plan(parsed_id)
    if result.status == "blocked":
        console.print(
            "[yellow]data audit blocked the investigation[/yellow] "
            f"findings={', '.join(result.blocked_by)}"
        )
        return
    console.print(
        f"[green]investigation complete[/green] run={result.research_run_id} "
        f"analyses={result.analyses} claims={result.claims} posture={result.posture}"
    )


@research_app.command("run-queue")
def research_run_queue(
    limit: Annotated[int, typer.Option(help="Hard cap on investigations run this pass.")] = 5,
) -> None:
    """Run the highest-priority queued investigations, up to a cap.

    Planning is free and running is not, so the cap is deliberate: a trigger storm should not
    turn one unusual evening into a provider bill.
    """
    result = run_queue(limit=limit)
    console.print(
        f"[green]queue run complete[/green] attempted={result.attempted} "
        f"completed={result.completed} blocked={result.blocked} failed={result.failed} "
        f"remaining={result.remaining}"
    )
    for posture in result.postures:
        console.print(f"  posture={posture}")


@research_app.command("score-credibility")
def research_score_credibility() -> None:
    """Refresh agent credibility, evidence ranking and source reliability from outcomes."""
    result = score_research_credibility()
    console.print(
        f"[green]credibility scored[/green] agents={result.agent_scores} "
        f"evidence_ranked={result.evidence_ranked} sources={result.source_scores} "
        f"claims_scored={result.claims_scored}"
    )


@research_app.command("author-rules")
def research_author_rules(
    limit: Annotated[int, typer.Option(help="Maximum failure patterns to work from.")] = 3,
) -> None:
    """Ask the research director to propose rules. Nothing proposed here can fire."""
    result = author_rules_from_findings(limit=limit)
    console.print(
        f"[green]rule authoring complete[/green] considered={result.considered} "
        f"compiled={result.compiled} proposed={result.proposed} "
        f"rejected_vocabulary={result.rejected_vocabulary} "
        f"rejected_leakage={result.rejected_leakage}"
    )
    for reason in result.reasons:
        console.print(f"  [yellow]{reason}[/yellow]")
    if result.proposed:
        console.print(
            "[dim]Proposed rules require a backtest and then a named human approver who is not "
            "the proposer; the database refuses anything else.[/dim]"
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


@monitor_app.command("liveness")
def monitor_liveness(
    record: Annotated[bool, typer.Option(help="Write findings as data-quality incidents.")] = True,
) -> None:
    """Check that the pipeline produced work, not merely that it ran.

    Exits non-zero when anything is failing, so a scheduler or an OnFailure handler can act
    on it. Every failure this catches was previously invisible: the services involved all
    exited zero while producing nothing.
    """
    report = run_liveness_checks(record=record)
    if report.healthy:
        console.print(f"[green]{report.summary()}[/green]")
        return

    console.print(f"[red]{report.summary()}[/red]")
    for finding in report.findings:
        colour = "red" if finding.check.blocks_recommendations else "yellow"
        console.print(
            f"  [{colour}]{finding.check.severity.value.upper():8}[/{colour}] "
            f"{finding.check.code}: {finding.detail}"
        )
    if report.incidents_written:
        console.print(f"  recorded {report.incidents_written} data-quality incident(s)")
    sys.exit(1)
