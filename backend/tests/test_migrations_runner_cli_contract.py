"""The `migrations.runner` CLI argument contract, pinned.

WHY THIS FILE EXISTS. `docs/product/troubleshooting.md` warns that
``python -m migrations.runner down 0010`` does NOT revert migration 0010 — the
second positional argument is a **db_path**, so `0010` is swallowed as a
connection string and the runner reverts whatever is at the head instead. That
is the worst class of doc claim in this repo: a documented command that runs
successfully and does something other than what the reader intended, against
real data.

The warning used to be anchored to raw line numbers in `runner.py`, which rot on
any unrelated edit above them (two of the four cited numbers had already drifted
when the 2026-09-11 doc truth-check read them). A behaviour worth documenting is
worth a test, so the doc now cites this file by name instead.

Offline by construction: the runner's `up`/`down` coroutines are replaced with
recorders, so nothing here touches Postgres.
"""

import asyncio
import inspect

import pytest

from migrations import runner


def test_down_takes_no_migration_stem() -> None:
    """`down()` reverses the HEAD. There is no stem selector to pass."""
    params = inspect.signature(runner.down).parameters
    assert list(params) == ["db_path", "migrations_dir"], (
        "down() grew or lost a parameter — troubleshooting.md's "
        "'`down` takes NO migration stem' warning describes this signature"
    )
    # `migrations_dir` is keyword-only and is a directory, not a selector.
    assert params["migrations_dir"].kind is inspect.Parameter.KEYWORD_ONLY


def test_cli_second_positional_is_a_db_path_not_a_stem(monkeypatch, capsys) -> None:
    """`down 0010` passes "0010" through as the DB PATH, not as a selector.

    This is the exact trap: the command exits 0 and reverts the head.
    """
    seen: list[str] = []

    async def _fake_down(db_path: str, **kwargs: object) -> str:
        seen.append(db_path)
        return "0030_head_stem"

    monkeypatch.setattr(runner, "down", _fake_down)
    monkeypatch.setattr(runner.sys, "argv", ["runner", "down", "0010"])

    assert runner._cli() == 0
    assert seen == ["0010"], (
        "the stem was not swallowed as a db_path — the CLI contract changed and "
        "docs/product/troubleshooting.md's down-migration warning needs revisiting"
    )
    assert "reverted: 0030_head_stem" in capsys.readouterr().out


@pytest.mark.parametrize("cmd", ["up", "down", "status"])
def test_db_path_defaults_when_omitted(monkeypatch, cmd: str) -> None:
    """With no second argument the runner falls back to the settings default."""
    seen: list[str] = []

    async def _recorder(db_path: str, **kwargs: object) -> None:
        seen.append(db_path)
        return None

    async def _status_recorder(db_path: str, **kwargs: object) -> tuple[list, bool]:
        seen.append(db_path)
        return [], True

    monkeypatch.setattr(runner, "up", _recorder)
    monkeypatch.setattr(runner, "down", _recorder)
    monkeypatch.setattr(runner, "_status_rows", _status_recorder)
    monkeypatch.setattr(runner.sys, "argv", ["runner", cmd])

    assert runner._cli() == 0
    assert seen == ["data/jobs.db"]


def test_unknown_command_is_refused() -> None:
    """A typo must not silently fall through to a destructive branch."""
    import sys

    argv = sys.argv
    try:
        sys.argv = ["runner", "revert"]
        assert runner._cli() == 2
    finally:
        sys.argv = argv


def test_down_is_one_step_and_returns_the_stem_it_reverted() -> None:
    """`down` returns the single stem it reverted — there is no `--all`."""
    src = inspect.getsource(runner.down)
    assert "applied[-1]" in src, "down() no longer pops the head"
    assert asyncio.iscoroutinefunction(runner.down)
