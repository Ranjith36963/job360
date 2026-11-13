"""Who approved what: the "AI reviewed" / "You approved" tags (owner 2026-11-13), derived from stored rows only.

Read-only and batched: one query per table for all the asked applications, always scoped by ``user_id`` (rules
#12/#25), test applications left out. Nothing is written and nothing new is stored. An item is
``{by: "ai"|"you", what: "cv"|"outreach"|"answers"|"submit", at, ref, ref_kind, reviewer}``.

* ``you`` needs a record written by the web session: the ``recorded_by`` COLUMN is ``web`` (payload text such as
  ``where`` or ``by`` is caller text and is never read). A chat approval (``where=chat``, a token's write) is not
  the owner's click and gets no tag.
* ``ai`` needs a record written by an agent (``recorded_by`` ``token:`` or ``agent:``): a ``cv_sent_on_review``
  (the server wrote it when the CV went on a strong review), a LinkedIn request or message that went without a
  Send (the rows ``sent_alone`` lists), an agent ``applied`` with no ``submit_approved``
  (by anyone, chat included) on the newest fill, or an agent fill whose answers are ``guessed`` / ``written`` with
  no ``answer_verified`` / ``answer_corrected`` (by anyone) on it.
* no record, no tag. Nothing pending is tagged. Fills and decisions are paired with ``agreement._groups`` and
  ``agreement._paired``; the CV mark is ``kit.cv_mark_matches``; the LinkedIn decision is ``sent_alone``'s.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from src.services.applications.agreement import _groups, _int, _paired
from src.services.applications.test_marker import TEST_ID_SQL

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

logger = logging.getLogger(__name__)

_TYPES = (
    "cv_seen", "submit_approved", "answer_verified", "answer_corrected", "form_filled", "applied",
    "cv_sent_on_review", "cv_reviewed",
)
_AI_SOURCES = ("guessed", "written")  # kit.ANSWER_SOURCES the owner has not seen: the rest were saved earlier
_REVIEWER_MAX = 40
_LINKEDIN = ("linkedin_request", "linkedin_message")


def _agent(by: Any) -> bool:
    return str(by).startswith(("token:", "agent:"))


def _item(
    by: str, what: str, at: Any, ref: int, kind: str = "event", reviewer: Optional[str] = None,
) -> dict[str, Any]:
    return {"by": by, "what": what, "at": str(at), "ref": ref, "ref_kind": kind, "reviewer": reviewer}


def _owner_marks(evs: list[dict[str, Any]], latest_cv: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """The CV part: ``you`` when the owner's click marks the CV that is now current, ``ai`` per sent-on-review."""
    from src.services.applications import kit  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    web = [e for e in evs if e["by"] == "web"]
    if latest_cv is not None:
        sha = kit.sha256_text(latest_cv["text"] or "")
        mine = [e for e in web if kit.cv_mark_matches(e, latest_cv["id"], sha)]
        if mine:
            top = max(mine, key=lambda e: e["id"])
            out.append(_item("you", "cv", top["at"], top["id"]))
    reviews = {e["id"]: e for e in evs if e["event_type"] == "cv_reviewed"}
    for e in evs:
        if e["event_type"] != "cv_sent_on_review" or not _agent(e["by"]):
            continue
        aid = e["payload"].get("artifact_id")
        if any(kit.cv_mark_matches(m, aid, None) for m in web):
            continue  # the owner marked that very CV too: the click wins, never an AI tag
        review = reviews.get(e["payload"].get("review_event_id"))
        name = str(review["payload"].get("reviewer") or "")[:_REVIEWER_MAX] if review else ""
        out.append(_item("ai", "cv", e["at"], e["id"], reviewer=name or None))
    return out


