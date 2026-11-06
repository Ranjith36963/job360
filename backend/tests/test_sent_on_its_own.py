"""Sent on its own (owner plan 2026-11-02): a log of what went to an employer without the user.
Nothing is sent alone before autonomy step 4, so the log is empty and invents nothing."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_the_log_is_empty_until_anything_is_sent_on_its_own(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.get("/api/sent-on-its-own")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"items": [], "cv_sample": []}
        assert (await client.get("/api/needs-you-count")).json()["n"] == 0, "a log never counts as a to-do"


@pytest.mark.asyncio
async def test_wrong_fact_on_an_item_that_does_not_exist_is_404(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/sent-on-its-own/1/wrong-fact")
        assert resp.status_code == 404, resp.text
