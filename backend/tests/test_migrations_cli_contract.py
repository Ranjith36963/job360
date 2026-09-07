"""The migrations CLI's argv contract — a DESTRUCTIVE command, pinned.

`python -m migrations.runner down 0010` reads like "revert 0010". It is not:
`_cli` takes argv[2] as a **db_path**, and `down()` always reverts whatever sits
at the head. Somebody who believes the friendlier reading reverts the wrong
migration against real data.

`docs/product/troubleshooting.md` warns about exactly this, and used to prove it
with two `runner.py:NN` citations. Those rotted — the code moved and the numbers
still looked authoritative. A behaviour worth documenting is worth a test, so the
doc cites these test names instead.
"""
from __future__ import annotations

import sys
from typing import Optional

import pytest

from migrations import runner


def test_down_takes_a_db_path_not_a_migration_stem(monkeypatch: pytest.MonkeyPatch) -> None:
    """argv[2] is swallowed as a connection path, never as a selector."""
    seen: list[str] = []

    async def _fake_down(db_path: str, **kwargs: object) -> Optional[str]:
        seen.append(db_path)
        return "0030_head"

    monkeypatch.setattr(runner, "down", _fake_down)
    monkeypatch.setattr(sys, "argv", ["runner", "down", "0010"])

    assert runner._cli() == 0
    # The stem the caller typed arrived as the db_path argument. `down()` never
    # receives a migration selector, because it has no parameter for one.
    assert seen == ["0010"]


def test_down_has_no_parameter_that_selects_a_migration() -> None:
    """The signature is the proof: nothing in it can name a stem to revert."""
    import inspect

    params = inspect.signature(runner.down).parameters
    assert list(params) == ["db_path", "migrations_dir"]


def test_cli_usage_line_documents_db_path_as_the_second_argument(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "argv", ["runner"])
    assert runner._cli() == 2
    assert "[up|down|status] [db_path]" in capsys.readouterr().err
