"""Sent on its own: the log of what went out without the user (owner plan 2026-11-02, Needs-you item 2). A log,
not a to-do: it never counts on Needs you.

Answers and essays: autonomy step 4 (the Kits session) will send them behind the owner's on/off setting; until it
ships none is listed (this module never invents an item). LinkedIn (owner 2026-11-03): a request without a note that
went under ``outreach_send`` auto (kind ``linkedin_request``), and a LinkedIn message the user did not press Send on
for exactly that send (kind ``linkedin_message``: the message after an accepted request, ``auto_after_accept``).
Derived from the append-only ``contact_outreach`` rows (``own_browser``), so nothing new is stored. Each item is
``{id, application_id, job_company, job_title, kind, question, answer, sources: [{label, text}], sent_at,
reported_wrong}``. Always scoped by ``user_id`` (rules #12/#25).

CV (owner 2026-11-06, the safety net before ``cv_auto_approve``): kind ``cv`` is a CV that went to an employer
on a strong ``cv_reviewed`` instead of the user's look, read from the server-written ``cv_sent_on_review`` event
(cv_review.py) and the review it names. Its id is the NEGATIVE of that event id: LinkedIn items keep their
positive ``contact_outreach`` ids, so the two never collide and the id stays a whole number for old clients.
``reported_wrong`` = a ``cv_wrong_fact`` event names it. :func:`daily_sample` picks 5 CV items a day to check.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

_LIMIT = 200
SAMPLE_SIZE = 5


def today() -> date:
    """The day the sample is drawn for (UTC); tests patch it."""
    return datetime.now(timezone.utc).date()


def daily_sample(user_id: str, ids: list[int], day: date) -> list[int]:
    """Up to :data:`SAMPLE_SIZE` of ``ids``, the same all ``day`` for this user (seeded by user + date)."""
    seed = hashlib.sha256(f"{user_id}|{day.isoformat()}".encode()).hexdigest()
    pool = sorted(set(ids))
    return sorted(random.Random(seed).sample(pool, min(SAMPLE_SIZE, len(pool))))  # noqa: S311 - not security


def _payload(raw: Any) -> dict[str, Any]:
    from src.services.applications import kit  # noqa: PLC0415

    parsed = kit._json(raw)
    return parsed if isinstance(parsed, dict) else {}


def _review_sources(review: dict[str, Any]) -> list[dict[str, str]]:
    """The review's verdict and its line-coverage facts (codes and counts, never CV text)."""
    by = review.get("reviewer") or "a reviewer"
    out = [{"label": "Review", "text": f"{review.get('confidence', '?')} (by {by})"}]
    if review.get("content_lines") is not None:
        covered = f"{review.get('covered', 0)} of {review.get('content_lines')}"
        out.append({"label": "Lines covered", "text": f"{covered} CV lines quoted from your profile"})
    out.append({"label": "Lines checked", "text": str(review.get("lines", 0))})
    return out


async def _cv_items(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """Kind ``cv``: one per ``cv_sent_on_review`` event, only from stored rows."""
    from src.services.applications import cv_review, spine  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT id, event_type, application_id, payload, recorded_at FROM application_events WHERE user_id = ? "
        "AND event_type IN (?, ?) ORDER BY id DESC LIMIT ?",
        (user_id, cv_review.SENT_EVENT, cv_review.WRONG_FACT_EVENT, _LIMIT * 2),
    )
    rows = [dict(r) for r in await cur.fetchall()]
    reported = {
        _payload(r["payload"]).get("sent_event_id") for r in rows if r["event_type"] == cv_review.WRONG_FACT_EVENT
    }
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["event_type"] != cv_review.SENT_EVENT:
            continue
        app_id, p = int(r["application_id"]), _payload(r["payload"])
        app = await spine.get_owned_application(db, user_id, app_id)
        cur = await db._db.execute(
            "SELECT payload FROM application_events WHERE id = ? AND user_id = ? AND application_id = ? "
            "AND event_type = ?", (p.get("review_event_id"), user_id, app_id, cv_review.EVENT_TYPE),
        )
        review = await cur.fetchone()
        art = await spine.get_artifact(db, user_id, app_id, int(p["artifact_id"])) if p.get("artifact_id") else None
        if app is None or review is None or art is None:
            continue  # never invent: every part must be a stored row of this user
        out.append({
            "id": -int(r["id"]), "application_id": app_id, "job_company": str(app.get("job_company") or ""),
            "job_title": str(app.get("job_title") or ""), "kind": "cv", "question": "CV sent after a strong review",
            "answer": f"CV version {art.get('version_no')} (artifact {art['id']})",
            "sources": _review_sources(_payload(dict(review)["payload"])), "sent_at": str(r["recorded_at"]),
            "reported_wrong": int(r["id"]) in reported,
        })
    return out


