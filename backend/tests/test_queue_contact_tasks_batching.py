"""The queue's contact tasks cost a FIXED number of statements, and answer exactly as the per-contact path did.

2026-11-17, production: ``GET /api/applications?queued=true&limit=50`` on a large account spent 515-669 ms in
``q_outreach``. ``own_browser._contact_tasks`` read, PER contact, the contact row, its outreach rows (twice), the
over-limit notes (twice) and the edit history: 597 statements for one request. Now one read each for the whole
page. The identity test keeps the old per-contact loop as its oracle (``_old_contact_tasks``, copied from
before the change) and compares whole task lists, under both ``outreach_send`` values and two fixed dates.
Helpers are copied, never imported from another test module.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

import pytest
from httpx import AsyncClient

from src.api.routes import browser as browser_routes
from src.core import settings
from src.repositories import pgsync
from src.services.applications import contacts, own_browser
from src.services.applications.own_browser import APPROVED, DECLINED, INVITED, QUEUE_MAX, RELEASED

_AD = {"company": "Northwind", "description": "Build things.", "title": "Engineer",
       "apply_url": "https://northwind.example/c/1"}
NOTE = "Hi, I would love to talk about the role."
LONG = "x" * 264
DATES = [datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc), datetime(2026, 12, 30, 23, 59, tzinfo=timezone.utc)]


async def _app(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _person(client: AsyncClient, app_id: int, n: int, state: str | None = "not_connected",
                  approve: bool = True, text: str = NOTE) -> tuple[int, int]:
    added = await client.post(
        f"/api/applications/{app_id}/contacts",
        json={"name": f"Priya {n}", "role": "Recruiter", "linkedin_url": f"https://www.linkedin.com/in/priya{n}"},
    )
    assert added.status_code == 201, added.text
    cid = int(added.json()["contact"]["id"])
    if state:
        patched = await client.patch(f"/api/contacts/{cid}", json={"linkedin_state": state})
        assert patched.status_code == 200, patched.text
    saved = await client.post(
        f"/api/applications/{app_id}/artifacts",
        json={"kind": "outreach", "contact_id": cid, "channel": "linkedin", "text": text},
    )
    assert saved.status_code == 201, saved.text
    mid = int(saved.json()["artifact_id"])
    if approve:
        sent = await client.post(f"/api/contacts/{cid}/own-browser-send", json={"message_id": mid, "decision": "approve"})
        assert sent.status_code == 200, sent.text
    return cid, mid


def _sql(statement: str, params: tuple[Any, ...] = ()) -> None:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute(statement, params)
        conn.commit()


async def _old_contact_tasks(db: Any, user_id: str, now: datetime) -> list[dict[str, Any]]:
    """The per-contact loop exactly as it was before batching (the oracle); only the profile read is shared."""
    from src.services.profile import assistant_settings as rules
    from src.services.profile.models import UserProfile
    from src.services.profile.storage import load_profile

    auto = rules.effective((load_profile(user_id) or UserProfile()).assistant_settings).outreach_send == (
        rules.OUTREACH_SEND_AUTO
    )
    cur = await db._db.execute(
        "SELECT a.contact_id, a.id AS newest FROM contact_outreach a WHERE a.user_id = ? AND a.entry = ? "
        "AND a.id = (SELECT MAX(d.id) FROM contact_outreach d WHERE d.contact_id = a.contact_id "
        "            AND d.entry IN (?, ?)) "
        "AND a.text = CAST((SELECT MAX(m.id) FROM contact_outreach m WHERE m.contact_id = a.contact_id "
        "                   AND m.entry = 'message') AS TEXT) "
        "AND EXISTS (SELECT 1 FROM contact_outreach m2 WHERE m2.contact_id = a.contact_id "
        "            AND CAST(m2.id AS TEXT) = a.text AND m2.channel = 'linkedin') "
        "AND NOT EXISTS (SELECT 1 FROM contact_outreach s WHERE s.contact_id = a.contact_id "
        "                AND s.entry IN ('sent', 'reply') AND s.id > CAST(a.text AS INTEGER)) "
        "ORDER BY a.id ASC LIMIT ?",
        (user_id, APPROVED, APPROVED, DECLINED, QUEUE_MAX),
    )
    ids = [int(dict(r)["contact_id"]) for r in await cur.fetchall()]
    cur = await db._db.execute(
        "SELECT m.contact_id FROM contact_outreach m WHERE m.user_id = ? AND m.entry = 'message' "
        "AND m.channel = 'linkedin' AND m.id = (SELECT MAX(x.id) FROM contact_outreach x "
        "                                       WHERE x.contact_id = m.contact_id AND x.entry = 'message') "
        "AND NOT EXISTS (SELECT 1 FROM contact_outreach s WHERE s.contact_id = m.contact_id AND s.id > m.id "
        "                AND s.entry IN ('sent', 'reply', 'skipped', ?, ?)) "
        "AND (? OR EXISTS (SELECT 1 FROM contact_outreach i WHERE i.contact_id = m.contact_id AND i.entry = ? "
        "                  AND i.text = ?)) ORDER BY m.id ASC LIMIT ?",
        (user_id, APPROVED, DECLINED, auto, RELEASED, INVITED, QUEUE_MAX),
    )
    ids += [c for c in (int(dict(r)["contact_id"]) for r in await cur.fetchall()) if c not in ids]
    out: list[dict[str, Any]] = []
    for contact_id in ids:
        base = await contacts.get_owned_contact(db, user_id, contact_id)
        if base is None:
            continue
        rows = await contacts._outreach_rows(db, contact_id)
        task = own_browser.derive(rows, now, auto, note_ok=True)
        if task is None or task["state"] not in ("queued", "in_progress"):
            continue
        if own_browser.follow_up_held(rows, task["message_id"], now):
            continue
        if await contacts.over_limit_notes(db, user_id, [contact_id]):
            continue
        view = await contacts._full_contact_view(db, user_id, base)
        state, invited = view.get("linkedin_state"), own_browser.invite_facts(rows) is not None
        if state == "pending" or (state == "not_connected" and invited):
            continue
        if task.get("auto") and not (state == "connected" if invited else state == "not_connected"):
            continue
        text = view["outreach"]["messages"][-1]["text"] if view["outreach"]["messages"] else ""
        route = own_browser.routing.browser_for(linkedin=True)
        out.append({
            "task_id": f"contact:{contact_id}", "kind": "linkedin_outreach",
            "what": f"Send your LinkedIn message to {view['name']}", "url": view.get("linkedin_url") or "",
            "contact_id": contact_id, "application_id": view.get("application_id"), "name": view["name"],
            "message_id": task["message_id"], "draft_text": text, "draft_chars": len(text),
            "linkedin_state": state,
            "why_own_browser": own_browser.WHY_WORDS["default" if not task.get("auto") else "auto"],
            "owner_yes": not task.get("auto"),
            "no_note": state != "connected" and own_browser.is_after_connect(rows, task["message_id"]),
            "claim": ({"session": task["claimed_by"], "until": task["claim_until"], "actor": task["claimed_actor"]}
                      if task["state"] == "in_progress" else None),
            "browser": own_browser.routing.label(route),
            "approved_at": task["approved_at"],
        })
    return out


async def _statements(db: Any, call: Any) -> tuple[int, Any]:
    real = db._db.execute
    seen: list[str] = []

    async def counting(sql: str, params: Any = ()) -> Any:
        seen.append(sql)
        return await real(sql, params)

    db._db.execute = counting
    try:
        out = await call()
    finally:
        db._db.execute = real
    return len(seen), out


@pytest.mark.asyncio
async def test_contact_task_statements_do_not_grow_with_the_number_of_contacts(authenticated_async_context):
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        app_id = await _app(client)
        for n in range(3):
            await _person(client, app_id, n)
    async with browser_routes._db() as db:
        small, tasks3 = await _statements(db, lambda: own_browser._contact_tasks(db, user_id, DATES[0]))
    async with authenticated_async_context() as client:
        for n in range(3, 12):
            await _person(client, app_id, n)
    async with browser_routes._db() as db:
        large, tasks12 = await _statements(db, lambda: own_browser._contact_tasks(db, user_id, DATES[0]))
    assert (len(tasks3), len(tasks12)) == (3, 12)
    assert large == small, f"{small} statements for 3 contacts but {large} for 12: a per-contact read is back"


@pytest.mark.asyncio
@pytest.mark.parametrize("auto", [False, True])
@pytest.mark.parametrize("now", DATES)
async def test_batched_tasks_equal_the_per_contact_path(authenticated_async_context, auto, now):
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        app_id = await _app(client)
        ids: dict[str, int] = {}
        ids["plain"], _ = await _person(client, app_id, 1)
        ids["edited"], _ = await _person(client, app_id, 2)
        edit = await client.patch(f"/api/contacts/{ids['edited']}", json={"name": "Priya Renamed", "role": "Lead"})
        assert edit.status_code == 200, edit.text
        ids["unknown"], _ = await _person(client, app_id, 3, state=None)
        ids["connected"], _ = await _person(client, app_id, 4, state="connected")
        ids["sent"], _ = await _person(client, app_id, 5)
        ids["declined"], m6 = await _person(client, app_id, 6)
        ids["pending"], _ = await _person(client, app_id, 7, state="pending")
        ids["over"], _ = await _person(client, app_id, 8)
        ids["over_connected"], _ = await _person(client, app_id, 9, state="connected")
        ids["draft_only"], _ = await _person(client, app_id, 10, approve=False)
        ids["claimed"], _ = await _person(client, app_id, 11)
        ids["no_outreach"] = int((await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Quiet", "linkedin_url": "https://www.linkedin.com/in/q"},
        )).json()["contact"]["id"])
        mark = await client.post(f"/api/contacts/{ids['sent']}/outreach", json={"entry": "sent", "channel": "linkedin"})
        assert mark.status_code == 201, mark.text
        no = await client.post(f"/api/contacts/{ids['declined']}/own-browser-send",
                               json={"message_id": m6, "decision": "decline"})
        assert no.status_code == 200, no.text
        if auto:
            for path in ("assistant_settings.outreach_send",):
                up = await client.patch("/api/profile", json={"edits": [{"path": path, "value": "auto"}]})
                assert up.status_code == 200, up.text
    for key in ("over", "over_connected"):  # a draft stored over the 200-character note limit
        _sql("UPDATE contact_outreach SET text = ? WHERE contact_id = ? AND entry = 'message'", (LONG, ids[key]))
    # A live claim at the first date (a stale one at the second): rows read in real time are not date-stable.
    _sql("INSERT INTO contact_outreach (user_id, contact_id, entry, channel, text, occurred_at, recorded_at, "
         "recorded_by) VALUES (?, ?, ?, 'linkedin', 'desk', ?, ?, 'web')",
         (user_id, ids["claimed"], own_browser.CLAIMED, *("2026-11-17T08:55:00+00:00",) * 2))
    # Another user's contact: never visible, even when MY outreach rows point at its id.
    _sql("INSERT INTO users (id, email, password_hash) VALUES ('other-user', 'other@example.com', 'x')")
    foreign = pgsync.connect(str(settings.DB_PATH))
    row = foreign.execute(
        "INSERT INTO application_contacts (user_id, application_id, name, linkedin_url, added_by, created_at) "
        "VALUES ('other-user', ?, 'Foreign', 'https://www.linkedin.com/in/f', 'web', '2026-11-01T00:00:00+00:00') "
        "RETURNING id", (app_id,),
    ).fetchone()
    foreign.commit()
    foreign.close()
    fid = int(row[0])
    stamp = ("2026-11-01T00:00:00+00:00",) * 2
    _sql("INSERT INTO contact_outreach (user_id, contact_id, entry, channel, text, version_no, occurred_at, "
         "recorded_at, recorded_by) VALUES (?, ?, 'message', 'linkedin', 'hi', 1, ?, ?, 'web')", (user_id, fid, *stamp))
    _sql("INSERT INTO contact_outreach (user_id, contact_id, entry, channel, text, occurred_at, recorded_at, "
         "recorded_by) SELECT ?, ?, ?, 'linkedin', CAST(id AS TEXT), ?, ?, 'web' FROM contact_outreach "
         "WHERE contact_id = ? AND entry = 'message'", (user_id, fid, APPROVED, *stamp, fid))

    async with browser_routes._db() as db:
        new = await own_browser._contact_tasks(db, user_id, now)
        old = await _old_contact_tasks(db, user_id, now)
    assert new == old
    got = {t["contact_id"] for t in new}
    assert fid not in got and ids["no_outreach"] not in got
    assert ids["plain"] in got and ids["edited"] in got and ids["over_connected"] in got
    assert not got & {ids["sent"], ids["declined"], ids["pending"], ids["over"]}
    assert any(t["name"] == "Priya Renamed" for t in new) and any(t["claim"] for t in new) is (now == DATES[0])
    assert (ids["draft_only"] in got) is auto


@pytest.mark.asyncio
async def test_the_batch_readers_equal_the_per_contact_readers(authenticated_async_context):
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        app_id = await _app(client)
        a, _ = await _person(client, app_id, 1)
        b, _ = await _person(client, app_id, 2, state=None, approve=False)
        up = await client.patch(f"/api/contacts/{a}", json={"role": "CTO", "notes": "met at a talk"})
        assert up.status_code == 200, up.text
    wanted = [a, b, 987654]  # an edited one, an unedited one, an unknown one
    async with browser_routes._db() as db:
        owned = await contacts.get_owned_contacts(db, user_id, wanted)
        assert owned == {c: await contacts.get_owned_contact(db, user_id, c) for c in (a, b)}
        assert await contacts.get_owned_contacts(db, "someone-else", wanted) == {}
        rows = await contacts._outreach_rows_by_contact(db, wanted)
        assert rows == {c: await contacts._outreach_rows(db, c) for c in wanted} and rows[987654] == []
        hist = await contacts._edit_histories(db, user_id, wanted)
        assert hist == {c: await contacts._edit_history(db, user_id, c) for c in wanted}
        assert hist[b] != hist[a] or hist[a]
        views = await contacts.contact_views_batch(
            db, user_id, [owned[a], owned[b]], rows, await contacts.over_limit_notes(db, user_id, [a, b]))
        for c in (a, b):  # the view holds time-stamped derived fields: compare the stable ones whole
            one = await contacts._full_contact_view(db, user_id, owned[c])
            assert views[c] == one
        assert await contacts.contact_views_batch(db, user_id, [], {}, {}) == {}


@pytest.mark.asyncio
async def test_batched_linkedin_states_equal_the_per_contact_read(authenticated_async_context):
    user_id = authenticated_async_context.fixture_user_id
    async with authenticated_async_context() as client:
        app_id = await _app(client)
        flipped, _ = await _person(client, app_id, 1, state="connected")
        patched = await client.patch(f"/api/contacts/{flipped}", json={"linkedin_state": "not_connected"})
        assert patched.status_code == 200, patched.text  # the newest of two different saved states
        never, _ = await _person(client, app_id, 2, state=None)
        once, _ = await _person(client, app_id, 3, state="pending")
    ids = [flipped, never, once, 987654]
    async with browser_routes._db() as db:
        batch = await contacts._linkedin_states_now(db, user_id, ids)
        single = {c: await contacts._linkedin_state_now(db, user_id, c) for c in ids}
        assert batch == single
        assert batch[flipped] == "not_connected" and batch[never] == "unknown" and batch[once] == "pending"
        assert await contacts._linkedin_states_now(db, user_id, []) == {}


@pytest.mark.asyncio
async def test_the_profile_read_runs_off_the_event_loop(authenticated_async_context, monkeypatch):
    from src.services.profile import storage

    user_id = authenticated_async_context.fixture_user_id
    seen: list[int] = []
    real = storage.load_profile

    def recorded(*args: Any, **kwargs: Any) -> Any:
        seen.append(threading.get_ident())
        return real(*args, **kwargs)

    monkeypatch.setattr(storage, "load_profile", recorded)
    async with browser_routes._db() as db:
        await own_browser._contact_tasks(db, user_id, DATES[0])
    assert seen, "the sync profile read was never called"
    assert threading.get_ident() not in seen, "load_profile ran on the event-loop thread"
