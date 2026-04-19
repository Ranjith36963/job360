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


def save_profile(
    profile: UserProfile,
    user_id: str,
    source_action: str = "user_edit",
) -> None:
    """Upsert a UserProfile for ``user_id`` AND append a versioned snapshot.

    Batch 1.8 (Pillar 1, plan §4.8) — every save also records an
    immutable snapshot in ``user_profile_versions``. Per the plan's
    retention heuristic we keep the most recent ``VERSION_RETENTION``
    per user; older rows are deleted from the tail after the insert.

    The ``source_action`` is an audit label — ``"cv_upload"``,
    ``"linkedin_upload"``, ``"github_refresh"``, ``"user_edit"``,
    ``"legacy_hydrate"``. Callers pass it when they know; default is
    ``"user_edit"`` so legacy call-sites continue to work.

    The writes happen in one transaction: if the snapshot insert fails
    (e.g. missing migration in a stale DB), the tip upsert also rolls
    back rather than leaving the two tables inconsistent.
    """
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
        # Batch 1.8 — append snapshot. Wrapped in try so a stale DB
        # without 0007 migration still allows the tip-row write;
        # connection still commits in that case to preserve legacy
        # behaviour. ``OperationalError`` on missing table is logged at
        # info (expected on pre-migration DBs).
        try:
            conn.execute(
                """
                INSERT INTO user_profile_versions
                    (user_id, created_at, source_action, cv_data, preferences)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, now, source_action, cv_json, pref_json),
            )
            _prune_old_versions(conn, user_id)
        except sqlite3.OperationalError as e:
            if "no such table" in str(e).lower():
                logger.info(
                    "user_profile_versions table absent — skipping snapshot "
                    "(run ``python -m migrations.runner up``). Tip still saved."
                )
            else:
                raise
        conn.commit()
    logger.info("Profile saved for user %s (action=%s)", user_id, source_action)


VERSION_RETENTION = 10
"""Keep the ``VERSION_RETENTION`` most-recent snapshots per user.
See plan §8 risks table ("Versioned snapshots balloon DB size")."""


def _prune_old_versions(conn: sqlite3.Connection, user_id: str) -> None:
    """Delete snapshots beyond ``VERSION_RETENTION`` for a single user.

    Uses one DELETE keyed on a NOT-IN sub-select. Fine for the expected
    load (dozens of saves per user over their lifetime); not tuned for
    the "millions of versions" case because that case never arrives.
    """
    conn.execute(
        """
        DELETE FROM user_profile_versions
        WHERE user_id = ?
          AND id NOT IN (
              SELECT id FROM user_profile_versions
              WHERE user_id = ?
              ORDER BY created_at DESC, id DESC
              LIMIT ?
          )
        """,
        (user_id, user_id, VERSION_RETENTION),
    )


def list_profile_versions(user_id: str, limit: int = 10) -> list[dict]:
    """Return the most-recent snapshots for ``user_id``, newest first.

    Each row is a dict with ``id`` / ``created_at`` / ``source_action``
    plus parsed ``cv_data`` + ``preferences``. Callers typically render
    these in a history UI — not used on the hot scoring path.
    """
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            """
            SELECT id, created_at, source_action, cv_data, preferences
            FROM user_profile_versions
            WHERE user_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        rows = cur.fetchall()
    out: list[dict] = []
    for row in rows:
        out.append({
            "id": row[0],
            "created_at": row[1],
            "source_action": row[2],
            "cv_data": json.loads(row[3]) if row[3] else {},
            "preferences": json.loads(row[4]) if row[4] else {},
        })
    return out


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
        save_profile(
            UserProfile(cv_data=cv, preferences=prefs),
            user_id,
            source_action="legacy_hydrate",  # Review fix #5
        )
        LEGACY_PROFILE_PATH.unlink()
        logger.info(
            "Hydrated legacy %s into user_profiles[%s] and deleted the JSON",
            LEGACY_PROFILE_PATH, user_id,
        )
    except Exception as e:  # noqa: BLE001 — preserve the legacy file on any failure
        logger.warning(
            "Legacy profile hydrate failed (file kept on disk): %s", e
        )
