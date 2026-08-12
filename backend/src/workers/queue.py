"""Enqueue an ARQ job from OUTSIDE the worker (the web process, the CLI).

Why this module exists
----------------------
Issue #271. ``_maybe_trigger_rescore`` fired ``rescore_user_feed`` with
``asyncio.create_task`` inside the FastAPI process: no queue entry, no retry, no
completion record. ``main`` auto-deploys on every merge, so a deploy alone killed
anything in flight — measured 2026-08-11, 9,708 ``user_feed`` rows sat on a
profile_version older than their user's current one, all pointing at jobs still
in the catalog.

``src/api/routes/search.py`` already had a private version of this (its
``_make_notification_enqueue``), but it is a closure that returns ``None`` on
failure and is scoped to one search run. This is the shared, importable door so
the next caller does not write a third copy.

Contract (deliberately narrow):
  * returns ``True`` when the job is known to the queue, ``False`` otherwise;
  * **never raises** — a queue outage must never fail the user's action;
  * connects lazily and closes the pool it opened, so a dead Redis costs one
    bounded timeout and nothing more.

``arq`` is imported inside the function (CLAUDE.md rule #11): the API process
must not pay for it on unrelated requests.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

logger = logging.getLogger("job360.workers.queue")

# Hard ceiling on reaching Redis. The caller is usually on a request path, so a
# configured-but-dead Redis must cost ~2s once, not arq's default ~10s of
# retrying (conn_retries=5, conn_retry_delay=1).
ENQUEUE_CONNECT_TIMEOUT_SECONDS = 2


async def enqueue_job(
    function_name: str,
    *args: Any,
    job_id: Optional[str] = None,
    **kwargs: Any,
) -> bool:
    """Queue ``function_name`` for the ARQ worker. Returns True when queued.

    Args:
        function_name: the name registered in ``WorkerSettings.functions``.
        *args: positional arguments passed to the task after ``ctx``.
        job_id: optional ARQ ``_job_id``. ARQ refuses a second job with an id it
            already knows, which is how a re-runnable backfill avoids
            double-work. An id collision counts as SUCCESS here — the work is
            already owed, which is exactly what the caller wanted.
        **kwargs: keyword arguments passed through to the task.

    Returns:
        ``True`` if the queue accepted (or already had) the job. ``False`` when
        there is no ``REDIS_URL``, arq is missing, Redis is unreachable, or the
        enqueue itself failed. Callers decide what to do with ``False`` — the
        profile route falls back to an in-process run and logs loudly.
    """
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if not redis_url:
        logger.warning(
            "enqueue skipped: REDIS_URL is not set, so %s cannot be queued",
            function_name,
        )
        return False

    pool: Any = None
    try:
        from arq import create_pool  # noqa: PLC0415 — lazy (rule #11)
        from arq.connections import RedisSettings  # noqa: PLC0415

        redis_settings = RedisSettings.from_dsn(redis_url)
        redis_settings.conn_retries = 0
        redis_settings.conn_timeout = ENQUEUE_CONNECT_TIMEOUT_SECONDS
        # BOTH calls are bounded, not just the connect. Found by adversarial
        # review: only create_pool was wrapped, and arq maps conn_timeout to the
        # CONNECT phase only — a Redis that accepts the TCP connection and then
        # stops responding (failing-over, paused, packet-black-holed) leaves
        # `enqueue_job` awaiting forever. That hangs a user-facing profile save
        # AND never reaches the in-process fallback below, so the work is lost —
        # which is exactly the safety claim this module exists to make.
        pool = await asyncio.wait_for(
            create_pool(redis_settings), timeout=ENQUEUE_CONNECT_TIMEOUT_SECONDS
        )
        await asyncio.wait_for(
            pool.enqueue_job(function_name, *args, _job_id=job_id, **kwargs),
            timeout=ENQUEUE_CONNECT_TIMEOUT_SECONDS,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — a dead queue must never raise here
        logger.warning(
            "enqueue failed for %s (%s: %s)", function_name, type(exc).__name__, exc
        )
        return False
    finally:
        if pool is not None:
            # redis-py 5 renamed close() -> aclose(); support both.
            closer = getattr(pool, "aclose", None) or getattr(pool, "close", None)
            if closer is not None:
                try:
                    result = closer()
                    if hasattr(result, "__await__"):
                        await result
                except Exception:  # noqa: BLE001, S110 — cleanup must stay quiet
                    pass
