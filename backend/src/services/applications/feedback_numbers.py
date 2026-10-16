"""Feedback numbers (slice 3) - SKELETON. The view bodies land in later commits."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover
    from src.repositories.database import JobDatabase


async def compute_views(
    db: JobDatabase,
    user_id: str,
    since_val: Optional[str],
    selected: list[str],
    *,
    company: Optional[str],
    view: str,
) -> dict[str, Any]:
    return {v: None for v in selected}
