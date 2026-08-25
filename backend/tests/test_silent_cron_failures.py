"""A cron that crashes must not report the same thing as a cron with no work.

PRODUCTION EVIDENCE (2026-08-13). ``notification_tick`` prints
``{'checked': 0, 'enqueued': 0}`` 288 times a day in the Railway worker log.
The prod DB says ``notification_rules`` has 0 rows, so today that output is
honest — but the code produces the IDENTICAL dict when its only SELECT raises,
and it logs nothing on the way out. So the one instrument that would ever show
the notification system breaking is structurally incapable of showing it: a
dead query and an empty table are byte-identical on the wire.

Same shape in ``send_bundle``: a failed digest SELECT returns the very dict
that means "nothing was queued".

These tests pin the DISTINCTION, not the happy path. The success contract
(``checked``/``enqueued``/``sent``/``failed``/``jobs_count``) is covered by
tests/test_notification_tick.py and must keep passing unchanged.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from src.workers.tasks import notification_tick, send_bundle


class _ExplodingDB:
    """A connection whose every query raises — i.e. Postgres is gone.

    Mimics just enough of the aiosqlite-shaped psycopg wrapper for the task
    to reach its except branch.
    """

    def __init__(self) -> None:
        self.row_factory: Any = None

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("connection is closed")


@pytest.mark.asyncio
async def test_notification_tick_reports_a_failed_query_instead_of_checked_zero(caplog):
    ctx = {"db": _ExplodingDB(), "enqueue": lambda *a, **k: None}

    with caplog.at_level(logging.ERROR):
        result = await notification_tick(ctx)

    # It still must not raise — an ARQ cron that throws is retried blindly.
    assert result["checked"] == 0
    assert result["enqueued"] == 0
    # ...but the failure must be DISTINGUISHABLE from "no rules exist".
    assert result.get("error") == 1, (
        "a crashed query returned the same dict as an empty notification_rules table"
    )
    assert any(
        "notification_tick" in rec.getMessage() for rec in caplog.records
    ), "the exception was swallowed with no log line"


@pytest.mark.asyncio
async def test_send_bundle_reports_a_failed_query_instead_of_sent_zero(caplog):
    ctx = {"db": _ExplodingDB()}

    with caplog.at_level(logging.ERROR):
        result = await send_bundle(ctx, "alice")

    assert result["sent"] == 0
    assert result["failed"] == 0
    assert result["jobs_count"] == 0
    assert result.get("error") == 1, (
        "a crashed digest query returned the same dict as an empty digest queue"
    )
    assert any("send_bundle" in rec.getMessage() for rec in caplog.records)
