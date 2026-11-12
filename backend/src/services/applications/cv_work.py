"""``cv_work``: the lean queue read for the scheduled CV write-and-review task (production 2026-11-10/11).

The task read the whole queue (3 pages, ~310 KB), the full profile and one 30-130 KB application per job before it
wrote a CV, so a Pro-plan run cleared 2-5. This returns, selected on the server, only the next queued rows that need
CV work, each with its capped ad and the ``view="review"`` block, plus ONE compact profile. Never ranks by fit
(rule 4); the order is the task's own: Job360's browser first, then the user's own, then queue position.

Needs CV work = on the open queue and either no CV yet, or the newest CV waits on the user's approval
(``approve_cv`` blocker), its review is not strong and it still has tries left. Fixed number of queries for the
queue, per-row reads only for the page. Read-only; every query is scoped by ``user_id`` (rules #12/#25).
"""
from __future__ import annotations

from typing import Any, Optional

from src.repositories.database import JobDatabase
from src.services.applications import cv_review, spine
from src.utils.logger import get_audit_logger, safe_log_value

AD_CAP = 6000  # characters of the frozen job ad per item
LIMIT_MAX, LIMIT_DEFAULT = 20, 10
_BROWSER_RANK = {"job360": 0, "own_browser": 1}
# The profile facts a CV writer needs, as profile_edits paths: no raw CV / LinkedIn / GitHub text.
_PROFILE_PATHS = ("cv_data.name", "cv_data.headline", "cv_data.location", "cv_data.summary", "cv_data.skills",
                  "cv_data.education", "cv_data.certifications", "cv_data.cv_positions", "cv_data.cv_projects",
                  "cv_data.links", "user_info.contact")
_CONTACT_KEYS = ("email", "phone", "linkedin_url", "address_city", "address_country")
# (the closed contact schema has no github or website key)
_ITEM_COLUMNS = ("id, job_title, job_company, job_country, job_description_snapshot, fit_recorded_at, "
                 "fit_jd_requirements")