def _linkedin_items(rows: list[dict[str, Any]], person: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure: the items one person's rows (oldest first) hold."""
    from src.services.applications import own_browser as ob  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    base = {k: person[k] for k in ("application_id", "job_company", "job_title")}
    for i, row in enumerate(rows):
        earlier = rows[:i]
        decisions = [r for r in earlier if r["entry"] in (ob.APPROVED, ob.DECLINED)]
        if row["entry"] == ob.RELEASED and row.get("text") == ob.INVITED:
            if decisions and decisions[-1]["entry"] == ob.APPROVED:
                continue  # the user pressed Send for this request
            kind, words = "linkedin_request", ""
            question = f"LinkedIn request without a note to {person['name']}"
        elif row["entry"] == "sent" and row["channel"] == "linkedin":
            messages = [r for r in earlier if r["entry"] == "message"]
            if not messages:
                continue
            mid = str(messages[-1]["id"])
            yes = [r for r in decisions if str(r.get("text")) == mid]
            invites = [r for r in earlier if r["entry"] == ob.RELEASED and r.get("text") == ob.INVITED]
            if yes and yes[-1]["entry"] == ob.APPROVED and not (invites and invites[-1]["id"] > yes[-1]["id"]):
                continue  # the user pressed Send on this message itself
            kind, words = "linkedin_message", str(messages[-1].get("text") or "")
            question = f"LinkedIn message to {person['name']}"
        else:
            continue
        out.append({
            **base, "id": int(row["id"]), "kind": kind, "question": question, "answer": words, "sources": [],
            "sent_at": str(row["recorded_at"]), "reported_wrong": False,
        })
    return out


async def list_items(db: JobDatabase, user_id: str) -> list[dict[str, Any]]:
    """The user's sent-on-its-own items, newest first."""
    from src.services.applications import contacts  # noqa: PLC0415
    from src.services.applications.spine import get_owned_application  # noqa: PLC0415

    cur = await db._db.execute(
        "SELECT DISTINCT contact_id FROM contact_outreach WHERE user_id = ? AND ((entry = 'sent' "
        "AND channel = 'linkedin') OR (entry = 'send_released' AND text = 'invited')) ORDER BY contact_id DESC LIMIT ?",
        (user_id, _LIMIT),
    )
    out: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        contact_id = int(dict(r)["contact_id"])
        base = await contacts.get_owned_contact(db, user_id, contact_id)
        if base is None:
            continue
        view = await contacts._full_contact_view(db, user_id, base, include_text=False)
        app_id = view.get("application_id")
        app = await get_owned_application(db, user_id, int(app_id)) if app_id is not None else None
        person = {
            "name": view["name"], "application_id": app_id,
            "job_company": str((app or {}).get("job_company") or view.get("company") or ""),
            "job_title": str((app or {}).get("job_title") or ""),
        }
        out.extend(_linkedin_items(await contacts._outreach_rows(db, contact_id), person))
    out.extend(await _cv_items(db, user_id))
    return sorted(out, key=lambda i: (i["sent_at"], i["id"]), reverse=True)


async def get_item(db: JobDatabase, user_id: str, item_id: int) -> Optional[dict[str, Any]]:
    """One of the user's items, or ``None`` (another user's item is ``None`` too)."""
    return next((i for i in await list_items(db, user_id) if i["id"] == item_id), None)


async def report_cv_wrong_fact(db: JobDatabase, user_id: str, item: dict[str, Any]) -> None:
    """The user's Wrong fact on a ``cv`` item: one append-only ``cv_wrong_fact`` (a repeat press writes nothing),
    then ``cv_auto_approve`` off at once through the web user's own settings write (on -> off is safer)."""
    from src.services.applications import cv_review, spine  # noqa: PLC0415
    from src.services.profile import assistant_settings as rules  # noqa: PLC0415
    from src.services.profile import edits as profile_edits  # noqa: PLC0415
    from src.services.profile.storage import load_profile  # noqa: PLC0415

    profile = load_profile(user_id)
    if profile is not None and profile.assistant_settings.cv_auto_approve is not False:
        profile_edits.record_edits(
            user_id, profile_edits.WEB_ACTOR, [(rules.CV_AUTO_APPROVE_PATH, False)], enforce_rate_limit=False,
        )
    if not item["reported_wrong"]:
        await spine.append_event(
            db, user_id=user_id, application_id=int(item["application_id"]), event_type=cv_review.WRONG_FACT_EVENT,
            payload={"sent_event_id": -int(item["id"])}, occurred_at=datetime.now(timezone.utc).isoformat(),
            recorded_by=profile_edits.WEB_ACTOR,
        )
