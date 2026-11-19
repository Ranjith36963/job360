"""Make an application READY for the PC queue in a test that is not about the CV (2026-11-19).

The PC queue offers an application only when check_submit can pass it (``own_browser._not_ready``): a CV that the
user saw or a strong review cleared, and no open ask. A test about routing, rotation or pace gives each of its PC
rows this honest input: a saved CV and the user's own ``cv_seen`` of exactly that CV (the web client, the user).
"""
from __future__ import annotations

from typing import Any


async def pc_ready(client: Any, application_id: int) -> int:
    """Save a CV for ``application_id`` and mark it seen by the user (``client`` is the user's web client).
    Returns the CV's artifact id."""
    from src.services.applications import kit

    text = f"Ada Lovelace\nPython data engineer, application {application_id}."
    saved = await client.post(f"/api/applications/{application_id}/artifacts", json={"kind": "cv", "text": text})
    assert saved.status_code in (200, 201), saved.text
    art = int(saved.json()["artifact_id"])
    seen = await client.post(f"/api/applications/{application_id}/events", json={
        "event_type": "cv_seen", "payload": {"artifact_id": art, "sha256": kit.sha256_text(text), "where": "web"}})
    assert seen.status_code == 201, seen.text
    return art
