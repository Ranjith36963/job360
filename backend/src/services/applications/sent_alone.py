"""Sent on its own: the log of answers and essays that went to an employer without the user (owner plan
2026-11-02, Needs-you item 2). A log, not a to-do: it never counts on Needs you.

Nothing is sent on its own yet. Autonomy step 4 (the Kits session) is what will send an answer or essay
without the owner, behind the owner's on/off setting; until it ships, :func:`list_items` returns nothing
and the page shows nothing (it never invents an item). Each item, when step 4 fills it, is
``{id, application_id, job_company, job_title, kind ("answer" | "essay"), question, answer, sources:
[{label, text}], sent_at, reported_wrong}``: the words sent, every source sentence it was checked against,
and whether the user pressed "Wrong fact". Always scoped by ``user_id`` (rules #12/#25).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase


async def list_items(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """The user's sent-on-its-own items, newest first. Empty until autonomy step 4 sends anything."""
    del db, user_id
    return []


async def get_item(db: JobDatabase, user_id: str, item_id: int) -> Optional[dict[str, Any]]:
    """One of the user's items, or ``None`` (another user's item is ``None`` too)."""
    return next((i for i in await list_items(db, user_id) if i["id"] == item_id), None)
