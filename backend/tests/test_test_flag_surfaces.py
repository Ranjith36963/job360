"""S7b (owner 2026-10-29): a test application is on NO user-facing list, and a new list cannot forget it.

Real Postgres, real HTTP doors. The same jobs are seeded twice, once real and once with
``bring_job(test=true)``: an ask, a CV, a filled form, a person with a drafted note, a lesson, a
receipt, an outcome suggestion and an interview. Every route in ``SURFACES`` must list the real ones
and none of the test ones. A companion test walks the GET routes under the list prefixes and fails
when one is in neither ``SURFACES`` nor ``NOT_A_LIST`` (like ``test_outbound_surfaces.py``).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import pytest
from httpx import AsyncClient

from src.core import settings
from tests._routes import route_table

FORM = "https://careers.{co}.example/apply/1"


async def _ok(resp: Any, code: int = 201) -> dict[str, Any]:
    assert resp.status_code == code, resp.text
    return resp.json()


async def _event(client: AsyncClient, app_id: int, event_type: str, **body: Any) -> dict[str, Any]:
    return await _ok(await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, **body}))


async def _seed(client: AsyncClient, co: str, test: bool) -> tuple[int, int]:
    """(an open job with an ask, CV, filled form, person and lesson; an applied job with a receipt,
    an outcome suggestion and an interview)."""
    ids = []
    for n in (1, 2):
        ad = {"title": f"Engineer {n}", "company": f"{co}{n}", "country": "GB", "description": "Build things.",
              "apply_url": f"https://{co.lower()}{n}.example/careers/1", "test": test}
        ids.append(int((await _ok(await client.post("/api/jobs/bring", json=ad), 200))["application_id"]))
    open_id, applied_id = ids
    await _ok(await client.post("/api/asks", json={"application_id": open_id, "question": "Notice period?"}))
    await _ok(await client.post(f"/api/applications/{open_id}/artifacts", json={"kind": "cv", "text": "Ada"}))
    await _event(client, open_id, "form_filled", payload={"form_url": FORM.format(co=co.lower()), "fields_count": 1,
                                                          "answers": []})
    person = await _ok(await client.post(f"/api/applications/{open_id}/contacts", json={"name": f"Rita {co}"}))
    cid = int(person["contact"]["id"] if "contact" in person else person["id"])
    await _ok(await client.post(f"/api/contacts/{cid}/outreach",
                                json={"entry": "message", "channel": "linkedin", "text": "Hi Rita"}))
    await _event(client, open_id, "lesson", detail="Ask about the base first.")
    await _ok(await client.post(f"/api/applications/{applied_id}/receipt", json={"channel": "company_site"}))
    src = {"message_id": f"<m-{co}@co.example>", "sender": "hr@co.example", "subject": "Update",
           "received_at": "2026-10-02T08:00:00+00:00"}
    await _event(client, applied_id, "outcome_suggested", source=src,
                 payload={"event_type": "rejected", "quote": "We will not proceed.", "why": "maybe"})
    when = (datetime.now(timezone.utc) + timedelta(days=3)).replace(microsecond=0).isoformat()
    await _event(client, applied_id, "interview_scheduled", scheduled_at=when)
    return open_id, applied_id


async def _get(client: AsyncClient, path: str, **params: Any) -> dict[str, Any]:
    return await _ok(await client.get(path, params=params), 200)


def _ids(rows: list[dict[str, Any]], key: str = "application_id") -> set[int]:
    return {int(r[key]) for r in rows if r.get(key) is not None}


async def _people(client: AsyncClient) -> set[int]:
    people = (await _get(client, "/api/people"))["people"]
    return {int(j["application_id"]) for p in people for j in p["jobs"]}


async def _receipts(client: AsyncClient) -> set[int]:
    rows = (await _get(client, "/api/receipts"))["receipts"]  # a summary carries no application id
    return {int((await _get(client, f"/api/receipts/{r['id']}"))["application_id"]) for r in rows}


async def _asks(client: AsyncClient) -> set[int]:
    body = await _get(client, "/api/asks")
    assert body["open_count"] == len(body["asks"]), "the number the user sees counts the list beside it"
    return _ids(body["asks"])


async def _whats_new(client: AsyncClient) -> set[int]:
    body = await _get(client, "/api/whats-new")
    return _ids(body["events"]) | _ids(body["open_asks"]) | _ids(body["applications"], "id")


async def _morning(client: AsyncClient) -> set[int]:
    tally = (await _get(client, "/api/morning-check"))["tally"]
    return {int(i["application_id"]) for bucket in tally.values() for i in bucket["items"]}


async def _due(client: AsyncClient) -> set[int]:
    due = (await _get(client, "/api/schedule/due"))["due"]
    return {int(a) for d in due for a in d.get("application_ids") or []}


async def _calendar(client: AsyncClient) -> set[int]:
    url = (await _ok(await client.post("/api/calendar-feed")))["url"]
    text = (await client.get(url.replace(settings.SITE_BASE_URL, ""))).text.replace("\r\n ", "")
    uids = [line[len("UID:app-"):] for line in text.split("\r\n") if line.startswith("UID:app-")]
    return {int(u.split("-", 1)[0]) for u in uids}


Reader = Callable[[AsyncClient], Awaitable[set[int]]]


def _rows(path: str, key: str, id_key: str = "application_id", **params: Any) -> Reader:
    async def read(client: AsyncClient) -> set[int]:
        return _ids((await _get(client, path, **params))[key], id_key)
    return read


# Every user-facing list (and the one calendar feed): each must leave a test application out.
SURFACES: dict[str, Reader] = {
    "/api/applications": _rows("/api/applications", "applications", "id", limit=200),
    "/api/applications/export": _rows("/api/applications/export", "applications", "id"),
    "/api/applications/lessons": _rows("/api/applications/lessons", "lessons"),
    "/api/applications/outcome-suggestions": _rows("/api/applications/outcome-suggestions", "suggestions"),
    "/api/receipts": _receipts,
    "/api/people": _people,
    "/api/asks": _asks,
    "/api/whats-new": _whats_new,
    "/api/morning-check": _morning,
    "/api/schedule/due": _due,
    "/api/ready-to-send": _rows("/api/ready-to-send", "items"),
    "/api/cv-to-approve": _rows("/api/cv-to-approve", "items"),
    "/api/calendar/{token}.ics": _calendar,
}

# GET routes under the list prefixes that are not a list of applications, and why.
NOT_A_LIST: dict[str, str] = {
    "/api/receipts/{receipt_id}": "one receipt by id (a test's record stays reachable)",
    "/api/applications/stats": "aggregates; already leaves tests out (stats reads test_marker)",
    "/api/applications/job/{job_id}": "one catalog job by id",
    "/api/applications/{application_id}": "one application by id (shows is_test)",
    "/api/applications/{application_id}/artifacts/{artifact_id}": "one artifact by id",
    "/api/applications/{application_id}/artifacts/{artifact_id}/diff": "one artifact by id",
    "/api/applications/{application_id}/alignment": "one application by id",
    "/api/applications/{application_id}/auto-email-check": "a gate for one application",
    "/api/applications/{application_id}/kit": "one application by id",
    "/api/applications/{application_id}/controls": "one application by id",
    "/api/applications/{application_id}/submit-check": "a gate for one application",
    "/api/applications/{application_id}/proof": "one application by id",
    "/api/applications/{application_id}/proof/screenshots/{screenshot_id}": "one screenshot by id",
    "/api/applications/{application_id}/calendar.ics": "one application by id",
    "/api/schedule/problems": "duty runs, no applications",
    "/api/schedule/status": "duty runs, no applications",
}

_PREFIXES = (
    "/api/applications", "/api/people", "/api/receipts", "/api/asks", "/api/whats-new", "/api/morning-check",
    "/api/schedule", "/api/ready-to-send", "/api/cv-to-approve",
)


def test_every_list_route_is_a_surface_or_named_not_a_list():
    from src.api.main import app

    table = route_table(app)
    gets = {r.path for r in table if "GET" in r.methods and r.path.startswith(_PREFIXES)}
    unclassified = gets - set(SURFACES) - set(NOT_A_LIST)
    assert not unclassified, f"new GET route(s) {sorted(unclassified)}: add each to SURFACES or NOT_A_LIST"
    assert not set(SURFACES) & set(NOT_A_LIST)
    every_get = {r.path for r in table if "GET" in r.methods}
    assert set(SURFACES) - every_get == set(), "a SURFACES row names a route that is gone"
    assert set(NOT_A_LIST) - every_get == set(), "a NOT_A_LIST row names a route that is gone"


@pytest.mark.asyncio
async def test_a_test_application_is_on_no_surface(authenticated_async_context, fixture_user_id):
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(UserProfile(cv_data=CVData(raw_text="Python engineer.", name="Ada")), fixture_user_id,
                 source_action="cv_upload")
    async with authenticated_async_context() as client:
        edit = {"edits": [{"path": "assistant_settings.people_hunt", "value": True}]}
        await _ok(await client.patch("/api/profile", json=edit), 200)
        real = set(await _seed(client, "Realco", test=False))
        tests = set(await _seed(client, "Testco", test=True))
        for path, read in SURFACES.items():
            seen = await read(client)
            assert seen & real, f"{path} listed none of the real applications: the check would be vacuous"
            assert seen & tests == set(), f"{path} lists a test application"


@pytest.mark.asyncio
async def test_open_count_leaves_tests_out_but_the_cap_and_doctor_count_every_ask(authenticated_async_context):
    from src.api import dependencies as api_deps
    from src.services.applications import asks

    async with authenticated_async_context() as client:
        ad = {"title": "Engineer", "company": "Capco", "description": "Build things.", "test": True}
        app_id = int((await _ok(await client.post("/api/jobs/bring", json=ad), 200))["application_id"])
        await _ok(await client.post("/api/asks", json={"application_id": app_id, "question": "Salary?"}))
        assert (await _get(client, "/api/asks"))["open_count"] == 0
        db = await api_deps.get_db()
        user_id = authenticated_async_context.fixture_user_id
        assert await asks.count_open_asks(db, user_id) == 1  # what the per-user cap and doctor read
        assert await asks.count_open_asks(db, user_id, exclude_test=True) == 0


@pytest.mark.asyncio
async def test_a_test_receipt_never_makes_a_site_known_to_the_kit(authenticated_async_context, fixture_user_id):
    """The kit reads the mark itself (kit._new_site); S7b leaves its logic as it is and pins it here."""
    from src.api import dependencies as api_deps
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        open_id, _applied = await _seed(client, "Kitco", test=True)
        await _ok(await client.post(f"/api/applications/{open_id}/receipt", json={"channel": "company_site"}))
        real = await _seed(client, "Otherco", test=False)
        db = await api_deps.get_db()
        assert await kit._new_site(db, fixture_user_id, real[0], "careers.kitco.example") is True
