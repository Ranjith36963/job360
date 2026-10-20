"""Feature 8 (G088): ``source.thread_id`` on an event + the read-only ``match_email``.

Real Postgres via ``authenticated_async_context``. Nothing is fetched, so no HTTP mocking.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

_NORTHWIND = {
    "title": "Data Engineer",
    "company": "Northwind Ltd",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/1",
    "description": "Build the pipelines. Python, dbt, Snowflake.",
}
_CONTOSO = {
    "title": "Platform Engineer",
    "company": "Contoso",
    "location": "London",
    "apply_url": "https://contoso.example/jobs/9",
    "description": "Own the platform. Kubernetes, Terraform, Go.",
}
_ACME = {
    "title": "Robotics Engineer",
    "company": "Acme Robotics",
    "location": "Leeds",
    "apply_url": "https://job-boards.greenhouse.io/acme/jobs/4739199005",
    "description": "Control software for robot arms.",
}
_CAREERS = {
    "title": "Backend Engineer",
    "company": "Zephyr Systems",
    "location": "Leeds",
    "apply_url": "https://careers.example.com/jobs/7",
    "description": "Services in Python.",
}


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> int:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return resp.json()["application_id"]


async def _second_user_session_cookie(email: str = "second@example.com") -> str:
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.core import settings
    from src.repositories import pgsync

    sync_client = TestClient(app)
    r = sync_client.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"})
    assert r.status_code == 201, r.text
    conn = pgsync.connect(str(settings.DB_PATH))
    conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
    conn.commit()
    conn.close()
    lr = sync_client.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
    assert lr.status_code == 200, lr.text
    cookie = sync_client.cookies.get("job360_session")
    assert cookie
    sync_client.close()
    return cookie


def _session_client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie})


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict[str, Any]] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(dict(record.__dict__))


@pytest.fixture
def audit_capture():
    logger = logging.getLogger("job360.audit")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)


async def _event(client: AsyncClient, app_id: int, event_type: str = "replied", **source: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"event_type": event_type}
    if source:
        body["source"] = source
    resp = await client.post(f"/api/applications/{app_id}/events", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _match(client: AsyncClient, **body: Any):
    return await client.post("/api/applications/match-email", json=body)


async def _ok(client: AsyncClient, **body: Any) -> dict[str, Any]:
    resp = await _match(client, **body)
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.mark.asyncio
async def test_thread_id_round_trips_on_the_source(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, _NORTHWIND)
        await _event(
            client, app_id, message_id="msg-northwind-1", thread_id="thr-18c2f9",
            sender="Priya Rao <priya@northwind.example>", subject="Re: Data Engineer",
        )
        await _event(client, app_id, "note", message_id="msg-northwind-2", sender="x@northwind.example", subject="Hi")
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        by_msg = {e["source"]["message_id"]: e["source"] for e in detail["events"] if e.get("source")}
        assert by_msg["msg-northwind-1"]["thread_id"] == "thr-18c2f9"
        assert by_msg["msg-northwind-2"]["thread_id"] is None
        from src.core import settings
        from src.repositories import pgsync

        conn = pgsync.connect(str(settings.DB_PATH))
        row = conn.execute(
            "SELECT source_thread_id FROM application_events WHERE source_message_id = ?", ("msg-northwind-1",)
        ).fetchone()
        conn.close()
        assert row[0] == "thr-18c2f9"
        exported = await client.get("/api/applications/export")
        assert exported.status_code == 200
        assert "thr-18c2f9" in exported.text


@pytest.mark.asyncio
async def test_thread_id_is_validated_like_the_other_source_fields(authenticated_async_context):
    from src.core import settings

    async with authenticated_async_context() as client:
        app_id = await _bring(client, _NORTHWIND)
        url = f"/api/applications/{app_id}/events"

        def body(**src: Any) -> dict[str, Any]:
            return {"event_type": "replied", "source": {"message_id": "m-1", **src}}

        r = await client.post(url, json=body(thread_id="thr\n1"))
        assert r.status_code == 422 and "thread_id must not contain control characters" in r.text
        too_long = "t" * (settings.APPLICATION_EVENT_SOURCE_THREAD_ID_MAX_CHARS + 1)
        r = await client.post(url, json=body(thread_id=too_long))
        assert r.status_code == 422 and "APPLICATION_EVENT_SOURCE_THREAD_ID_MAX_CHARS" in r.text
        r = await client.post(url, json=body(thread="x"))
        assert r.status_code == 422


@pytest.mark.asyncio
async def test_same_thread_names_the_application_and_its_event(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        await _bring(client, _CONTOSO)
        await _event(client, a, message_id="msg-a-1", thread_id="thr-A-77")
        data = await _ok(client, thread_id="thr-A-77")
        assert data["truncated"] is False
        assert len(data["candidates"]) == 1
        cand = data["candidates"][0]
        assert cand["application_id"] == a
        assert cand["reasons"] == ["same_thread"]
        assert cand["contact_ids"] == []
        assert len(cand["thread_event_ids"]) == 1


@pytest.mark.asyncio
async def test_contact_email_uses_the_current_email(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _bring(client, _NORTHWIND)
        b = await _bring(client, _CONTOSO)
        added = await client.post(
            f"/api/applications/{b}/contacts", json={"name": "Maya Chen", "email": "old.maya@contoso.example"}
        )
        assert added.status_code == 201, added.text
        cid = added.json()["contact_id"]
        upd = await client.patch(f"/api/contacts/{cid}", json={"email": "maya.chen@contoso.example"})
        assert upd.status_code == 200, upd.text
        data = await _ok(client, sender="Maya Chen <Maya.Chen@Contoso.example>")
        assert [c["application_id"] for c in data["candidates"]] == [b]
        assert "contact_email" in data["candidates"][0]["reasons"]
        assert data["candidates"][0]["contact_ids"] == [cid]
        assert (await _ok(client, sender="old.maya@contoso.example"))["candidates"] == []
        cold = await client.post("/api/contacts", json={"name": "Lee", "email": "cold.lee@contoso.example"})
        assert cold.status_code == 201, cold.text
        assert (await _ok(client, sender="cold.lee@contoso.example"))["candidates"] == []


@pytest.mark.asyncio
async def test_sender_domain_in_email_senders(authenticated_async_context):
    async with authenticated_async_context() as client:
        acme = await _bring(client, _ACME)
        await _bring(client, _CAREERS)
        data = await _ok(client, sender="no-reply@us.greenhouse-mail.io")
        assert [c["application_id"] for c in data["candidates"]] == [acme]
        cand = data["candidates"][0]
        assert cand["reasons"] == ["sender_domain_in_email_senders"]
        assert cand["email_senders"] == ["greenhouse.io", "greenhouse-mail.io"]
        assert (await _ok(client, sender="x@evil-greenhouse.io"))["candidates"] == []


@pytest.mark.asyncio
async def test_company_in_subject_is_a_whole_word(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _bring(client, _NORTHWIND)
        data = await _ok(client, subject="Your application at Northwind - next steps")
        assert [c["reasons"] for c in data["candidates"]] == [["company_in_subject"]]
        assert (await _ok(client, subject="Northwindy weekly digest"))["candidates"] == []


@pytest.mark.asyncio
async def test_reasons_are_facts_in_fixed_order_no_verdict(authenticated_async_context):
    async with authenticated_async_context() as client:
        older = await _bring(client, _NORTHWIND)
        newer = await _bring(client, {**_NORTHWIND, "title": "Analytics Engineer", "apply_url": "https://northwind.example/careers/2"})
        await _event(client, older, message_id="m-old", thread_id="thr-both")
        added = await client.post(
            f"/api/applications/{older}/contacts", json={"name": "Priya Rao", "email": "priya@northwind.example"}
        )
        assert added.status_code == 201, added.text
        data = await _ok(
            client, sender="Priya Rao <priya@northwind.example>", thread_id="thr-both", subject="Northwind interview"
        )
        assert set(data) == {"candidates", "truncated"}
        by_id = {c["application_id"]: c for c in data["candidates"]}
        assert by_id[older]["reasons"] == ["same_thread", "contact_email", "company_in_subject"]
        assert by_id[newer]["reasons"] == ["company_in_subject"]
        assert set(by_id[older]) == {
            "application_id", "job_title", "job_company", "status", "reasons",
            "thread_event_ids", "contact_ids", "email_senders",
        }
        assert [c["application_id"] for c in data["candidates"]] == [newer, older]


@pytest.mark.asyncio
async def test_closed_applications_are_still_candidates(authenticated_async_context):
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        await _event(client, a, "withdrawn")
        data = await _ok(client, subject="Northwind")
        assert [c["application_id"] for c in data["candidates"]] == [a]
        assert data["candidates"][0]["status"] == "withdrawn"


@pytest.mark.asyncio
async def test_needs_at_least_one_input(authenticated_async_context):
    async with authenticated_async_context() as client:
        for body in ({}, {"sender": "  ", "subject": ""}):
            r = await _match(client, **body)
            assert r.status_code == 422
            assert "give at least one of sender, thread_id or subject" in r.text
        assert (await _match(client, **{"from": "x"})).status_code == 422


@pytest.mark.asyncio
async def test_cap_truncates_newest_first(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "MATCH_EMAIL_MAX_CANDIDATES", 2)
    async with authenticated_async_context() as client:
        ids = [
            await _bring(client, {**_NORTHWIND, "title": f"Data Engineer {n}", "apply_url": f"https://northwind.example/careers/{n}"})
            for n in (11, 12, 13)
        ]
        data = await _ok(client, subject="Northwind")
        assert [c["application_id"] for c in data["candidates"]] == [ids[2], ids[1]]
        assert data["truncated"] is True


@pytest.mark.asyncio
async def test_another_users_facts_never_match(authenticated_async_context):
    async with authenticated_async_context() as client:
        a1 = await _bring(client, _NORTHWIND)
        await _event(client, a1, message_id="m-shared", thread_id="thr-shared-9")
        cookie = await _second_user_session_cookie()
        async with _session_client(cookie) as client2:
            a2 = await _bring(client2, _NORTHWIND)
            added = await client2.post(
                f"/api/applications/{a2}/contacts", json={"name": "Maya", "email": "maya.chen@contoso.example"}
            )
            assert added.status_code == 201, added.text
            assert (await _ok(client2, thread_id="thr-shared-9"))["candidates"] == []
            assert [c["application_id"] for c in (await _ok(client2, subject="Northwind"))["candidates"]] == [a2]
        assert (await _ok(client, sender="maya.chen@contoso.example"))["candidates"] == []


@pytest.mark.asyncio
async def test_match_email_writes_nothing(authenticated_async_context):
    from src.core import settings
    from src.repositories import pgsync

    def snapshot() -> tuple[Any, ...]:
        conn = pgsync.connect(str(settings.DB_PATH))
        try:
            n = conn.execute("SELECT COUNT(*) FROM application_events").fetchone()[0]
            apps = conn.execute(
                "SELECT id, status, last_event_at, inbox_checked_at FROM applications ORDER BY id"
            ).fetchall()
            return (n, [tuple(r) for r in apps])
        finally:
            conn.close()

    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        await _event(client, a, message_id="m-w", thread_id="thr-w")
        before = snapshot()
        for body in ({"thread_id": "thr-w"}, {"subject": "Northwind"}, {"sender": "a@northwind.example"}):
            await _ok(client, **body)
        assert snapshot() == before


@pytest.mark.asyncio
async def test_logs_ids_never_the_sender_thread_or_subject(authenticated_async_context, audit_capture):
    async with authenticated_async_context() as client:
        a = await _bring(client, _NORTHWIND)
        await _event(client, a, message_id="m-l", thread_id="thr-private-55")
        await _ok(
            client, sender="priya.secret@northwind.example", thread_id="thr-private-55", subject="Offer for Priya"
        )
        mine = [r for r in audit_capture.records if r.get("event") == "match_email"]
        assert mine and mine[-1]["candidate_count"] >= 1
        assert all(isinstance(i, int) for i in mine[-1]["application_ids"])
        before = len(audit_capture.records)
        r = await _match(client, thread_id="zq-secret\n1")
        assert r.status_code == 422
        for rec in audit_capture.records:
            text = str(rec)
            for secret in ("priya.secret", "northwind.example", "thr-private-55", "Offer for Priya"):
                assert secret not in text
        assert not any("zq-secret" in str(rec) for rec in audit_capture.records[before:])


@pytest.mark.asyncio
async def test_mcp_tool_returns_the_route_result(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m

    user = m.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc"
    )
    tools = m.build_server()._tool_manager
    token = m._current_user.set(user)
    try:
        async with authenticated_async_context() as client:
            a = await _bring(client, _NORTHWIND)
            await _event(client, a, message_id="m-t", thread_id="thr-A-77")
            http = await _ok(client, thread_id="thr-A-77")
            via_tool = await tools.get_tool("match_email").fn(thread_id="thr-A-77")
            assert via_tool == http
            with pytest.raises(Exception) as exc:
                await tools.get_tool("match_email").fn()
            assert "give at least one" in str(exc.value)
    finally:
        m._current_user.reset(token)


def test_recipes_point_to_match_email():
    from src.api.routes.recipes import load_recipe

    feedback = load_recipe("feedback").text
    assert "match_email" in feedback and "facts, not a verdict" in feedback
    daily = load_recipe("daily").text
    assert "match_email" in daily and len(daily.splitlines()) <= 70
    rules = load_recipe("rules").text
    assert "thread_id" in rules and "match_email" in rules


@pytest.mark.asyncio
async def test_company_in_subject_folds_and_bounds_unicode(authenticated_async_context):
    straße = {**_NORTHWIND, "company": "Straße GmbH", "apply_url": "https://strasse.example/jobs/1"}
    nord = {**_CONTOSO, "company": "Nord", "apply_url": "https://nord.example/jobs/2"}
    async with authenticated_async_context() as client:
        await _bring(client, straße)
        await _bring(client, nord)
        data = await _ok(client, subject="Your application at Strasse")
        assert [c["job_company"] for c in data["candidates"]] == ["Straße GmbH"]
        assert (await _ok(client, subject="News from Nordéa"))["candidates"] == []


@pytest.mark.asyncio
async def test_validation_error_log_never_carries_the_input(authenticated_async_context, caplog):
    with caplog.at_level(logging.WARNING):
        async with authenticated_async_context() as client:
            r = await client.post("/api/applications/match-email", json={"from": "priya.secret@northwind.example"})
            assert r.status_code == 422
    assert any(rec.getMessage() == "validation_error" for rec in caplog.records)
    assert "priya.secret" not in "".join(f"{rec.getMessage()} {rec.__dict__}" for rec in caplog.records)
