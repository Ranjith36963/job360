"""Job360 CLI — command-line interface for the job search system."""

import asyncio
import subprocess
import sys

import click

from src.main import run_search, SOURCE_REGISTRY
from src.__version__ import __version__


@click.group()
@click.version_option(version=__version__, prog_name="job360")
def cli():
    """Job360 — Automated UK job search aggregator."""


def _validate_db_path(ctx, param, value):
    """Validate --db-path to prevent path traversal and arbitrary system paths."""
    if value is None:
        return value
    from pathlib import Path
    p = Path(value).resolve()
    # Must be under the project data/ directory or current working directory
    cwd = Path.cwd().resolve()
    data_dir = (Path(__file__).resolve().parent.parent / "data").resolve()
    if not (str(p).startswith(str(data_dir)) or str(p).startswith(str(cwd))):
        raise click.BadParameter(
            f"Database path must be under the project data/ directory or current working directory. Got: {value}"
        )
    if not p.suffix == ".db":
        raise click.BadParameter("Database file must have .db extension.")
    return str(p)


@cli.command()
@click.option("--source", type=click.Choice(sorted(SOURCE_REGISTRY.keys()), case_sensitive=False),
              default=None, help="Run a single source only.")
@click.option("--dry-run", is_flag=True, help="Fetch and score jobs without saving to DB or sending notifications.")
@click.option("--log-level", type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
              default="INFO", help="Set logging verbosity.")
@click.option("--db-path", default=None, callback=_validate_db_path, help="Override database file path.")
@click.option("--no-email", is_flag=True, help="Skip all notifications (email, Slack, Discord).")
@click.option("--dashboard", is_flag=True, help="Launch Streamlit dashboard after the run.")
def run(source, dry_run, log_level, db_path, no_email, dashboard):
    """Run the job search pipeline."""
    try:
        stats = asyncio.run(run_search(
            db_path=db_path,
            source_filter=source,
            dry_run=dry_run,
            log_level=log_level,
            no_notify=no_email,
            launch_dashboard=dashboard,
        ))
        click.echo(f"Done: {stats['total_found']} found, {stats['new_jobs']} new, {stats['sources_queried']} sources.")
    except KeyboardInterrupt:
        click.echo("\nJob360: Search interrupted. Exiting gracefully.")
        raise SystemExit(130)


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


@cli.command()
@click.option("--stage", default=None,
              type=click.Choice(["applied", "outreach_week1", "outreach_week2", "outreach_week3",
                                 "interview", "offer", "rejected", "withdrawn"], case_sensitive=False),
              help="Filter by pipeline stage.")
@click.option("--reminders", is_flag=True, help="Show only applications with due reminders.")
def pipeline(stage, reminders):
    """View your application pipeline status."""
    import sqlite3
    from src.config.settings import DB_PATH
    from src.pipeline.reminders import format_reminder_message

    if not DB_PATH.exists():
        click.echo("No database found. Run 'job360 run' first.")
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if reminders:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            rows = conn.execute(
                "SELECT a.*, j.title, j.company FROM applications a "
                "JOIN jobs j ON a.job_id = j.id "
                "WHERE a.next_reminder IS NOT NULL AND a.next_reminder <= ? "
                "ORDER BY a.next_reminder",
                (now,),
            ).fetchall()
            if not rows:
                click.echo("No due reminders.")
                return
            click.echo(f"Due reminders ({len(rows)}):")
            for row in rows:
                row_dict = dict(row)
                click.echo(f"  {row_dict['title']} @ {row_dict['company']} — {format_reminder_message(row_dict)}")
            return

        query = "SELECT a.*, j.title, j.company FROM applications a JOIN jobs j ON a.job_id = j.id"
        params = []
        if stage:
            query += " WHERE a.status = ?"
            params.append(stage)
        query += " ORDER BY a.last_updated DESC"

        rows = conn.execute(query, params).fetchall()
        if not rows:
            click.echo("No applications found.")
            return

        # Group by stage
        stages = {}
        for row in rows:
            row_dict = dict(row)
            s = row_dict["status"]
            stages.setdefault(s, []).append(row_dict)

        for s, apps in stages.items():
            click.echo(f"\n{s.upper()} ({len(apps)})")
            click.echo("-" * 40)
            for app in apps:
                reminder = f" [Reminder: {app['next_reminder'][:10]}]" if app.get("next_reminder") else ""
                click.echo(f"  {app['title']} @ {app['company']}{reminder}")
    finally:
        conn.close()


