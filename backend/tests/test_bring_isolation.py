"""P0 2026-10-14 — what a user brings is what that user sees.

`jobs` is a shared catalog keyed on normalized (company, title) (rule #10), so
two users who bring the same title at the same company share ONE catalog row.
Before this fix the second user's paste was discarded: their application
snapshot, `get_job`, the apply kit and the receipt all carried the FIRST
user's ad text, link and location. And a same-user re-bring of a different
posting silently overwrote the existing application's country.

Pinned here:
  (a) two users, same title+company, different pastes -> each reads their own
      text / link / location through every per-user read (route, MCP
      `get_job`, application detail, kit, receipt), never the other's;
  (b) same user, a clearly different posting -> 409 naming what differs, and
      the existing application's facts are unchanged;
  (c) an exact re-bring stays idempotent (same application, one `brought`).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as api_deps

_A = {
    "title": "Machine Learning Engineer",
    "company": "Northwind Labs",
    "location": "London, UK",
    "apply_url": "https://boards.greenhouse.io/northwind/jobs/1111111",
    "description": "LONDON-AD-TEXT: build ranking models in our London office. Python, PyTorch.",
    "country": "GB",
}
_B = {
    "title": "Machine Learning Engineer",
    "company": "Northwind Labs",
    "location": "Berlin, Germany",
    "apply_url": "https://northwind.example/careers/jobs/2222222",
    "description": "BERLIN-AD-TEXT: build forecasting models in our Berlin hub. Go, Rust.",
    "country": "DE",
}


async def _second_user(email: str = "isolation-b@example.com") -> tuple[str, str]:
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
    user_id = sync_client.get("/api/auth/me").json()["id"]
    sync_client.close()
    assert cookie and user_id
    return cookie, user_id


def _client(cookie: str) -> AsyncClient:
    from src.api.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies={"job360_session": cookie})


async def _mcp_get_job(user_id: str, job_id: int) -> dict:
    pytest.importorskip("mcp")
    from src.api import mcp_server

    tool = mcp_server.build_server()._tool_manager.get_tool("get_job")
    mcp_server._current_user.set(mcp_server.CurrentUser(id=user_id, email="x@example.com"))
    try:
        return await tool.fn(job_id=job_id)
    finally:
        mcp_server._current_user.set(None)


async def _brought_events(application_id: int) -> int:
    db = await api_deps.get_db()
    cur = await db._conn.execute(
        "SELECT COUNT(*) FROM application_events WHERE application_id = ? AND event_type = 'brought'",
        (application_id,),
    )
    return int((await cur.fetchone())[0])


def _assert_is(view: dict, ad: dict, other: dict, *, desc_key: str, url_key: str, loc_key: str = "location") -> None:
    assert view[desc_key] == ad["description"], view
    assert view[url_key] == ad["apply_url"], view
    assert view[loc_key] == ad["location"], view
    assert other["description"] not in str(view)
    assert other["apply_url"] not in str(view)
    assert other["location"] not in str(view)


def _assert_kit(job: dict, ad: dict, other: dict) -> None:
    """The kit's job block carries no ad text — link, location, country."""
    assert job["apply_url"] == ad["apply_url"] and job["location"] == ad["location"], job
    assert job["country"] == ad["country"]
    assert other["apply_url"] not in str(job) and other["location"] not in str(job)


