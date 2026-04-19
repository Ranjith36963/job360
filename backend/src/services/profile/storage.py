"""Per-user profile storage backed by the ``user_profiles`` table.

Batch 3.5.2 rebases profile storage from a single-file
``data/user_profile.json`` to a per-user SQLite table. Every helper
takes a required ``user_id`` argument. Data-loss bug from the single-
file era (two authenticated users overwriting each other's CVs) is
closed.

Backwards-compat: on the FIRST call to ``load_profile(DEFAULT_TENANT_ID)``
or ``profile_exists(DEFAULT_TENANT_ID)``, if the legacy JSON file still
exists AND no row for the default tenant is in the DB, the JSON is
hydrated into the DB and then deleted. One-shot, idempotent, and
non-destructive on parse error (file stays for the user to inspect).

Storage is synchronous (stdlib ``sqlite3``), not async (``aiosqlite``).
Single-row reads/writes are sub-millisecond; keeping this sync means
both the async HTTP path and the Click CLI can call it without
``asyncio.run`` wrappers. Matches the storage pattern for
``user_profile.json`` that predates this batch.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.settings import DATA_DIR, DB_PATH
from src.core.tenancy import DEFAULT_TENANT_ID
from src.services.profile.models import CVData, UserPreferences, UserProfile

logger = logging.getLogger("job360.profile.storage")

LEGACY_PROFILE_PATH: Path = DATA_DIR / "user_profile.json"
"""Pre-Batch-3.5.2 single-file store. Hydrated into the DB on first load
of ``DEFAULT_TENANT_ID`` then deleted. Monkey-patchable in tests."""


def save_profile(profile: UserProfile, user_id: str) -> None:
    """Upsert a UserProfile for ``user_id`` into user_profiles."""
    cv_json = json.dumps(asdict(profile.cv_data), default=str)
    pref_json = json.dumps(asdict(profile.preferences), default=str)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO user_profiles (user_id, cv_data, preferences, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                cv_data = excluded.cv_data,
                preferences = excluded.preferences,
                updated_at = excluded.updated_at
            """,
            (user_id, cv_json, pref_json, now),
        )
        conn.commit()
    logger.info("Profile saved for user %s", user_id)


def load_profile(user_id: str) -> Optional[UserProfile]:
    """Load the UserProfile for ``user_id``, or None if absent.

    Side effect: on first call for ``DEFAULT_TENANT_ID``, if the legacy
    JSON file exists and the DB row is missing, hydrate from JSON and
    delete the file.
    """
    _maybe_hydrate_legacy_json(user_id)
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT cv_data, preferences FROM user_profiles WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cv_raw = json.loads(row[0]) if row[0] else {}
    pref_raw = json.loads(row[1]) if row[1] else {}
    return UserProfile(
        cv_data=CVData(**_filter_fields(cv_raw, CVData)),
        preferences=UserPreferences(**_filter_fields(pref_raw, UserPreferences)),
    )


def profile_exists(user_id: str) -> bool:
    """Return True if ``user_id`` has a profile row.

    Also triggers one-shot legacy hydrate for DEFAULT_TENANT_ID so a
    fresh deployment with only ``user_profile.json`` on disk correctly
    reports ``True`` on the first call.
    """
    _maybe_hydrate_legacy_json(user_id)
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT 1 FROM user_profiles WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        return cur.fetchone() is not None


def _filter_fields(d: dict, cls) -> dict:
    """Drop keys not present as dataclass fields on ``cls``.

    Guards against schema drift where the JSON payload carries fields
    that a newer ``CVData`` / ``UserPreferences`` doesn't declare.
    """
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in valid}


def _maybe_hydrate_legacy_json(user_id: str) -> None:
    """One-shot: legacy JSON -> user_profiles[DEFAULT_TENANT_ID] + delete file.

    Only fires when user_id == DEFAULT_TENANT_ID AND the legacy JSON file
    still exists AND no DB row yet. On success, writes the row + deletes
    the JSON. On exception, logs + leaves the JSON in place (user can
    retry; we don't destroy their data on parse error).
    """
    if user_id != DEFAULT_TENANT_ID or not LEGACY_PROFILE_PATH.exists():
        return
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT 1 FROM user_profiles WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        if cur.fetchone() is not None:
            return
    try:
        data = json.loads(LEGACY_PROFILE_PATH.read_text(encoding="utf-8"))
        cv = CVData(**_filter_fields(data.get("cv_data", {}), CVData))
        prefs = UserPreferences(
            **_filter_fields(data.get("preferences", {}), UserPreferences)
        )
        save_profile(UserProfile(cv_data=cv, preferences=prefs), user_id)
        LEGACY_PROFILE_PATH.unlink()
        logger.info(
            "Hydrated legacy %s into user_profiles[%s] and deleted the JSON",
            LEGACY_PROFILE_PATH, user_id,
        )
    except Exception as e:  # noqa: BLE001 — preserve the legacy file on any failure
        logger.warning(
            "Legacy profile hydrate failed (file kept on disk): %s", e
        )
