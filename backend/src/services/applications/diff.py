"""Slice 8 (#515) — original vs tailored, as lines, read-only.

docs/plans/2026-09-11-cv-diff/spec.md R2. A pure function over two texts
plus one loader for each side; the route composes them. Nothing here
writes: the version that counts is the one the receipt names
(VISION decision 26), so there is no Keep and no MCP tool (rule M2).
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Optional

from src.core import settings
from src.repositories.database import JobDatabase


def diff_lines(base: str, target: str) -> dict[str, Any]:
    """Line diff of ``base`` → ``target`` as a flat list the web can paint.

    Each entry is ``{"op": "equal" | "del" | "add", "text": line}``; inside a
    changed hunk the removed lines come first, then the added ones. Both
    sides are cut to ``APPLICATION_DIFF_MAX_LINES`` before matching
    (``SequenceMatcher`` is quadratic in the worst case) and ``truncated``
    says so. ``autojunk=False`` so a CV full of repeated bullets is not
    "junk" to the matcher.
    """
    cap = settings.APPLICATION_DIFF_MAX_LINES
    base_lines = base.splitlines()
    target_lines = target.splitlines()
    truncated = len(base_lines) > cap or len(target_lines) > cap
    base_lines = base_lines[:cap]
    target_lines = target_lines[:cap]

    lines: list[dict[str, str]] = []
    added = removed = 0
    matcher = SequenceMatcher(None, base_lines, target_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            lines.extend({"op": "equal", "text": t} for t in base_lines[i1:i2])
            continue
        if tag in ("delete", "replace"):
            chunk = base_lines[i1:i2]
            removed += len(chunk)
            lines.extend({"op": "del", "text": t} for t in chunk)
        if tag in ("insert", "replace"):
            chunk = target_lines[j1:j2]
            added += len(chunk)
            lines.extend({"op": "add", "text": t} for t in chunk)
    return {"lines": lines, "added": added, "removed": removed, "truncated": truncated}


def load_profile_cv_text(user_id: str) -> str:
    """The candidate's stored CV text — the "original" a tailored cv is
    diffed against. Empty when no CV was ever uploaded (an empty base, not
    an error: the whole artifact then reads as added)."""
    # Lazy: profile storage pulls the extraction stack transitively (rule #16).
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    profile = load_profile(user_id)
    if profile is None:
        return ""
    return getattr(profile.cv_data, "raw_text", "") or ""


async def previous_version(
    db: JobDatabase, application_id: int, kind: str, version_no: int
) -> Optional[dict[str, Any]]:
    """The version of ``kind`` just below ``version_no`` on this application,
    or ``None`` for the first one."""
    cur = await db._db.execute(
        "SELECT id, kind, version_no, text FROM application_artifacts "
        "WHERE application_id = ? AND kind = ? AND version_no < ? "
        "ORDER BY version_no DESC LIMIT 1",
        (application_id, kind, version_no),
    )
    row = await cur.fetchone()
    return dict(row) if row else None


async def is_applied(db: JobDatabase, application_id: int, artifact_id: int) -> bool:
    """True when any receipt on this application names the version — the
    one fact that makes a version "the tailored one" (decision 26)."""
    cur = await db._db.execute(
        "SELECT 1 FROM application_receipts WHERE application_id = ? "
        "AND (cv_artifact_id = ? OR cover_letter_artifact_id = ?) LIMIT 1",
        (application_id, artifact_id, artifact_id),
    )
    return (await cur.fetchone()) is not None