# ═══════════════════════════════════════════════════════════════════════════
# (a) two users, same title+company, different pastes
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_two_users_same_key_each_read_their_own_paste(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as a:
        ra = await a.post("/api/jobs/bring", json=_A)
        assert ra.status_code == 200, ra.text
        a_body = ra.json()
        cookie_b, user_b = await _second_user()
        async with _client(cookie_b) as b:
            rb = await b.post("/api/jobs/bring", json=_B)
            assert rb.status_code == 200, rb.text
            b_body = rb.json()

            # One shared catalog row (rule #10), two applications.
            assert b_body["job"]["id"] == a_body["job"]["id"]
            job_id = int(a_body["job"]["id"])
            assert b_body["application_id"] != a_body["application_id"]
            # `existing` is per user: B never brought it before. Whether A did
            # is not B's business.
            assert b_body["existing"] is False
            # The bring reply itself is B's own paste.
            _assert_is(b_body["job"], _B, _A, desc_key="description", url_key="apply_url")
            assert b_body["country"] == "DE"

            # B's per-user reads: route, MCP get_job, application detail, kit.
            got_b = await b.get(f"/api/applications/job/{job_id}")
            assert got_b.status_code == 200, got_b.text
            _assert_is(got_b.json(), _B, _A, desc_key="description", url_key="apply_url")
            mcp_b = await _mcp_get_job(user_b, job_id)
            _assert_is(mcp_b, _B, _A, desc_key="description", url_key="apply_url")
            assert mcp_b["country"] == "DE"
            detail_b = (await b.get(f"/api/applications/{b_body['application_id']}")).json()
            _assert_is(
                detail_b["job"], _B, _A, desc_key="job_description_snapshot", url_key="job_url", loc_key="job_location"
            )
            kit_b = await b.get(f"/api/applications/{b_body['application_id']}/kit")
            assert kit_b.status_code == 200, kit_b.text
            _assert_kit(kit_b.json()["job"], _B, _A)
            assert kit_b.json()["job"]["country"] == "DE"

            # B's receipt ("the ad as it read") is B's ad.
            rec_b = await b.post(f"/api/receipts/{job_id}", json={"channel": "email"})
            assert rec_b.status_code == 201, rec_b.text
            full_b = (await b.get(f"/api/receipts/{rec_b.json()['id']}")).json()
            assert full_b["job_description"] == _B["description"]
            assert full_b["job_apply_url"] == _B["apply_url"] and full_b["job_location"] == _B["location"]

        # A still reads A's own paste through every door.
        got_a = await a.get(f"/api/applications/job/{job_id}")
        _assert_is(got_a.json(), _A, _B, desc_key="description", url_key="apply_url")
        mcp_a = await _mcp_get_job(fixture_user_id, job_id)
        _assert_is(mcp_a, _A, _B, desc_key="description", url_key="apply_url")
        assert mcp_a["country"] == "GB"
        kit_a = await a.get(f"/api/applications/{a_body['application_id']}/kit")
        assert kit_a.status_code == 200, kit_a.text
        _assert_kit(kit_a.json()["job"], _A, _B)
        rec_a = await a.post(f"/api/receipts/{job_id}", json={})
        full_a = (await a.get(f"/api/receipts/{rec_a.json()['id']}")).json()
        assert full_a["job_description"] == _A["description"] and full_a["job_apply_url"] == _A["apply_url"]


@pytest.mark.asyncio
async def test_a_job_you_never_brought_cannot_be_adopted(authenticated_async_context, fixture_user_id):
    """The legacy receipt door and MCP `record_application` used to upsert an
    application onto ANY job_id and freeze the catalog row (someone else's
    paste) into the caller's receipt. Never brought = 404, nothing written."""
    async with authenticated_async_context() as a:
        job_id = int((await a.post("/api/jobs/bring", json=_A)).json()["job"]["id"])
    cookie_b, user_b = await _second_user("isolation-adopt@example.com")
    async with _client(cookie_b) as b:
        assert (await b.post(f"/api/receipts/{job_id}", json={})).status_code == 404
        assert (await b.get(f"/api/applications/job/{job_id}")).status_code == 404
        assert (await b.get("/api/applications")).json()["applications"] == []

    pytest.importorskip("mcp")
    from src.api import mcp_server

    tool = mcp_server.build_server()._tool_manager.get_tool("record_application")
    mcp_server._current_user.set(mcp_server.CurrentUser(id=user_b, email="x@example.com"))
    try:
        with pytest.raises(Exception) as err:
            await tool.fn(job_id=job_id)
        assert "not found" in str(err.value).lower()
    finally:
        mcp_server._current_user.set(None)
    db = await api_deps.get_db()
    cur = await db._conn.execute("SELECT COUNT(*) FROM applications WHERE user_id = ?", (user_b,))
    assert (await cur.fetchone())[0] == 0


# ═══════════════════════════════════════════════════════════════════════════
# (b) same user, a clearly different posting under the same title+company
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("change", "named"),
    [
        ({"apply_url": "https://boards.greenhouse.io/northwind/jobs/9999999"}, "apply_url"),
        ({"location": "New York, US"}, "location"),
        ({"country": "US"}, "country"),
        ({"location": "Austin", "country": "US", "apply_url": "https://northwind.example/jobs/77777"}, "location"),
    ],
)
async def test_same_user_different_posting_is_409_and_changes_nothing(
    authenticated_async_context, audit_capture, change, named
):
    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_A)).json()
        app_id = first["application_id"]
        before = (await client.get(f"/api/applications/{app_id}")).json()

        resp = await client.post("/api/jobs/bring", json={**_A, **change, "remote": True, "found_on": "linkedin"})
        assert resp.status_code == 409, resp.text
        detail = resp.json()["detail"]
        assert named in detail and str(app_id) in detail and "update_job" in detail
        # The agent is told what differs, never handed back the ad.
        assert _A["description"] not in detail

        after = (await client.get(f"/api/applications/{app_id}")).json()
        assert after["job"] == before["job"], "the existing application's facts must not move"
        assert after["job"]["country"] == "GB" and after["job"]["job_location"] == _A["location"]
        assert after["job"]["remote"] is None and after["job"]["found_on"] is None
        assert await _brought_events(app_id) == 1
        assert len((await client.get("/api/applications")).json()["applications"]) == 1

    refused = [r for r in audit_capture.records if r.get("event") == "job_bring_conflict"]
    assert refused and refused[-1]["application_id"] == app_id and named in refused[-1]["reason"]
    blob = repr(audit_capture.records)
    assert _A["description"] not in blob and _A["apply_url"] not in blob


