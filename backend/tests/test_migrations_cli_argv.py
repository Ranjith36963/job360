"""`migrations.runner`'s CLI argument shape, pinned.

The trap this guards: ``python -m migrations.runner down 0010`` reads like
"revert migration 0010" and is not. ``down`` takes no stem — the second
positional is the db_path, and the runner reverts whatever sits at the head.
A doc sentence saying so rots; this asserts it.
"""

import pytest

from migrations import runner


def _capture(monkeypatch, argv):
    """Run ``runner._cli`` with ``argv``, returning what it passed to up/down."""
    seen: dict[str, object] = {}

    async def fake_down(db_path, **kwargs):
        seen["cmd"] = "down"
        seen["db_path"] = db_path
        seen["kwargs"] = kwargs
        return "0041_event_source_and_schedule"

    async def fake_up(db_path, **kwargs):
        seen["cmd"] = "up"
        seen["db_path"] = db_path
        seen["kwargs"] = kwargs
        return []

    monkeypatch.setattr(runner, "down", fake_down)
    monkeypatch.setattr(runner, "up", fake_up)
    monkeypatch.setattr(runner.sys, "argv", argv)
    rc = runner._cli()
    return rc, seen


def test_down_swallows_a_migration_stem_as_the_db_path(monkeypatch):
    """`down 0010` does NOT target 0010 — it connects to a db named "0010"."""
    rc, seen = _capture(monkeypatch, ["runner", "down", "0010"])

    assert rc == 0
    assert seen["cmd"] == "down"
    assert seen["db_path"] == "0010"
    # No selector reaches `down` at all: there is no `down <stem>`.
    assert seen["kwargs"] == {}


def test_down_takes_no_selector_and_defaults_the_db_path(monkeypatch):
    rc, seen = _capture(monkeypatch, ["runner", "down"])

    assert rc == 0
    assert seen["db_path"] == "data/jobs.db"
    assert seen["kwargs"] == {}


def test_up_has_the_same_positional_shape(monkeypatch):
    rc, seen = _capture(monkeypatch, ["runner", "up", "some/other.db"])

    assert rc == 0
    assert seen["cmd"] == "up"
    assert seen["db_path"] == "some/other.db"


def test_no_command_is_a_usage_error(monkeypatch):
    monkeypatch.setattr(runner.sys, "argv", ["runner"])
    assert runner._cli() == 2


@pytest.mark.parametrize("stem_like", ["0010", "0041_event_source_and_schedule"])
def test_every_stem_shaped_argument_is_read_as_a_path(monkeypatch, stem_like):
    _, seen = _capture(monkeypatch, ["runner", "down", stem_like])
    assert seen["db_path"] == stem_like
