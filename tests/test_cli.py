from click.testing import CliRunner
from src.cli import cli, SOURCE_REGISTRY


runner = CliRunner()


def test_cli_help():
    """CLI --help should show available commands."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "run" in result.output
    assert "dashboard" in result.output
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


def test_source_registry_has_24_sources():
    """SOURCE_REGISTRY should have all 24 sources."""
    assert len(SOURCE_REGISTRY) == 24
    expected = {"reed", "adzuna", "jsearch", "arbeitnow", "remoteok",
                "jobicy", "himalayas", "greenhouse", "lever", "workable",
                "ashby", "findajob", "remotive", "jooble", "linkedin",
                "smartrecruiters", "pinpoint", "recruitee", "indeed", "glassdoor",
                "workday", "google_jobs", "devitjobs", "landingjobs"}
    assert set(SOURCE_REGISTRY.keys()) == expected


def test_run_help_shows_new_flags():
    """run --help should show --no-email and --dashboard flags."""
    result = runner.invoke(cli, ["run", "--help"])
    assert result.exit_code == 0
    assert "--no-email" in result.output
    assert "--dashboard" in result.output


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
