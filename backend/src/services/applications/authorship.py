"""``recorded_by`` / ``made_by`` are derived, never accepted (spec S3).

One function, one source of truth: a request body field named ``recorded_by``
or ``made_by`` is rejected with 422 at the Pydantic layer (``extra="forbid"``
on every applications request model) — silently dropping it would let a
caller believe it worked, and forging authorship is the one thing an
append-only log must not permit.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from src.core import settings

if TYPE_CHECKING:  # pragma: no cover — type-only
    from src.api.auth_deps import CurrentUser


def actor_for(user: CurrentUser) -> str:
    """``"web"`` for a session, ``"token:<name>"`` for a personal token,
    ``"agent:<client>"`` for an OAuth grant.

    The OAuth client name is attacker-supplied text by construction (slice 1
    R2 sanitises it at registration; this re-truncates it to
    ``settings.APPLICATION_ACTOR_NAME_MAX_CHARS`` — spec C5). It is stored as
    data here, never rendered as markup.
    """
    if user.auth_via == "token":
        name = (user.actor_name or "").strip() or "unknown"
        return f"token:{name}"
    if user.auth_via == "oauth":
        name = (user.actor_name or "").strip()[: settings.APPLICATION_ACTOR_NAME_MAX_CHARS] or "unknown"
        return f"agent:{name}"
    return "web"