@pytest.mark.asyncio
async def test_a_city_bracket_title_brings_the_other_posting_separately(authenticated_async_context):
    """The 409's own advice works: a disambiguated title is its own job."""
    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_A)).json()
        other = await client.post("/api/jobs/bring", json={**_B, "title": "Machine Learning Engineer (Berlin)"})
        assert other.status_code == 200, other.text
        assert other.json()["application_id"] != first["application_id"]
        assert other.json()["job"]["description"] == _B["description"]
        mine = (await client.get(f"/api/applications/job/{first['job']['id']}")).json()
        assert mine["description"] == _A["description"] and mine["location"] == _A["location"]


# ═══════════════════════════════════════════════════════════════════════════
# (c) an exact re-bring stays idempotent
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_exact_rebring_is_idempotent(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = (await client.post("/api/jobs/bring", json=_A)).json()
        # Same ad, the link carrying tracking noise and the location spelled
        # shorter: still the same posting, not a conflict.
        again = await client.post(
            "/api/jobs/bring",
            json={**_A, "location": "London", "apply_url": _A["apply_url"] + "?utm_source=linkedin#apply"},
        )
        assert again.status_code == 200, again.text
        body = again.json()
        assert body["existing"] is True
        assert body["application_id"] == first["application_id"]
        assert body["job"]["id"] == first["job"]["id"]
        assert body["job"]["description"] == _A["description"]
        assert body["country"] == "GB"
        assert await _brought_events(first["application_id"]) == 1
        assert len((await client.get("/api/applications")).json()["applications"]) == 1


@pytest.fixture
def audit_capture():
    import logging

    class _Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.records: list[dict] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append({"msg": record.getMessage(), **{k: v for k, v in record.__dict__.items()}})

    logger = logging.getLogger("job360.audit")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    yield handler
    logger.removeHandler(handler)
