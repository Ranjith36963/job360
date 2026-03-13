"""Job360 CLI — command-line interface for the job search system."""

import asyncio
import subprocess
import sys

import click

from src.main import run_search, SOURCE_REGISTRY


@click.group()
@click.version_option(version="1.0.0", prog_name="job360")
def cli():
    """Job360 — Automated job search aggregator powered by your CV."""


@cli.command()
@click.option("--source", type=click.Choice(sorted(SOURCE_REGISTRY.keys()), case_sensitive=False),
              default=None, help="Run a single source only.")
@click.option("--dry-run", is_flag=True, help="Fetch and score jobs without saving to DB or sending notifications.")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
              default="INFO", help="Set logging verbosity.")
@click.option("--db-path", default=None, help="Override database file path.")
def run(source, dry_run, log_level, db_path):
    """Run the job search pipeline."""
    stats = asyncio.run(run_search(
        db_path=db_path,
        source_filter=source,
        dry_run=dry_run,
        log_level=log_level,
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


@cli.command("upload-cv")
@click.argument("cv_path", type=click.Path(exists=True))
def upload_cv(cv_path):
    """Upload a CV (PDF/DOCX) and extract your skills profile."""
    from pathlib import Path
    from src.cv_parser import extract_text, extract_profile, save_profile

    click.echo(f"Reading CV: {cv_path}")
    try:
        text = extract_text(cv_path)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1)

    if not text.strip():
        click.echo("Warning: No text could be extracted from the file.", err=True)
        raise SystemExit(1)

    click.echo(f"Extracted {len(text)} characters of text.")

    profile = extract_profile(text)
    profile["source_file"] = Path(cv_path).name

    click.echo("\nExtracted Profile:")
    click.echo(f"  Job titles:       {len(profile['job_titles'])} matched")
    click.echo(f"  Primary skills:   {len(profile['primary_skills'])} matched")
    click.echo(f"  Secondary skills: {len(profile['secondary_skills'])} matched")
    click.echo(f"  Tertiary skills:  {len(profile['tertiary_skills'])} matched")
    click.echo(f"  Locations:        {len(profile['locations'])} matched")

    if profile["primary_skills"]:
        click.echo(f"\n  Top skills: {', '.join(profile['primary_skills'])}")

    save_path = save_profile(profile)
    click.echo(f"\nProfile saved to: {save_path}")
    click.echo("Next 'job360 run' will use this profile for scoring.")


@cli.command("sources")
def list_sources():
    """List all available job sources."""
    click.echo("Available sources:")
    for name in sorted(SOURCE_REGISTRY.keys()):
        click.echo(f"  - {name}")


if __name__ == "__main__":
    cli()
