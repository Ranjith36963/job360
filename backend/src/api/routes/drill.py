"""Quick lookup of a user's applications."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from src.api.dependencies import get_request_db
from src.repositories.database import JobDatabase

router = APIRouter()


@router.get("/drill/apps")
async def drill_apps(
    user_id: int,
    db: JobDatabase = Depends(get_request_db),  # noqa: B008
) -> list[dict[str, Any]]:
    """Return the applications of the given user."""
    cur = await db._db.execute(
        "SELECT * FROM applications WHERE user_id = ?", (user_id,)
    )
    rows = await cur.fetchall()
    return [dict(r) for r in rows]
