"""What an "Auto for a while" grant covers (gap roadmap slice 30, top-10 gap #1, second half).

Slice 8 bound the user's manual yes to what they saw. The counted auto grant
(``auto_grant_count`` / ``auto_grant_until``, bulk-apply R1) was the other half: once
confirmed, every application became auto-submit-eligible, including ones queued after
the user looked, on sites they never saw.

Now, when the user confirms a grant on the web, the server SNAPSHOTS their batch queue
(the ``queued`` / ``unqueued`` events; still ``considering``) with each application's form
host: the host of its newest ``form_filled``, else the host of its apply link
(``applications.job_url``), else none. ``may_submit`` resolves auto only for an application
in the snapshot whose form host now equals the snapshotted one (``assistant_settings.
grant_covers``); anything else keeps the user's own mode and asks ``grant_scope``.

Storage: one ``profile_edits`` row at :data:`SCOPE_PATH`, written by
:func:`record_snapshot` inside the SAME transaction as the web write that set the grant
(``edits.record_edits`` is the one writer, so every web door - PATCH /profile, Confirm on
Needs you, Take back - snapshots). It is not an editable path: ``update_profile`` refuses
it for everyone and the overlay never applies it. Append-only like every overlay row.

A grant with no snapshot (confirmed before this shipped) keeps today's behaviour until its
window ends - grants are time-boxed to ``ASSISTANT_AUTO_GRANT_MAX_DAYS`` - but only when it
began before :data:`SCOPE_REQUIRED_FROM`; one that began after it without a snapshot covers
nothing (fail safe).

Logs: ``auto_grant_scoped`` - the user, how many applications and how many hosts. Never a
host or a URL.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from src.core import settings
from src.repositories import pgsync
from src.services.profile import assistant_settings as rules
from src.services.profile import edits as profile_edits
from src.utils.logger import get_audit_logger, safe_log_value

SCOPE_PATH = f"{rules.HEAD}.auto_grant_scope"
# Grants that began before this moment without a snapshot stay unscoped until they end; never
# move it later (same stance as kit.FORM_CHECK_REQUIRED_FROM).
SCOPE_REQUIRED_FROM = datetime(2026, 10, 20, tzinfo=timezone.utc)
# The most applications one snapshot holds (first in, first out); later ones simply ask.
SCOPE_MAX = 500

Scope = dict[int, Optional[str]]


def open_queue_hosts(conn: Any, user_id: str) -> Scope:
    """``{application_id: form host}`` for the user's open batch queue, first in first out: the same
    reading as ``spine.queued_at_by_app`` (newest surviving ``queued`` / ``unqueued`` by id; a
    corrected event is skipped) plus ``status = 'considering'``, as the batch list shows it. The host
    is the newest ``form_filled``'s, else the apply link's (``screen_form_url``), else ``None``.
    Scoped by ``user_id`` (rule #12)."""
    queued: dict[int, str] = {}
    for _id, app_id, event_type, recorded_at in conn.execute(
        "SELECT e.id, e.application_id, e.event_type, e.recorded_at FROM application_events e "
        "WHERE e.user_id = ? AND e.event_type IN ('queued', 'unqueued') "
        "AND NOT EXISTS (SELECT 1 FROM application_events c WHERE c.corrects_event_id = e.id) ORDER BY e.id ASC",
        (user_id,),
    ).fetchall():
        if event_type == "queued":
            queued[int(app_id)] = str(recorded_at)
        else:
            queued.pop(int(app_id), None)
    if not queued:
        return {}
    marks = ",".join("?" for _ in queued)
    links = {
        int(row[0]): row[1]
        for row in conn.execute(
            f"SELECT id, job_url FROM applications WHERE user_id = ? AND status = 'considering' "  # noqa: S608
            f"AND id IN ({marks})",
            (user_id, *queued),
        ).fetchall()
    }
    order = sorted(links, key=lambda i: (queued[i], i))[:SCOPE_MAX]
    if not order:
        return {}
    marks = ",".join("?" for _ in order)
    filled: dict[int, Optional[str]] = {}
    for app_id, payload in conn.execute(
        f"SELECT application_id, payload FROM application_events WHERE user_id = ? "  # noqa: S608
        f"AND event_type = 'form_filled' AND application_id IN ({marks}) ORDER BY id DESC",
        (user_id, *order),
    ).fetchall():
        if int(app_id) in filled:
            continue
        try:
            parsed = json.loads(payload) if isinstance(payload, str) else payload
        except ValueError:
            parsed = None
        host = parsed.get("host") if isinstance(parsed, dict) else None
        filled[int(app_id)] = host if isinstance(host, str) and host else None
    out: Scope = {}
    for app_id in order:
        host = filled.get(app_id)
        if host is None and app_id not in filled:
            link = links[app_id]
            host = rules.screen_form_url(link if isinstance(link, str) else None)
        out[app_id] = host
    return out


def summary(scope: Scope) -> dict[str, Any]:
    """``{count, hosts, no_host}``: how many applications, their distinct hosts (sorted) and how many
    have no host yet (those can never be auto under the grant)."""
    hosts = sorted({h for h in scope.values() if h})
    return {"count": len(scope), "hosts": hosts, "no_host": sum(1 for h in scope.values() if not h)}


def record_snapshot(conn: Any, user_id: str, set_at: str) -> Scope:
    """Snapshot the open queue as the scope of the grant the web write being recorded sets. Runs on
    the caller's connection INSIDE its transaction (``edits.record_edits``), so the grant and its
    scope land together or not at all; the row goes in AFTER the grant rows (a higher id)."""
    scope = open_queue_hosts(conn, user_id)
    value = {"apps": [{"id": app_id, "host": host} for app_id, host in scope.items()]}
    conn.execute(
        "INSERT INTO profile_edits (user_id, path, value, set_by, set_at, source, remember_consent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, SCOPE_PATH, json.dumps(value), profile_edits.WEB_ACTOR, set_at, profile_edits.SOURCE_USER_SAID, None),
    )
    shown = summary(scope)
    get_audit_logger().info(
        "auto_grant_scoped",
        extra={
            "event": "auto_grant_scoped", "user_id": safe_log_value(user_id), "applications": shown["count"],
            "hosts": len(shown["hosts"]), "no_host": shown["no_host"], "result": "ok",
        },
    )
    return scope


def _parse(raw: Any) -> Scope:
    """A stored snapshot as ``{id: host}``; anything unreadable reads as covering nothing."""
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        value = None
    apps = value.get("apps") if isinstance(value, dict) else None
    out: Scope = {}
    for item in apps if isinstance(apps, list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), int) and not isinstance(item.get("id"), bool):
            host = item.get("host")
            out[int(item["id"])] = host if isinstance(host, str) and host else None
    return out


