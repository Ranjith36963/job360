"""Sign in once per site (diagnosis D, 2026-11-13): sign-in steps grouped by host.

Production had 62 queued jobs waiting on a sign-in / create-account / code step across ~23 hosts, and Needs you
showed one card per job, though one sign-in on a site clears every job on it. Here:

* ``group`` folds the jobs whose FIRST blocker is a ``site_step`` with a sign-in-family reason into one item
  per canonical host (``blocked._site``: lower case, no ``www.``): ``{host, word, app_ids}``, counted once;
* ``signed_in`` (the web-only ``POST /api/sites/{host}/signed-in``) records, for each of the user's open jobs
  with an open sign-in-family block on that host, ``unblocked`` {user_did_it, "signed in on <host>"} (which
  closes their asks through ``blocked.after_append``) and ``site_account`` {host}, then puts each job back on
  the queue as "Mark resolved" does. A CAPTCHA, a safety stop or "finish on the site" is never cleared here.

Always scoped by ``user.id`` (rules #12/#25). No MCP tool: an agent records ``unblocked`` per job itself.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from src.core import settings
from src.services.applications import blocked
from src.services.applications.spine import SpineError
from src.services.profile import assistant_settings as settings_rules
from src.utils.logger import get_audit_logger, safe_log_value

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.api.auth_deps import CurrentUser
    from src.repositories.database import JobDatabase

SIGN_IN_REASONS = frozenset({"login_needed", "account_creation", "otp_needed"})


def host_of(block: dict[str, Any], job_url: Any) -> str:
    """The site a sign-in block is on: its ``page_host``, else the job's own link; "" when neither reads."""
    return blocked._site(block.get("page_host")) or blocked._site(job_url)


def canonical_host(raw: str) -> str:
    """The route's ``{host}`` as ``group`` names it; ``SpineError`` 422 when it is not a host name."""
    host = settings_rules.parse_site_host(raw)
    if host is None:
        raise SpineError(422, "host must be a site's host name, such as jobs.example.com")
    return blocked._site(host)


def group(firsts: list[tuple[int, Mapping[str, Any]]]) -> tuple[list[dict[str, Any]], set[int]]:
    """``firsts``: (application id, first blocker) of the jobs that wait on the user, in page order. Returns
    (one ``{host, word, app_ids}`` per host, in the order its first job came; the grouped ids)."""
    items: dict[str, dict[str, Any]] = {}
    for app_id, first in firsts:
        ref = first.get("ref") or {}
        host = ref.get("signin_host")
        if first.get("kind") != "site_step" or not host:
            continue
        item = items.setdefault(host, {"host": host, "word": first["label"], "app_ids": []})
        if item["word"] != first["label"]:
            item["word"] = "sign in"  # mixed steps on one site: signing in is what comes first
        item["app_ids"].append(app_id)
    for item in items.values():
        item["app_ids"].sort()
    return list(items.values()), {i for item in items.values() for i in item["app_ids"]}


async def open_on_host(db: JobDatabase, user_id: str, host: str) -> list[int]:
    """The caller's open jobs with an open sign-in-family block on ``host`` (two queries, any count)."""
    closed = tuple(settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES)
    marks = ",".join("?" for _ in closed)
    cur = await db._db.execute(
        f"SELECT id, job_url FROM applications WHERE user_id = ? AND status NOT IN ({marks})",  # noqa: S608
        (user_id, *closed),
    )
    urls = {int(r["id"]): r["job_url"] for r in await cur.fetchall()}
    blocks = await blocked.open_blocks(db, user_id, sorted(urls))
    return sorted(
        app_id for app_id, p in blocks.items()
        if p.get("reason") in SIGN_IN_REASONS and host_of(p, urls[app_id]) == host
    )


async def signed_in(db: JobDatabase, user: CurrentUser, raw_host: str) -> dict[str, Any]:
    """The user signed in on ``raw_host``: clear every sign-in-family block there (see the module doc).
    409 when none waits. Returns ``{host, unblocked: [ids]}``; one audit line (ids and counts only)."""
    from src.services.applications import kit, queue_preflight, spine  # noqa: PLC0415 - kit imports this package
    from src.services.applications.authorship import actor_for  # noqa: PLC0415

    host = canonical_host(raw_host)
    app_ids = await open_on_host(db, user.id, host)
    if not app_ids:
        _log(user, [], 0, "nothing_blocked")
        raise SpineError(409, f"no job waits on a sign-in on {host}")
    note = f"signed in on {host}"[: blocked.NOTE_MAX_CHARS]
    requeued: list[int] = []
    for app_id in app_ids:
        now = datetime.now(timezone.utc).isoformat()
        block = await blocked.open_block(db, user.id, app_id)
        stored = blocked.check_payload(user, "unblocked", {"resolution": "user_did_it", "note": note}, "")
        await blocked.record(db, user, app_id, "unblocked", stored, occurred_at=now)
        account = await kit.check_kit_event(db, user, app_id, "site_account", {"host": host})
        result = await spine.append_event(
            db, user_id=user.id, application_id=app_id, event_type="site_account",
            payload=account, occurred_at=now, recorded_by=actor_for(user),
        )
        kit.log_kit_event(user, app_id, "site_account", account, int(result["event_id"]))
        reason = str((block or {}).get("reason") or "")
        if await queue_preflight.back_to_queue(db, user.id, app_id, reason, recorded_by=actor_for(user)):
            requeued.append(app_id)
    _log(user, app_ids, len(requeued), "ok")
    if requeued:
        from src.services.browser_agent import duty_trigger  # noqa: PLC0415

        await duty_trigger.ignite(user.id, ("batch",), "job_unblocked", requeued, db=db)
    return {"host": host, "unblocked": app_ids}


def _log(user: CurrentUser, app_ids: list[int], requeued: int, result: str) -> None:
    get_audit_logger().info(
        "site_signed_in",
        extra={"event": "site_signed_in", "user_id": safe_log_value(user.id), "application_ids": app_ids,
               "unblocked": len(app_ids), "requeued": requeued, "result": result},
    )
