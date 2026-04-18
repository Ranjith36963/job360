from unittest.mock import patch, MagicMock

from click.testing import CliRunner
from src.cli import cli, SOURCE_REGISTRY


runner = CliRunner()


def test_cli_help():
    """CLI --help should show available commands."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "status" in result.output
    assert "sources" in result.output


def test_cli_version():
    """CLI --version should show version."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "1.0.0" in result.output


def test_run_help():
    """run --help should show source, dry-run, log-level options."""
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--source" in result.output
    assert "--dry-run" in result.output
    assert "--log-level" in result.output
    assert "--db-path" in result.output


def test_sources_command():
    """sources command should list all 12 sources."""
    result = runner.invoke(cli, ["sources"])
    assert result.exit_code == 0
    for name in SOURCE_REGISTRY:
        assert name in result.output


def test_source_registry_has_48_sources():
    """SOURCE_REGISTRY should have all 48 sources."""
    assert len(SOURCE_REGISTRY) == 48
    expected = {"reed", "adzuna", "jsearch", "arbeitnow", "remoteok",
                "jobicy", "himalayas", "greenhouse", "lever", "workable",
                "ashby", "findajob", "remotive", "jooble", "linkedin",
                "smartrecruiters", "pinpoint", "recruitee", "indeed", "glassdoor",
                "workday", "google_jobs", "devitjobs", "landingjobs",
                "aijobs", "themuse", "hackernews", "careerjet", "findwork",
                "nofluffjobs",
                # Phase 4 new sources
                "hn_jobs", "yc_companies", "jobs_ac_uk", "nhs_jobs",
                "personio", "workanywhere", "weworkremotely", "realworkfromanywhere",
                "biospace", "jobtensor", "climatebase", "eightykhours",
                "bcs_jobs", "uni_jobs", "successfactors", "aijobs_global",
                "aijobs_ai", "nomis"}
    assert set(SOURCE_REGISTRY.keys()) == expected


def test_run_help_shows_new_flags():
    """run --help should show --no-email flag."""
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--no-email" in result.output


def test_view_help():
    """view --help should show all expected options."""
    result = runner.invoke(cli, ["view", "--help"])
    assert result.exit_code == 0
    assert "--hours" in result.output
    assert "--min-score" in result.output
    assert "--source" in result.output
    assert "--visa-only" in result.output
    assert "--db-path" in result.output


def test_cli_help_shows_view():
    """CLI --help should list the view command."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "view" in result.output


def test_setup_profile_help():
    """setup-profile --help should show --cv, --linkedin, --github options."""
    result = runner.invoke(cli, ["setup-profile", "--help"])
    assert result.exit_code == 0
    assert "--cv" in result.output
    assert "--linkedin" in result.output
    assert "--github" in result.output


def test_setup_profile_preserves_github_username(tmp_path):
    """BUG-1 regression: github_username must survive merge in CLI flow."""
    from src.services.profile.models import CVData, UserPreferences
    from src.services.profile.preferences import merge_cv_and_preferences

    prefs = UserPreferences(
        target_job_titles=["Engineer"],
        additional_skills=["Python"],
        github_username="myuser",
    )
    merged = merge_cv_and_preferences(["SQL"], ["Data Analyst"], prefs)
    assert merged.github_username == "myuser"


def test_setup_profile_corrupt_cv(tmp_path):
    """BUG-5 regression: corrupt CV should not crash the CLI."""
    bad_cv = tmp_path / "bad.pdf"
    bad_cv.write_bytes(b"not a pdf")
    result = runner.invoke(cli, ["setup-profile", "--cv", str(bad_cv)], input="\n\n\n\n0\n0\n\n")
    # Should not crash, but continue with warning
    assert result.exit_code == 0 or "Warning" in result.output or "could not parse" in result.output