def _form_marks(evs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Submit and answers, both read against the newest fill (a decision of an older fill does not count)."""
    fills = {e["id"]: e["payload"] for e in evs if e["event_type"] == "form_filled"}
    groups = _groups(fills)
    grp = groups[max(fills)] if fills else None

    def bound(e: dict[str, Any]) -> dict[str, Any]:
        stamp = e["payload"].get("fingerprint")
        stamp = stamp if isinstance(stamp, dict) else {}
        fill = _int(e["payload"].get("form_filled_event_id")) or _int(stamp.get("form_filled_event_id"))
        return {"id": e["id"], "fill": fill}

    paired = [e for e in evs if grp is not None and _paired(bound(e), grp)]  # whoever recorded the decision
    web = [e for e in paired if e["by"] == "web"]
    out: list[dict[str, Any]] = []
    yes = [e for e in web if e["event_type"] == "submit_approved"]
    applied = [e for e in evs if e["event_type"] == "applied" and _agent(e["by"])]
    if yes:
        top = max(yes, key=lambda e: e["id"])
        out.append(_item("you", "submit", top["at"], top["id"]))
    elif applied and not any(e["event_type"] == "submit_approved" for e in paired):
        top = max(applied, key=lambda e: e["id"])
        if grp is None or top["id"] > grp["start"]:
            out.append(_item("ai", "submit", top["at"], top["id"]))
    decided = [e for e in web if e["event_type"] in ("answer_verified", "answer_corrected")]
    if decided:
        top = max(decided, key=lambda e: e["id"])
        out.append(_item("you", "answers", top["at"], top["id"]))
    elif grp is not None and not any(e["event_type"] in ("answer_verified", "answer_corrected") for e in paired):
        fill = next(e for e in evs if e["id"] == max(fills))
        answers = fill["payload"].get("answers")
        rows = answers if isinstance(answers, list) else []
        guessed = [a for a in rows if isinstance(a, dict) and a.get("source") in _AI_SOURCES]
        if guessed and _agent(fill["by"]):
            out.append(_item("ai", "answers", fill["at"], fill["id"]))
    return out


def _outreach_marks(rows: list[dict[str, Any]], app_id: Optional[int]) -> list[dict[str, Any]]:
    """One contact's rows (oldest first): ``ai`` for what ``sent_alone`` lists, ``you`` for a sent request or
    message that the owner's own web click on exactly it carried."""
    from src.services.applications import own_browser as ob  # noqa: PLC0415
    from src.services.applications.sent_alone import _linkedin_items  # noqa: PLC0415

    person = {"name": "", "application_id": app_id, "job_company": "", "job_title": ""}
    alone = {i["id"] for i in _linkedin_items(rows, person)}
    out = [
        _item("ai", "outreach", r["recorded_at"], r["id"], "outreach")
        for r in rows if r["id"] in alone and _agent(r["recorded_by"])
    ]
    for i, row in enumerate(rows):
        earlier, decisions = rows[:i], [r for r in rows[:i] if r["entry"] in (ob.APPROVED, ob.DECLINED)]
        if row["id"] in alone:
            continue
        if row["entry"] == ob.RELEASED and row.get("text") == ob.INVITED:
            last = decisions[-1] if decisions else None
        elif row["entry"] == "sent" and row["channel"] == "linkedin":
            messages = [r for r in earlier if r["entry"] == "message"]
            mine = [r for r in decisions if messages and str(r.get("text")) == str(messages[-1]["id"])]
            last = mine[-1] if mine else None
        else:
            continue
        if last is not None and last["entry"] == ob.APPROVED and last["recorded_by"] == "web":
            out.append(_item("you", "outreach", row["recorded_at"], row["id"], "outreach"))
    return out


async def approvals_for(db: JobDatabase, user_id: str, app_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Application id -> its approval items (an application with none is absent). Reads only."""
    from src.services.applications import kit  # noqa: PLC0415

    ids = sorted({int(a) for a in app_ids})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    test_sql, test_params = TEST_ID_SQL("a.id", "a.user_id")

    async def rows(sql: str, args: list[Any]) -> list[dict[str, Any]]:
        cur = await db._db.execute(sql, args)
        return [dict(r) for r in await cur.fetchall()]

    owned = {int(r["id"]) for r in await rows(
        f"SELECT a.id FROM applications a WHERE a.user_id = ? AND a.id IN ({marks}) AND NOT {test_sql}",  # noqa: S608
        [user_id, *ids, *test_params])}
    if not owned:
        return {}
    keep = sorted(owned)
    km = ",".join("?" for _ in keep)
    events: dict[int, list[dict[str, Any]]] = {a: [] for a in keep}
    for r in await rows(
        "SELECT id, application_id, event_type, payload, recorded_at, recorded_by FROM application_events "  # noqa: S608
        f"WHERE user_id = ? AND application_id IN ({km}) "
        f"AND event_type IN ({','.join('?' for _ in _TYPES)}) ORDER BY id",
        [user_id, *keep, *_TYPES],
    ):
        p = kit._json(r["payload"])
        events[int(r["application_id"])].append({
            "id": int(r["id"]), "event_type": r["event_type"], "payload": p if isinstance(p, dict) else {},
            "at": r["recorded_at"], "by": str(r["recorded_by"] or ""),
        })
    cvs = {int(r["application_id"]): r for r in await rows(
        "SELECT a.id, a.application_id, a.text FROM application_artifacts a WHERE a.kind = 'cv' "  # noqa: S608
        f"AND a.application_id IN ({km}) AND a.id = (SELECT MAX(b.id) FROM application_artifacts b "
        "WHERE b.application_id = a.application_id AND b.kind = 'cv')", keep)}
    contacts: dict[int, list[dict[str, Any]]] = {}
    for r in await rows(
        "SELECT o.id, o.contact_id, o.entry, o.channel, o.text, o.recorded_at, o.recorded_by, c.application_id "  # noqa: S608
        "FROM contact_outreach o JOIN application_contacts c ON c.id = o.contact_id "
        "WHERE o.user_id = ? AND c.user_id = ? "
        f"AND c.application_id IN ({km}) ORDER BY o.id", [user_id, user_id, *keep],
    ):
        contacts.setdefault(int(r["contact_id"]), []).append(r)
    out: dict[int, list[dict[str, Any]]] = {}
    for app_id in keep:
        found = _owner_marks(events[app_id], cvs.get(app_id)) + _form_marks(events[app_id])
        for rows_ in contacts.values():
            if int(rows_[0]["application_id"]) == app_id:
                found += _outreach_marks(rows_, app_id)
        if found:
            out[app_id] = found
    logger.info("approvals_read user=%s apps=%d tagged=%d codes=%s", user_id, len(keep), len(out),
                sorted({f"{i['by']}:{i['what']}" for v in out.values() for i in v}))
    return out
