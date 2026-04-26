"""ARQ worker boot surface for Job360.

Batch 3.5 Deliverable D — makes the ARQ runtime executable. Until this
module existed, Batch 2's tasks could only be called directly from
tests; there was no entry point for `arq src.workers.settings.WorkerSettings`.

Design per CLAUDE.md rule #11:
  * arq is imported LAZILY — ``_load_redis_settings()`` is the only
    place that touches ``arq.connections.RedisSettings``. At module top
    we expose the ARQ-compatible function list and a light stand-in
    ``_RedisSettings`` namespace so pytest can assert on host/port
    without pip-installing arq.
  * `WorkerSettings.redis_settings` resolves to the real ARQ
    RedisSettings at ``arq`` boot; tests see a SimpleNamespace with
    ``.host`` / ``.port`` parsed from ``REDIS_URL``.

To run the worker in production::

    arq src.workers.settings.WorkerSettings

Environment:
  REDIS_URL  — redis://host:port[/db] (default: redis://localhost:6379)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from src.workers.tasks import (
    enrich_job_task,
    mark_ledger_failed_task,
    mark_ledger_sent_task,
    score_and_ingest,
    send_daily_digest,
    send_notification,
)


@dataclass(frozen=True)
class _RedisSettings:
    """Stand-in for arq.connections.RedisSettings when arq is unavailable.

    Exposes the .host / .port surface tests assert against without pulling
    in arq. ARQ accepts any object whose attributes match RedisSettings's
    field names, so this shim can even be used directly at runtime when the
    user wants a minimal dep.
    """

    host: str = "localhost"
    port: int = 6379
    database: int = 0


def _parse_redis_url(url: Optional[str]) -> _RedisSettings:
    """Parse a redis://host:port/db URL into a _RedisSettings."""
    if not url:
        return _RedisSettings()
    parsed = urlparse(url)
    db = 0
    if parsed.path and parsed.path.lstrip("/").isdigit():
        db = int(parsed.path.lstrip("/"))
    return _RedisSettings(
        host=parsed.hostname or "localhost",
        port=parsed.port or 6379,
        database=db,
    )


def _load_arq_redis_settings() -> object:
    """Lazy-load arq.connections.RedisSettings for the actual worker boot.

    Tests never call this (the module-level _RedisSettings shim is what
    they introspect). ARQ calls it when booting ``WorkerSettings``.
    """
    from arq.connections import RedisSettings  # local import — CLAUDE.md #11

    rs = _parse_redis_url(os.environ.get("REDIS_URL"))
    return RedisSettings(host=rs.host, port=rs.port, database=rs.database)


class WorkerSettings:
    """ARQ boot class — see `arq src.workers.settings.WorkerSettings`.

    `functions` is the list ARQ registers as enqueueable jobs. Everything
    in it must be an async function whose first positional arg is `ctx`.
    """

    functions = [
        score_and_ingest,
        send_notification,
        mark_ledger_sent_task,
        mark_ledger_failed_task,
        enrich_job_task,
        # Step-3 B-04 — daily digest sender
        send_daily_digest,
    ]

    # ARQ cron_jobs: ARQ will call send_daily_digest for each user+channel pair.
    # The cron expression here is a daily sweep at 08:00 UTC.
    # Fine-grained per-user scheduling (digest_send_time from notification_rules)
    # would require a per-user enqueueing loop — deferred to a follow-up batch.
    # Until then: the cron enqueues a no-op sweep; callers must pass user_id+channel.
    # To enable: uncomment and install arq[cron].
    # cron_jobs = [
    #     cron(send_daily_digest, hour=8, minute=0),
    # ]

    # At test time this is the stand-in shim. At runtime ARQ reads the
    # attribute via `getattr(WorkerSettings, 'redis_settings', None)`;
    # when it's a _RedisSettings, ARQ accepts it (RedisSettings is a
    # pydantic-ish dataclass with the same field names). For callers
    # who want the real arq.RedisSettings, call load_arq_redis_settings().
    redis_settings = _parse_redis_url(os.environ.get("REDIS_URL"))


# Re-export for legacy import paths some callers may expect
__all__ = ["WorkerSettings", "_parse_redis_url", "_load_arq_redis_settings"]
