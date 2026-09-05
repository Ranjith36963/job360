"""Job360 CLI — the two commands the product still has.

Slice 5 (#483) deleted `run`, `status`, `view` and `sources`: there is no
search pipeline to run, no run log to report and no feed to view. What is
left is the server and the single-tenant profile setup.
"""

import asyncio
from typing import Optional

import click


@click.group()
@click.version_option(version="1.0.0", prog_name="job360")
def cli() -> None:
    """Job360 — the memory and context layer for your job-search agent."""


@cli.command()
@click.option("--port", default=8000, help="Port to run the API server on.")
@click.option("--host", default="127.0.0.1", help="Host to bind to.")
def api(port: int, host: str) -> None:
    """Start the FastAPI backend server."""
    import uvicorn
    click.echo(f"Starting Job360 API on {host}:{port}")
    uvicorn.run("src.api.main:app", host=host, port=port, reload=True)


@cli.command("setup-profile")
@click.option("--cv", "cv_path", default=None, type=click.Path(exists=True),
              help="Path to CV file (PDF or DOCX).")
@click.option("--linkedin", "linkedin_path", default=None, type=click.Path(exists=True),
              help="Path to LinkedIn profile PDF (profile page → More → Save to PDF).")
@click.option("--github", "github_username", default=None,
              help="GitHub username to fetch public repos.")
def setup_profile(
    cv_path: Optional[str],
    linkedin_path: Optional[str],
    github_username: Optional[str],
) -> None:
    """Set up your user profile for personalised job search.

    The CLI is single-tenant by design — every ``python -m src.cli``
    invocation writes to ``DEFAULT_TENANT_ID``. Per-user profiles are
    the HTTP API's job (``/api/profile`` with a session cookie). No
    ``--user-id`` flag; that's scope creep. Batch 3.5.2 intentionally
    leaves this contract in place (Deliverable E).
    """
    from src.core.tenancy import DEFAULT_TENANT_ID
    from src.services.profile.cv_parser import parse_cv
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.preferences import merge_cv_and_preferences
    from src.services.profile.storage import save_profile

    click.echo("Job360 Profile Setup")
    click.echo("=" * 40)

    # Parse CV if provided
    cv_data = CVData()
    if cv_path:
        click.echo(f"Parsing CV: {cv_path}")
        try:
            cv_data = parse_cv(cv_path)
        except RuntimeError as e:
            click.echo(f"Error: CV analysis failed — {e}", err=True)
            return
        except Exception as e:
            click.echo(f"  Warning: could not parse CV ({e}). Continuing without CV data.")
            cv_data = CVData()
        if cv_data.skills:
            click.echo(f"  Found {len(cv_data.skills)} skills: {', '.join(cv_data.skills[:10])}...")
        if cv_data.job_titles:
            click.echo(f"  Found {len(cv_data.job_titles)} job titles: {', '.join(cv_data.job_titles[:5])}")
    else:
        click.echo("No CV provided. You can add one later via the frontend at http://localhost:3000/profile.")

    # Parse LinkedIn PDF if provided
    if linkedin_path:
        from src.services.profile.linkedin_parser import enrich_cv_from_linkedin, parse_linkedin_pdf
        click.echo(f"\nParsing LinkedIn PDF: {linkedin_path}")
        linkedin_data = parse_linkedin_pdf(linkedin_path)
        cv_data = enrich_cv_from_linkedin(cv_data, linkedin_data)
        n_skills = len(linkedin_data.get("skills", []))
        n_positions = len(linkedin_data.get("positions", []))
        if not (n_skills or n_positions):
            click.echo("  Warning: no LinkedIn data extracted. Confirm the file is a profile PDF (not a CV).")
        else:
            click.echo(f"  LinkedIn: {n_skills} skills, {n_positions} positions")

    # Fetch GitHub data if username provided
    if github_username:
        from src.services.profile.github_enricher import enrich_cv_from_github, fetch_github_profile
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
        preferred_locations=[loc.strip() for loc in locations_input.split(",") if loc.strip()],
        work_arrangement=arrangement,
        salary_min=salary_min if salary_min > 0 else None,
        salary_max=salary_max if salary_max > 0 else None,
        negative_keywords=[n.strip() for n in negatives_input.split(",") if n.strip()],
        github_username=github_username or "",
    )

    if cv_data.skills or cv_data.job_titles:
        prefs = merge_cv_and_preferences(cv_data.skills, cv_data.job_titles, prefs)

    profile = UserProfile(cv_data=cv_data, preferences=prefs)
    save_profile(profile, DEFAULT_TENANT_ID)
    click.echo("\nProfile saved to user_profiles table (DEFAULT_TENANT_ID).")
    click.echo("Your agent can read it over MCP, or the web app at /profile.")


if __name__ == "__main__":
    cli()
