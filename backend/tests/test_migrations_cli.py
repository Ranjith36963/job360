"""What `python -m migrations.runner <cmd> <arg>` actually does with its argv.

This exists because `down` LOOKS like it takes a migration stem and does not.
`python -m migrations.runner down 0010` runs successfully, prints a stem, and
reverts whatever is at the HEAD — `0010` is swallowed as a db_path. A command
that succeeds while doing something other than what it appears to do is the
most expensive kind of documentation bug there is, so it is pinned here rather
than described in prose that nothing checks.

`tests/test_migrations.py` covers the `up`/`down`/`status` FUNCTIONS. This file
covers only the argv layer between the shell and them.
"""

import sys

from migrations import runner


def _run_cli(monkeypatch, argv, *, calls):
    """Drive `runner._cli()` with a fake argv, recording what it dispatched."""

    def _record(name):
        def _fake(db_path, **kwargs):
            calls.append((name, db_path))

            async def _coro():
                return None

            return _coro()

        return _fake

    monkeypatch.setattr(runner, "up", _record("up"))
    monkeypatch.setattr(runner, "down", _record("down"))
    monkeypatch.setattr(sys, "argv", ["runner"] + argv)
    return runner._cli()


def test_down_treats_its_second_argument_as_a_db_path_not_a_migration_stem(monkeypatch):
    """`down 0010` does NOT target migration 0010 — it targets db_path "0010"."""
    calls = []
    assert _run_cli(monkeypatch, ["down", "0010"], calls=calls) == 0
    assert calls == [("down", "0010")]


def test_down_with_no_argument_uses_the_default_db_path(monkeypatch):
    calls = []
    assert _run_cli(monkeypatch, ["down"], calls=calls) == 0
    assert calls == [("down", "data/jobs.db")]


def test_up_takes_the_same_optional_db_path_and_no_selector(monkeypatch):
    calls = []
    assert _run_cli(monkeypatch, ["up", "/tmp/other.db"], calls=calls) == 0
    assert calls == [("up", "/tmp/other.db")]


def test_usage_names_only_up_down_status_and_an_optional_db_path(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["runner"])
    assert runner._cli() == 2
    assert "[up|down|status] [db_path]" in capsys.readouterr().err


def test_an_unknown_command_is_refused_rather_than_guessed(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["runner", "0010"])
    assert runner._cli() == 2
    assert "unknown command" in capsys.readouterr().err