@cli.command("setup-profile")
@click.option("--cv", "cv_path", default=None, type=click.Path(exists=True),
              help="Path to CV file (PDF or DOCX).")
@click.option("--linkedin", "linkedin_path", default=None, type=click.Path(exists=True),
              help="Path to LinkedIn data export ZIP.")
@click.option("--github", "github_username", default=None,
              help="GitHub username to fetch public repos.")
def setup_profile(cv_path, linkedin_path, github_username):
    """Set up your user profile for personalised job search."""
    from src.profile.models import CVData, UserPreferences, UserProfile
    from src.profile.cv_parser import parse_cv
    from src.profile.preferences import merge_cv_and_preferences
    from src.profile.storage import save_profile, profile_exists

    click.echo("Job360 Profile Setup")
    click.echo("=" * 40)

    # Parse CV if provided
    cv_data = CVData()
    if cv_path:
        click.echo(f"Parsing CV: {cv_path}")
        try:
            cv_data = parse_cv(cv_path)
        except Exception as e:
            click.echo(f"  Warning: could not parse CV ({e}). Continuing without CV data.")
            cv_data = CVData()
        if cv_data.skills:
            click.echo(f"  Found {len(cv_data.skills)} skills: {', '.join(cv_data.skills[:10])}...")
        if cv_data.job_titles:
            click.echo(f"  Found {len(cv_data.job_titles)} job titles: {', '.join(cv_data.job_titles[:5])}")
    else:
        click.echo("No CV provided. You can add one later via the dashboard.")

    # Parse LinkedIn export if provided
    if linkedin_path:
        from src.profile.linkedin_parser import parse_linkedin_zip, enrich_cv_from_linkedin
        click.echo(f"\nParsing LinkedIn export: {linkedin_path}")
        linkedin_data = parse_linkedin_zip(linkedin_path)
        cv_data = enrich_cv_from_linkedin(cv_data, linkedin_data)
        n_skills = len(linkedin_data.get("skills", []))
        n_positions = len(linkedin_data.get("positions", []))
        click.echo(f"  LinkedIn: {n_skills} skills, {n_positions} positions")

    # Fetch GitHub data if username provided
    if github_username:
        from src.profile.github_enricher import fetch_github_profile, enrich_cv_from_github
        click.echo(f"\nFetching GitHub repos for: {github_username}")
        github_data = asyncio.run(fetch_github_profile(github_username))
        cv_data = enrich_cv_from_github(cv_data, github_data)
        n_repos = len(github_data.get("repositories", []))
        n_skills = len(github_data.get("skills_inferred", []))
        click.echo(f"  GitHub: {n_repos} repos, {n_skills} skills inferred")

    # Interactive prompts
    titles_input = click.prompt(
        "\nTarget job titles (comma-separated)",
        default="", show_default=False,
    )
    skills_input = click.prompt(
        "Additional skills (comma-separated)",
        default="", show_default=False,
    )
    locations_input = click.prompt(
        "Preferred locations (comma-separated)",
        default="London, Remote", show_default=True,
    )
    arrangement = click.prompt(
        "Work arrangement",
        type=click.Choice(["", "remote", "hybrid", "onsite"], case_sensitive=False),
        default="",
    )
    salary_min = click.prompt("Minimum salary (GBP, 0 to skip)", type=int, default=0)
    salary_max = click.prompt("Maximum salary (GBP, 0 to skip)", type=int, default=0)
    negatives_input = click.prompt(
        "Negative keywords to exclude from titles (comma-separated)",
        default="", show_default=False,
    )

    prefs = UserPreferences(
        target_job_titles=[t.strip() for t in titles_input.split(",") if t.strip()],
        additional_skills=[s.strip() for s in skills_input.split(",") if s.strip()],
        preferred_locations=[l.strip() for l in locations_input.split(",") if l.strip()],
        work_arrangement=arrangement,
        salary_min=salary_min if salary_min > 0 else None,
        salary_max=salary_max if salary_max > 0 else None,
        negative_keywords=[n.strip() for n in negatives_input.split(",") if n.strip()],
    )

    if cv_data.skills or cv_data.job_titles:
        prefs = merge_cv_and_preferences(cv_data.skills, cv_data.job_titles, prefs)

    profile = UserProfile(cv_data=cv_data, preferences=prefs)
    path = save_profile(profile)
    click.echo(f"\nProfile saved to {path}")
    click.echo("Next pipeline run will use your personalised keywords.")


if __name__ == "__main__":
    cli()
