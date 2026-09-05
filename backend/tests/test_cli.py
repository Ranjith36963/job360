"""The CLI's two surviving commands.

Slice 5 (#483) deleted `run`, `status`, `view`, `sources` and
`rescore-backfill` with the pipeline they drove — and with them the
five-surface source contract (old rule #8) this file used to pin. What is
left is `api` (start the server) and `setup-profile` (single-tenant profile
setup, `DEFAULT_TENANT_ID`).
"""

from click.testing import CliRunner

from src.cli import cli

runner = CliRunner()


def test_cli_help():
    """--help lists exactly the two commands that still exist."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "api" in result.output
    assert "setup-profile" in result.output


def test_the_deleted_commands_are_really_gone():
    """The sourcing-era commands are DELETED, not hidden behind a flag —
    invoking one must be an unknown-command error, not a no-op that looks
    like it worked."""
    for gone in ("run", "status", "view", "sources", "rescore-backfill"):
        result = runner.invoke(cli, [gone, "--help"])
        assert result.exit_code != 0, f"`{gone}` still exists"


def test_cli_version():
    """CLI --version should show version."""
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "1.0.0" in result.output


def test_api_help():
    """api --help should show the host/port options."""
    result = runner.invoke(cli, ["api", "--help"])
    assert result.exit_code == 0
    assert "--port" in result.output
    assert "--host" in result.output


def test_setup_profile_help():
    """setup-profile --help should show --cv, --linkedin, --github options."""
    result = runner.invoke(cli, ["setup-profile", "--help"])
    assert result.exit_code == 0
    assert "--cv" in result.output
    assert "--linkedin" in result.output
    assert "--github" in result.output


def test_setup_profile_preserves_github_username(tmp_path):
    """BUG-1 regression: github_username must survive merge in CLI flow."""
    from src.services.profile.models import UserPreferences
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
