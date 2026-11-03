"""Sent on its own: the log of what went out without the user (owner plan 2026-11-02, Needs-you item 2). A log,
not a to-do: it never counts on Needs you.

Answers and essays: autonomy step 4 (the Kits session) will send them behind the owner's on/off setting; until it
ships none is listed (this module never invents an item). LinkedIn (owner 2026-11-03): a request without a note that
went under ``outreach_send`` auto (kind ``linkedin_request``), and a LinkedIn message the user did not press Send on
for exactly that send (kind ``linkedin_message``: the message after an accepted request, ``auto_after_accept``).
Derived from the append-only ``contact_outreach`` rows (``own_browser``), so nothing new is stored. Each item is
``{id, application_id, job_company, job_title, kind, question, answer, sources: [{label, text}], sent_at,
reported_wrong}``. Always scoped by ``user_id`` (rules #12/#25).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - type-only
    from src.repositories.database import JobDatabase

_LIMIT = 200


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
    return sorted(out, key=lambda i: (i["sent_at"], i["id"]), reverse=True)


async def get_item(db: JobDatabase, user_id: str, item_id: int) -> Optional[dict[str, Any]]:
    """One of the user's items, or ``None`` (another user's item is ``None`` too)."""
    return next((i for i in await list_items(db, user_id) if i["id"] == item_id), None)
