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
from typing import Any, Optional
from urllib.parse import urlparse

from src.workers.tasks import (
    enrich_job_task,
    mark_ledger_failed_task,
    mark_ledger_sent_task,
    nightly_ghost_sweep,
    notification_tick,
    refresh_catalog,
    score_and_ingest,
    send_bundle,
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


async def worker_startup(ctx: dict[str, Any]) -> None:
    """ARQ ``on_startup`` — populate ``ctx`` with what every task needs.

    WITHOUT this hook, real ARQ only injects ``job_id``/``redis``/etc. into
    ``ctx``; it never sets ``ctx['db']`` or ``ctx['enqueue']``. Every task reads
    ``ctx['db']`` (tasks.py), so the FIRST cron tick raised ``KeyError: 'db'`` and
    the whole notification/digest/ghost-sweep/enrichment pipeline was silently
    dead in production. Tests passed only because they build ``ctx`` by hand.

    This opens ONE connection for the worker process and exposes ``enqueue`` as a
    thin wrapper over ``ctx['redis'].enqueue_job``. ``max_jobs = 1`` (below) keeps
    tasks serial so the single connection is never used by two coroutines at once
    (the psycopg "another operation in progress" hazard).
    """
    # docs/fable/09 P0 — the worker process had NO Sentry, so a crashing cron was
    # invisible. Initialise it (prod-gated, no-op in dev/test) so worker failures
    # actually reach the same Sentry project as API errors, tagged component=worker.
    from src.core.observability import init_sentry

    init_sentry(component="worker")

    from migrations import runner  # local import — keep module import light
    from src.core.settings import DB_PATH
    from src.repositories import pg

    # Worker may boot independently of the API; migrations are idempotent and
    # advisory-locked, so applying them here is safe.
    await runner.up(str(DB_PATH))

    conn = await pg.connect(str(DB_PATH))
    conn.row_factory = pg.Row
    ctx["db"] = conn

    async def _enqueue(function_name: str, *args: object) -> object:
        redis = ctx.get("redis")
        if redis is None:  # defensive — real ARQ always injects redis
            raise RuntimeError("worker enqueue called before redis was injected")
        return await redis.enqueue_job(function_name, *args)

    ctx["enqueue"] = _enqueue


async def worker_shutdown(ctx: dict[str, Any]) -> None:
    """ARQ ``on_shutdown`` — close the connection opened in ``worker_startup``."""
    db = ctx.get("db")
    if db is not None:
        await db.close()


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
        # Channels & Notifications overhaul — bundle sender + tick
        send_bundle,
        notification_tick,
        # Step-3 B-14 — nightly ghost-detection sweep
        nightly_ghost_sweep,
        # The production loop: refill the shared catalog on a timer
        refresh_catalog,
    ]

    # Lifecycle hooks — open/close the DB connection tasks read from ``ctx['db']``
    # and wire ``ctx['enqueue']``. staticmethod so ARQ can call them as plain
    # ``await on_startup(ctx)`` without an implicit ``self`` bind.
    on_startup = staticmethod(worker_startup)
    on_shutdown = staticmethod(worker_shutdown)

    # Serial execution: tasks share the single ``ctx['db']`` connection, so run
    # one at a time to avoid two coroutines using one psycopg connection at once.
    # Worker throughput here is not latency-critical (notifications/enrichment).
    max_jobs = 1

    # Explicit per-task ceiling (docs/fable/04) sized to the slowest known task —
    # the LLM-judge/enrichment batch. Without this, ARQ's generic default lets a
    # hung LLM call tie up the (single) worker slot indefinitely.
    job_timeout = 600  # seconds

    # M4 — liveness. arq writes a heartbeat key to Redis every
    # ``health_check_interval`` seconds and `arq --check` reads it back; the key
    # is set with a TTL of interval+1, so a dead worker stops looking alive one
    # interval after it dies.
    #
    # arq's default is 3600, which means a worker that died at 00:01 still
    # reported healthy at 00:59. That is not hypothetical here — this worker has
    # died silently in production before (commit 7f906c6, "revive dead ARQ
    # worker (P0)"), and nothing noticed because nothing was watching. 30s makes
    # death observable within roughly one minute.
    #
    # Cost is one small Redis SET per interval — negligible next to the queue
    # traffic itself.
    health_check_interval = 30  # seconds

    # M4 — explicit retry ceiling. arq's default is also 5, but stating it means
    # a future arq upgrade cannot change our retry behaviour silently, and it
    # documents that a poisoned job gives up rather than looping forever on the
    # single (max_jobs=1) worker slot.
    max_tries = 5

    # Periodic / cron jobs — Step-3 B-14.
    # ARQ cron format: ``cron(func, *, hour=None, minute=None, ...)``.
    # IMPORTANT: newer arq reads ``WorkerSettings.__dict__['cron_jobs']`` directly
    # and does NOT trigger descriptors — so cron_jobs must be a REAL list in the
    # class body (the old _CronJobsDescriptor returned itself and crashed the
    # worker with "'_CronJobsDescriptor' object is not iterable"). Build it
    # eagerly; fall back to [] if arq is somehow absent so importing this module
    # never fails in a minimal env. (arq is a declared dependency; not in the
    # lazy-import rule list, so a top-level import here is fine.)
    try:
        from arq.cron import cron as _cron  # noqa: PLC0415

        cron_jobs = [
            _cron(nightly_ghost_sweep, hour=2, minute=0),
            # THE PRODUCTION LOOP. Nothing fetched jobs on a timer before
            # this: run_search fired only on a human click, while
            # purge_old_jobs deleted anything >30 days, so the catalog
            # drained (prod 2026-07-27: 11 run_log rows in 25 days, 112 jobs
            # visible of 5,827 fetched). Daily is the right cadence — jobs are
            # posted daily and the purge window is 30 days — and it keeps
            # keyed-API quota modest (~30 runs/month, not ~120). 04:00 UTC:
            # clear of the 02:00 ghost sweep, done before UK morning.
            # Cost is CPU only; user_id=None makes the paid LLM stages
            # structurally unreachable (see refresh_catalog's docstring).
            _cron(refresh_catalog, hour=4, minute=0),
            _cron(notification_tick, minute=set(range(0, 60, 5))),
        ]
        del _cron
    except Exception:  # noqa: BLE001 — arq absent in a minimal env
        cron_jobs = []

    # ARQ reads redis_settings from the class __dict__ and needs the REAL arq
    # RedisSettings surface (.sentinel/.ssl/.password/...). The minimal
    # _RedisSettings shim lacks those (crashed create_pool with "no attribute
    # 'sentinel'") AND drops the password from a redis://user:pass@host URL.
    # Use arq's RedisSettings.from_dsn (parses auth/ssl too); fall back to the
    # shim only if arq is absent (minimal test env).
    try:
        from arq.connections import RedisSettings as _ArqRedisSettings  # noqa: PLC0415

        _redis_url = os.environ.get("REDIS_URL")
        redis_settings = (
            _ArqRedisSettings.from_dsn(_redis_url) if _redis_url else _ArqRedisSettings()
        )
        del _ArqRedisSettings, _redis_url
    except Exception:  # noqa: BLE001 — arq absent in a minimal env
        redis_settings = _parse_redis_url(os.environ.get("REDIS_URL"))


# Re-export for legacy import paths some callers may expect
__all__ = ["WorkerSettings", "_parse_redis_url", "_load_arq_redis_settings"]
