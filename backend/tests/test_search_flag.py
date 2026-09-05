"""The old search UI goes behind a flag, off by default (spec R12/R13).

Frozen. `SEARCH_UI_ENABLED` and `CATALOG_CRONS_ENABLED` do not exist in
`src/core/settings.py` yet, so every test below is expected to fail
(AttributeError) until slice 2 adds them. Not a security control (S10): the
search routes keep their existing `Depends` auth regardless of the flag —
these tests only pin whether the FEATURE exists.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.mark.asyncio
async def test_search_routes_404_when_the_flag_is_off(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "SEARCH_UI_ENABLED", False)

    async with authenticated_async_context() as client:
        started = await client.post("/api/search")
        assert started.status_code == 404, started.text
        status = await client.get("/api/search/anything/status")
        assert status.status_code == 404, status.text


@pytest.mark.asyncio
async def test_search_routes_work_when_on(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "SEARCH_UI_ENABLED", True)

    async with authenticated_async_context() as client:
        started = await client.post("/api/search")
        assert started.status_code != 404, started.text
        assert started.status_code == 200, started.text
        run_id = started.json()["run_id"]

        status = await client.get(f"/api/search/{run_id}/status")
        assert status.status_code != 404, status.text


def test_catalog_crons_are_off_by_default(monkeypatch):
    """`WorkerSettings.cron_jobs` builds ONCE at class-definition time (module
    import), so exercising both flag states means re-importing both the
    settings module (which defines the flag) and `src.workers.settings`
    (whose class body reads it) — reassigning the constant after the fact
    would test nothing about how `cron_jobs` is actually built.
    """
    monkeypatch.delenv("CATALOG_CRONS_ENABLED", raising=False)

    import src.core.settings as settings
    import src.workers.settings as worker_settings

    importlib.reload(settings)
    importlib.reload(worker_settings)
    try:
        names = {getattr(c.coroutine, "__name__", "") for c in worker_settings.WorkerSettings.cron_jobs}
        assert "refresh_catalog" not in names, "refresh_catalog must be OFF by default"
        assert "enrichment_sweep" not in names, "enrichment_sweep must be OFF by default"
        assert "nightly_ghost_sweep" in names, "nightly_ghost_sweep must stay unconditional"
        assert "notification_tick" in names, "notification_tick must stay unconditional"
    finally:
        importlib.reload(settings)
        importlib.reload(worker_settings)


def test_catalog_crons_on_with_the_flag(monkeypatch):
    monkeypatch.setenv("CATALOG_CRONS_ENABLED", "1")

    import src.core.settings as settings
    import src.workers.settings as worker_settings

    importlib.reload(settings)
    importlib.reload(worker_settings)
    try:
        names = {getattr(c.coroutine, "__name__", "") for c in worker_settings.WorkerSettings.cron_jobs}
        assert "refresh_catalog" in names
        assert "enrichment_sweep" in names
        assert "nightly_ghost_sweep" in names
        assert "notification_tick" in names
    finally:
        monkeypatch.delenv("CATALOG_CRONS_ENABLED", raising=False)
        importlib.reload(settings)
        importlib.reload(worker_settings)
