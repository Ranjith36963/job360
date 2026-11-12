"""Test helper (owner 2026-11-09): seed the strong ``outreach_reviewed`` an automatic outreach send now needs.

For tests whose subject is another rule of ``check_outreach`` (caps, bounces, LinkedIn states): it stores the row
``outreach_review.record`` would store for a strong review of the contact's NEWEST draft, exact id + sha. The
review's own verification is proven through the real door in tests/test_outreach_reviewed.py."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from src.core import settings
from src.services.applications import outreach_review


def seed_strong_review(contact_id: int) -> int:
    """Store a strong review of contact ``contact_id``'s newest draft; returns the draft's id."""
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        row = conn.execute(
            "SELECT id, user_id, channel, text FROM contact_outreach WHERE contact_id = ? AND entry = 'message' "
            "ORDER BY id DESC LIMIT 1", (contact_id,),
        ).fetchone()
        assert row is not None, "seed_strong_review needs a saved draft"
        message_id, user_id, channel, text = int(row[0]), str(row[1]), str(row[2]), str(row[3] or "")
        stored = {
            "message_id": message_id, "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "claimed": "strong",
            "confidence": "strong", "downgraded": [], "lines": 1, "content_lines": 1, "covered": 1,
            "reviewer": "test-reviewer", "by": "web", "channel": channel,
        }
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO contact_outreach (user_id, contact_id, entry, channel, text, version_no, occurred_at, "
            "recorded_at, recorded_by, source_message_id, label) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, 'web', '', '')",
            (user_id, contact_id, outreach_review.ENTRY, channel, json.dumps(stored, sort_keys=True), now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return message_id
