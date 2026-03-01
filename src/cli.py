"""Job360 CLI — command-line interface for the job search system."""

import asyncio
import subprocess
import sys

import click

from src.main import run_search, SOURCE_REGISTRY


@click.group()
@click.version_option(version="1.0.0", prog_name="job360")
def cli():
    """Job360 — Automated UK AI/ML job search aggregator."""


@cli.command()
@click.option("--source", type=click.Choice(sorted(SOURCE_REGISTRY.keys()), case_sensitive=False),
              default=None, help="Run a single source only.")
@click.option("--dry-run", is_flag=True, help="Fetch and score jobs without saving to DB or sending notifications.")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
              default="INFO", help="Set logging verbosity.")
@click.option("--db-path", default=None, help="Override database file path.")
@click.option("--no-email", is_flag=True, help="Skip all notifications (email, Slack, Discord).")
@click.option("--dashboard", is_flag=True, help="Launch Streamlit dashboard after the run.")
def run(source, dry_run, log_level, db_path, no_email, dashboard):
    """Run the job search pipeline."""
    stats = asyncio.run(run_search(
        db_path=db_path,
        source_filter=source,
        dry_run=dry_run,
        log_level=log_level,
        no_notify=no_email,
        launch_dashboard=dashboard,
    ))
    click.echo(f"Done: {stats['total_found']} found, {stats['new_jobs']} new, {stats['sources_queried']} sources.")


@cli.command()
def dashboard():
    """Launch the Streamlit web dashboard."""
    click.echo("Starting Job360 Dashboard...")
    subprocess.run([sys.executable, "-m", "streamlit", "run", "src/dashboard.py"], check=True)


@cli.command()
def status():
    """Show the last run stats from the database."""
    import sqlite3
    from src.config.settings import DB_PATH

    if not DB_PATH.exists():
        click.echo("No database found. Run 'job360 run' first.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM run_log ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            click.echo("No runs recorded yet.")
            return
        click.echo(f"Last run: {row['timestamp']}")
        click.echo(f"  Total found: {row['total_found']}")
        click.echo(f"  New jobs:    {row['new_jobs']}")

        total_jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        click.echo(f"  Total in DB: {total_jobs}")
    finally:
        conn.close()


@cli.command()
@click.option("--hours", default=168, type=int, help="Show jobs from last N hours (default 168 = 7 days).")
@click.option("--min-score", default=30, type=int, help="Minimum match score to display.")
@click.option("--source", default=None, help="Filter by source name.")
@click.option("--visa-only", is_flag=True, help="Show only visa-flagged jobs.")
@click.option("--db-path", default=None, help="Override database file path.")
def view(hours, min_score, source, visa_only, db_path):
    """View jobs in a time-bucketed Rich terminal table."""
    from src.cli_view import display_jobs
    display_jobs(hours=hours, min_score=min_score, source=source,
                 visa_only=visa_only, db_path=db_path)


@cli.command("sources")
def list_sources():
    """List all available job sources."""
    click.echo("Available sources:")
    for name in sorted(SOURCE_REGISTRY.keys()):
        click.echo(f"  - {name}")


if __name__ == "__main__":
    cli()
