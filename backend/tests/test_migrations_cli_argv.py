"""`python -m migrations.runner down 0010` does NOT target migration 0010.

The second positional argument is a **db_path**, not a migration selector, so a
stem typed there is swallowed as a connection string and `down` still reverts
whatever is at the head. Against a head of 0041 that reverts 0041, and a
following `up` re-applies it: it looks like it worked and changes nothing about
0010.

That is the worst class of documentation drift in this repo — a documented
destructive command that runs successfully and does something OTHER than what
the doc says. `docs/product/troubleshooting.md` §8 cites these tests by name
instead of restating the argument parsing, which rots on any edit above it.

No database is touched: `_cli` dispatches to module-level `up`/`down`, so
capturing those proves the argv contract offline.
"""

from __future__ import annotations

import inspect

import pytest

from migrations import runner

# Bound before any fixture can replace it — the signature assertion below must
# read the REAL `down`, not a recorder.
_REAL_DOWN = runner.down


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Replace the real up/down with recorders; return the (cmd, db_path) log."""
    calls: list[tuple[str, str]] = []

    async def fake_up(db_path: str) -> list[str]:
        calls.append(("up", db_path))
        return []

    async def fake_down(db_path: str) -> str | None:
        calls.append(("down", db_path))
        return None

    monkeypatch.setattr(runner, "up", fake_up)
    monkeypatch.setattr(runner, "down", fake_down)
    return calls


def test_down_swallows_a_migration_stem_as_a_db_path(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[str, str]]
) -> None:
    """`down 0010` passes "0010" as the CONNECTION, never as a selector."""
    monkeypatch.setattr(runner.sys, "argv", ["runner", "down", "0010"])

    assert runner._cli() == 0
    assert captured == [("down", "0010")]


def test_down_takes_no_migration_selector(monkeypatch: pytest.MonkeyPatch, captured: list[tuple[str, str]]) -> None:
    """There is no `down <stem>` and no `down --all` — only the head, one step.

    `down`'s own signature is the proof: one positional (the db_path) and one
    keyword-only test hook. Nothing names a target migration.
    """
    params = inspect.signature(_REAL_DOWN).parameters
    assert list(params) == ["db_path", "migrations_dir"]
    assert params["migrations_dir"].kind is inspect.Parameter.KEYWORD_ONLY

    # And the bare form still reaches `down` — with the default db_path.
    monkeypatch.setattr(runner.sys, "argv", ["runner", "down"])
    assert runner._cli() == 0
    assert captured == [("down", "data/jobs.db")]


def test_up_takes_the_same_second_argument_as_a_db_path(
    monkeypatch: pytest.MonkeyPatch, captured: list[tuple[str, str]]
) -> None:
    """The `down`/`up` pair a reader types after a failed `down 0010`."""
    monkeypatch.setattr(runner.sys, "argv", ["runner", "up", "0010"])

    assert runner._cli() == 0
    assert captured == [("up", "0010")]


def test_unknown_command_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo must not silently fall through to a destructive default."""
    monkeypatch.setattr(runner.sys, "argv", ["runner", "downgrade", "0010"])
    assert runner._cli() == 2

    monkeypatch.setattr(runner.sys, "argv", ["runner"])
    assert runner._cli() == 2