def scope_for(conn: Any, user_id: str, began: Optional[str], began_id: Optional[int]) -> Optional[Scope]:
    """The scope of the grant that began at row ``began_id`` (``assistant_settings._grant_began``): the
    newest snapshot written with or after it. No snapshot: ``None`` (covers everything) for a grant
    that began before :data:`SCOPE_REQUIRED_FROM`, else ``{}`` (covers nothing)."""
    if began_id is None:
        return {}
    row = conn.execute(
        "SELECT value FROM profile_edits WHERE user_id = ? AND path = ? AND id > ? ORDER BY id DESC LIMIT 1",
        (user_id, SCOPE_PATH, began_id),
    ).fetchone()
    if row is not None:
        return _parse(row[0])
    try:
        legacy = rules.parse_iso_with_offset(str(began)) < SCOPE_REQUIRED_FROM
    except ValueError:
        legacy = False
    return None if legacy else {}


def scope_view(user_id: str, cfg: rules.EffectiveSettings, now: datetime) -> dict[str, Any]:
    """The ``auto_grant_scope`` block of the settings read model (web and MCP ``get_profile``):
    ``queue`` - what a grant confirmed NOW would cover (the confirm shows it); ``granted`` - what the
    grant in force covers (``None`` when none is in force, or it is ``unscoped``); ``unscoped`` - a grant
    from before snapshots, covering every application until it ends."""
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        queue = open_queue_hosts(conn, user_id)
        granted: Optional[dict[str, Any]] = None
        unscoped = False
        if cfg.auto_grant_count is not None and rules._grant_not_over(cfg.auto_grant_until, now):
            began, began_id = rules._grant_began(conn, user_id)
            scope = scope_for(conn, user_id, began, began_id)
            unscoped = scope is None
            granted = None if scope is None else summary(scope)
    return {"queue": summary(queue), "granted": granted, "unscoped": unscoped}
