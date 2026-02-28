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


def test_source_registry_has_12_sources():
    """SOURCE_REGISTRY should have all 12 sources."""
    assert len(SOURCE_REGISTRY) == 12
    expected = {"reed", "adzuna", "jsearch", "arbeitnow", "remoteok",
                "jobicy", "himalayas", "greenhouse", "lever", "workable",
                "ashby", "findajob"}
    assert set(SOURCE_REGISTRY.keys()) == expected
