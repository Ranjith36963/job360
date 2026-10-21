"""known_people - the people this user ALREADY stored at a job's company (outreach pack #4).

A pure join over the user's own application_contacts (+ the contact_edits overlay, + the
linked application's job_company for a contact with no company of its own), keyed by
contacts.company_key (rule #1). Never a lookup, a search or a ranking (product rules 4/5):
order is list_people's (newest contact first). People already on this application are left out.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core import settings
from src.services.applications import contacts

if TYPE_CHECKING:
    from src.repositories.database import JobDatabase

EMPTY: dict[str, Any] = {"count": 0, "people": [], "truncated": False}


def _slim(p: dict[str, Any]) -> dict[str, Any]:
    jobs = p.get("jobs") or []
    last_sent, last_reply = p.get("last_sent"), p.get("last_reply")
    return {
        "contact_ids": p["contact_ids"], "name": p["name"], "role": p.get("role") or "",
        "company": p.get("company") or ((jobs[0]["job_company"] or None) if jobs else None),
        "found_via": p.get("found_via"), "reachable": bool(p.get("reachable")),
        "application_ids": [int(j["application_id"]) for j in jobs],
        "message_count": int(p.get("message_count") or 0),
        "last_sent_at": last_sent["occurred_at"] if last_sent else None,
        "last_sent_channel": last_sent["channel"] if last_sent else None,
        "replied": bool(p.get("replied")),
        "last_reply_at": last_reply["occurred_at"] if last_reply else None,
    }


async def for_application(db: JobDatabase, user_id: str, app_row: dict[str, Any]) -> dict[str, Any]:
    if not app_row:
        return dict(EMPTY, people=[])
    company = str(app_row.get("job_company") or "")
    if not contacts.company_key(company):
        return dict(EMPTY, people=[])
    listed = await contacts.list_people(db, user_id, company=company, exclude_application_id=int(app_row["id"]))
    people = listed["people"] or []
    cap = settings.KNOWN_PEOPLE_MAX
    return {"count": len(people), "people": [_slim(p) for p in people[:cap]], "truncated": len(people) > cap}
