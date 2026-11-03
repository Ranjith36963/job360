"""Test helper (autonomy A8): give an application the single-use ``submit_cleared`` an assistant's receipt
needs, the way production gets one from check_submit saying ``submit``. For tests about other rules whose
setup cannot reach a ``submit`` verdict. One clearance covers one receipt: call it again per receipt.
"""
from __future__ import annotations


async def clear(user_id: str, application_id: int, actor: str = "claude-code") -> int:
    from src.api import dependencies as api_deps
    from src.services.applications import submit_clearance

    db = await api_deps.get_db()
    return await submit_clearance.record(db, user_id, application_id, actor, "https://example.test/apply", "test setup")