def compact_profile(user_id: str) -> Optional[dict[str, Any]]:
    """The facts a CV writer needs (empty fields left out, rule #29), or ``None`` without a profile."""
    from src.services.profile import edits as profile_edits  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415 - heavy (rule #16)

    profile = load_profile(user_id)
    if profile is None:
        return None
    values = profile_edits.field_values(profile, _PROFILE_PATHS)
    out = {path.split(".", 1)[1]: v for path, v in values.items() if v not in ("", None, [], {})}
    contact = out.pop("contact", None)
    contact = {k: contact[k] for k in _CONTACT_KEYS if isinstance(contact, dict) and contact.get(k)}  # no DOB, street
    return {**out, **({"contact": contact} if contact else {})}


async def _needing_work(db: JobDatabase, user_id: str, queue_order: list[int]) -> list[int]:
    """The queued ids that need CV work, unordered. Reuses the blockers (``approve_cv``), the review state and
    the retry budget the review view counts, and ``queue_preflight.cv_rows`` for the rows with no CV yet."""
    from src.services.applications import blockers as blockers_service  # noqa: PLC0415 - imports spine
    from src.services.applications import queue_preflight  # noqa: PLC0415

    if not queue_order:
        return []
    no_cv = {int(r["id"]) for r in await queue_preflight.cv_rows(db, user_id)} & set(queue_order)
    skip = await queue_preflight.cv_skip_ids(db, user_id, queue_order)
    no_cv -= skip
    have_cv = [i for i in queue_order if i not in no_cv and i not in skip]
    states = await cv_review.states_for_apps(db, user_id, have_cv)
    candidates = [i for i in have_cv if states.get(i) != cv_review.STRONG]
    waiting = await blockers_service.blockers_for(db, user_id, candidates)
    approve = [i for i in candidates if any(b["kind"] == "approve_cv" for b in waiting.get(i, []))]
    tries = await cv_review.tries_left_for_apps(db, user_id, approve)
    return sorted(no_cv) + [i for i in approve if tries.get(i, 0) > 0]


async def cv_work(
    db: JobDatabase, user_id: str, *, limit: int = LIMIT_DEFAULT, after: Optional[int] = None,
    include_profile: bool = True,
) -> dict[str, Any]:
    """The next ``limit`` queued applications needing CV work after the cursor ``after`` (an application id);
    see the module docstring."""
    from src.services.applications import queue_preflight  # noqa: PLC0415
    from src.services.applications.fit_record import fit_view  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415 - heavy (rule #16)

    if not 1 <= int(limit) <= LIMIT_MAX:
        raise spine.SpineError(422, f"limit must be between 1 and {LIMIT_MAX}")
    queue_order, _queued_at = await spine.open_queue(db, user_id, load_profile(user_id))
    position = {app_id: n for n, app_id in enumerate(queue_order, start=1)}
    ids = await _needing_work(db, user_id, queue_order)
    cursor = after if after is not None and after in position else None
    browsers = await queue_preflight.queue_browsers(db, user_id, ids + ([cursor] if cursor is not None else []))

    def kind(app_id: int) -> str:
        return str(browsers.get(app_id, {}).get("kind") or "")

    def sort_key(i: int) -> tuple[int, int]:
        return _BROWSER_RANK.get(kind(i), 2), position[i]

    ids.sort(key=sort_key)
    # The cursor is the last id the caller saw: continue with what sorts AFTER it NOW (that row may have left
    # the list since). A cursor that is no longer queued has no place to sort: start over, never skip a row.
    start = sum(1 for i in ids if cursor is not None and sort_key(i) <= sort_key(cursor))
    page = ids[start:start + limit]
    rows: dict[int, dict[str, Any]] = {}
    if page:
        cur = await db._db.execute(
            f"SELECT {_ITEM_COLUMNS} FROM applications WHERE user_id = ? "  # noqa: S608 - constant columns
            f"AND id IN ({','.join('?' for _ in page)})",
            (user_id, *page),
        )
        rows = {int(r["id"]): dict(r) for r in await cur.fetchall()}
        newest = await cv_review.newest_cv_ids(db, user_id, page)
        cur = await db._db.execute(
            "SELECT id, created_at FROM application_artifacts WHERE user_id = ? "  # noqa: S608 - placeholders only
            f"AND id IN ({','.join('?' for _ in newest) or '0'})",
            (user_id, *newest.values()),
        )
        made = {int(r[0]): r[1] for r in await cur.fetchall()}
        saved = {app_id: made[art_id] for app_id, art_id in newest.items() if art_id in made}
    else:
        saved = {}
    items = []
    for app_id in page:
        row = rows[app_id]
        view = await cv_review.review_view(db, user_id, app_id) or {}
        ad = str(row.get("job_description_snapshot") or "")
        reqs = (fit_view(row) or {}).get("jd_requirements") or []
        items.append({
            "application_id": app_id, "job_title": row.get("job_title") or "",
            "job_company": row.get("job_company") or "", "country": row.get("job_country") or None,
            "browser": kind(app_id) or None, "ad": ad[:AD_CAP], "ad_truncated": len(ad) > AD_CAP,
            "jd_requirements": [str(r.get("text") or "") if isinstance(r, dict) else str(r) for r in reqs if r],
            "review": view.get("review"),
            "cv_saved_at": str(saved[app_id]) if app_id in saved else None,
        })
    out: dict[str, Any] = {
        "items": items, "total_needing_work": len(ids),
        "next_after": page[-1] if page and start + limit < len(ids) else None,
    }
    if include_profile:
        out["profile"] = compact_profile(user_id)
    get_audit_logger().info(
        "cv_work", extra={"event": "cv_work", "user_id": safe_log_value(user_id), "queued": len(queue_order),
                          "needing": len(ids), "returned": len(items), "after": safe_log_value(after), "result": "ok"})
    return out
