"""S5d - the Ready to send cards. Real HTTP doors, real rows, VALUE asserts (rule #21)."""
from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings

FORM = "https://careers.example/apply/7"
CV = "Ada Lovelace\nSenior data engineer."
_AD = {
    "title": "Data Engineer", "company": "Northwind", "location": "London", "country": "GB", "remote": False,
    "apply_url": "https://northwind.example/careers/7", "description": "Build the pipelines.",
}
MEM = {"question": "Right to work", "answer": "citizen", "source": "memory", "key": "right_to_work.GB.work_authorization"}
FULL = [
    MEM,
    {"question": "Salary", "answer": "85000 GBP", "source": "memory", "key": "salary.GB"},
    {"question": "Full name", "answer": "Ada Lovelace", "source": "profile", "key": "contact.name"},
    {"question": "Why Northwind?", "answer": "I like pipelines.", "source": "written"},
]


def _e(path: str, value: Any) -> dict[str, Any]:
    return {"path": path, "value": value}


async def _memory(client: AsyncClient) -> None:
    resp = await client.patch("/api/profile", json={"edits": [
        _e("user_info.contact", {"email": "ada@example.com", "phone": "+44 7700 900123",
                                 "legal_first_name": "Ada", "legal_last_name": "Lovelace"}),
        _e("user_info.right_to_work", {"countries": [
            {"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False}]}),
        _e("preferences.salary_by_country", [{"country": "GB", "amount": 85000, "currency": "GBP", "period": "year"}]),
    ]})
    assert resp.status_code == 200, resp.text


async def _bring(client: AsyncClient, **over: Any) -> int:
    resp = await client.post("/api/jobs/bring", json={**_AD, **over})
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _fill(client: AsyncClient, app_id: int, answers: Any = None, expect: int = 201):
    payload: dict[str, Any] = {"form_url": FORM, "fields_count": 4}
    if answers is not None:
        payload["answers"] = answers
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": "form_filled", "payload": payload})
    assert resp.status_code == expect, resp.text
    return resp


async def _cv(client: AsyncClient, app_id: int, kind: str = "cv") -> None:
    assert (await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": kind, "text": CV})).status_code == 201


async def _ready(client: AsyncClient, **params: Any) -> dict[str, Any]:
    resp = await client.get("/api/ready-to-send", params=params or None)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _codes(card: dict[str, Any]) -> list[str]:
    return [f["code"] for f in card["flags"]]


@pytest.mark.asyncio
async def test_a_clean_card_carries_job_versions_answers_and_who_saved_them(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _memory(client)
        app_id = await _bring(client)
        await _cv(client, app_id)
        await _cv(client, app_id, "cover_letter")
        await _fill(client, app_id, FULL)
        body = await _ready(client)
        assert (body["total"], body["unflagged"], body["paused"]) == (1, 1, False)
        card = body["items"][0]
        assert (card["application_id"], card["job_company"], card["job_location"]) == (app_id, "Northwind", "London")
        assert card["job_country"] == "GB" and card["brought_by"] and card["brought_at"]
        assert (card["cv"]["version"], card["cover_letter"]["version"]) == (1, 1) and card["flags"] == []
        by_key = {a["key"]: a for a in card["answers"] if a["key"]}
        assert by_key["right_to_work.GB.work_authorization"]["saved_by"] == "web"
        assert by_key["right_to_work.GB.work_authorization"]["saved_at"]
        assert by_key["contact.name"]["saved_by"] is None, "a profile answer has no memory author"
        assert [a["source"] for a in card["answers"]] == ["memory", "memory", "profile", "written"]


@pytest.mark.asyncio
async def test_each_flag_fires_on_its_own_row_and_is_absent_otherwise(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _memory(client)
        none, guess, blank, gone, german, nocv, dup_a, dup_b = [
            await _bring(client, title=f"Role {n}", apply_url=f"https://co{n}.example/c/{n}",
                         **({"country": "DE"} if n == 5 else {}))
            for n in range(1, 9)
        ]
        for app_id in (none, guess, blank, gone, german, dup_a, dup_b):
            await _cv(client, app_id)
        await _fill(client, none)
        await _fill(client, guess, [*FULL, {"question": "Visa?", "answer": "no", "source": "guessed"}])
        await _fill(client, blank, [*FULL, {"question": "Middle name", "answer": "", "source": "written"}])
        await _fill(client, gone, [*FULL, {"question": "Fax", "answer": "1", "source": "memory", "key": "contact.fax"}])
        await _fill(client, german, FULL)
        await _fill(client, nocv, FULL)
        assert (await client.post(f"/api/applications/{dup_a}/receipt", json={"channel": "company_site"})).status_code == 201
        await _fill(client, dup_b, FULL)
        cards = {c["application_id"]: c for c in (await _ready(client))["items"]}
        assert _codes(cards[none]) == ["no_answers"]
        assert _codes(cards[guess]) == ["guessed"] and cards[guess]["flags"][0]["text"] == "An answer was guessed: Visa?"
        assert _codes(cards[blank]) == ["blank"] and cards[blank]["flags"][0]["text"] == "An answer is blank: Middle name"
        assert _codes(cards[gone]) == ["not_in_memory"]
        assert _codes(cards[nocv]) == ["no_cv"]
        missing = [f for f in cards[german]["flags"] if f["code"] == "missing"]
        assert {f["key"] for f in missing} == {
            "right_to_work.DE.work_authorization", "right_to_work.DE.needs_sponsorship", "salary.DE",
        }
        assert any(f["text"] == "Salary for DE not saved yet — answer it first" and f["country"] == "DE" for f in missing)
        assert dup_a not in cards, "a sent application is not ready"


@pytest.mark.asyncio
async def test_duplicate_flag_and_clean_rows_count_as_unflagged(authenticated_async_context):
    async with authenticated_async_context() as client:
        await _memory(client)
        sent = await _bring(client)
        await _cv(client, sent)
        assert (await client.post(f"/api/applications/{sent}/receipt", json={"channel": "company_site"})).status_code == 201
        twin = await _bring(client, title="Data Engineer II")
        clean = await _bring(client, title="Other", apply_url="https://other.example/c/1")
        for app_id in (twin, clean):
            await _cv(client, app_id)
            await _fill(client, app_id, FULL)
        body = await _ready(client)
        assert body["total"] == 2 and body["unflagged"] == 1
        flagged = [c for c in body["items"] if c["flags"]]
        assert [c["application_id"] for c in body["items"] if not c["flags"]] == [clean]
        assert len(flagged) == 1 and _codes(flagged[0]) == ["duplicate"]


def test_a_salary_range_or_one_amount_counts_as_saved():
    from src.services.applications.ready import _salary_saved

    def profile(*recs: dict[str, Any]) -> Any:
        return SimpleNamespace(preferences=SimpleNamespace(salary_by_country=list(recs)))

    assert _salary_saved(profile({"country": "DE", "amount": 70000}), "DE")
    assert _salary_saved(profile({"country": "de", "min": 70000, "max": 80000}), "DE")
    assert not _salary_saved(profile({"country": "GB", "amount": 70000}), "DE")
    assert not _salary_saved(profile({"country": "DE", "currency": "EUR"}), "DE")


@pytest.mark.asyncio
async def test_answers_are_validated_and_an_assistant_may_send_them(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        good = {"question": "Name", "answer": "Ada", "source": "profile"}
        await _fill(client, app_id, [{**good, "source": "invented"}], 422)
        await _fill(client, app_id, [{**good, "extra": "x"}], 422)
        await _fill(client, app_id, [{"question": "Name", "source": "profile"}], 422)
        await _fill(client, app_id, [{**good, "answer": "x" * (settings.USER_INFO_ANSWER_MAX_CHARS + 1)}], 422)
        await _fill(client, app_id, [{**good, "question": "q" * 301}], 422)
        await _fill(client, app_id, [{**good, "key": ""}], 422)
        await _fill(client, app_id, [good] * (settings.KIT_FORM_FIELDS_MAX + 1), 422)
        await _fill(client, app_id, "nope", 422)
        await _fill(client, app_id, [{**good, "question": "Na\x00me‮", "answer": "A\x07da"}])
        card = (await _ready(client))["items"][0]
        assert (card["answers"][0]["question"], card["answers"][0]["answer"]) == ("Name", "Ada")
        long = "Para one.\n\nPara two " + "y" * (settings.USER_INFO_ANSWER_MAX_CHARS - 30)
        assert len(long) > settings.PROFILE_EDIT_MAX_ITEM_CHARS
        await _fill(client, app_id, [{**good, "question": "Why\nus?", "answer": long}])
        card = (await _ready(client))["items"][0]
        assert (card["answers"][0]["question"], card["answers"][0]["answer"]) == ("Why us?", long), (
            "a long written answer keeps its line breaks; a question's line break is a space")
        token = (await client.post("/api/tokens", json={"name": "claude-code"})).json()["token"]
        from src.api.main import app

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
        ) as chat:
            await _fill(chat, app_id, [good])
            assert (await _ready(chat))["items"][0]["filled_by"] != "web"


@pytest.mark.asyncio
async def test_counts_only_scoping_pause_and_auth(authenticated_async_context):
    from fastapi.testclient import TestClient

    from src.api.main import app
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        first, second = await _bring(client), await _bring(client, title="Two", apply_url="https://two.example/c/2")
        await _fill(client, first, FULL)
        await _fill(client, second, FULL)
        counts = await _ready(client, limit=0)
        assert (counts["total"], counts["items"]) == (2, [])
        assert [c["application_id"] for c in (await _ready(client, application_id=first))["items"]] == [first]
        assert (await client.get("/api/ready-to-send", params={"application_id": 999999})).status_code == 404
        assert (await _ready(client, limit=1))["total"] == 2
        assert (await client.patch("/api/profile", json={"edits": [_e("assistant_settings.paused_until", "until_resumed")]})).status_code == 200
        assert (await _ready(client))["paused"] is True
        stranger = TestClient(app)
        email = "ready-stranger@example.com"
        assert stranger.post("/api/auth/register", json={"email": email, "password": "s3cretpassword"}).status_code == 201
        conn = pgsync.connect(str(settings.DB_PATH))
        conn.execute("UPDATE users SET email_verified_at = ? WHERE email = ?", ("2026-01-01T00:00:00Z", email))
        conn.commit()
        conn.close()
        lr = stranger.post("/api/auth/login", json={"email": email, "password": "s3cretpassword"})
        cookies = {"job360_session": str(lr.cookies.get("job360_session"))}
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", cookies=cookies) as other:
            assert (await _ready(other))["total"] == 0, "another user never sees it"
            assert (await other.get("/api/ready-to-send", params={"application_id": first})).status_code == 404
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as anon:
        assert (await anon.get("/api/ready-to-send")).status_code == 401


@pytest.mark.asyncio
async def test_the_log_carries_counts_never_answers(authenticated_async_context):
    records: list[dict[str, Any]] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(dict(record.__dict__))

    audit, handler, old = logging.getLogger("job360.audit"), Capture(), logging.getLogger("job360.audit").level
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    try:
        async with authenticated_async_context() as client:
            app_id = await _bring(client)
            await _fill(client, app_id, [{"question": "Secretq", "answer": "Zebrasecret", "source": "written"}])
            await _ready(client)
    finally:
        audit.removeHandler(handler)
        audit.setLevel(old)
    filled = [r for r in records if r.get("event") == "form_filled"]
    read = [r for r in records if r.get("event") == "ready_to_send_read"]
    assert filled[0]["answers_count"] == 1 and (read[0]["count"], read[0]["flagged"]) == (1, 1)
    assert not any("Zebrasecret" in str(v) or "Secretq" in str(v) for r in filled + read for v in r.values())


@pytest.mark.asyncio
async def test_send_refuses_a_stale_card_and_records_nothing(authenticated_async_context):
    """The yes names what the user saw: a newer CV or a newer fill is 409, nothing saved."""
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        await _cv(client, app_id)
        await _fill(client, app_id, FULL)
        seen = (await _ready(client))["items"][0]
        ids = {"artifact_id": seen["cv"]["artifact_id"], "form_filled_event_id": seen["form_filled_event_id"]}
        await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": CV + "\nEdited."})
        assert (await client.post(f"/api/applications/{app_id}/send/approve", params=ids)).status_code == 409
        fresh = (await _ready(client))["items"][0]
        await _fill(client, app_id, FULL)
        stale = {"artifact_id": fresh["cv"]["artifact_id"], "form_filled_event_id": fresh["form_filled_event_id"]}
        assert (await client.post(f"/api/applications/{app_id}/send/approve", params=stale)).status_code == 409
        events = [e["event_type"] for e in (await client.get(f"/api/applications/{app_id}")).json()["events"]]
        assert "submit_approved" not in events and "cv_seen" not in events
        now = (await _ready(client))["items"][0]
        ok = {"artifact_id": now["cv"]["artifact_id"], "form_filled_event_id": now["form_filled_event_id"]}
        assert (await client.post(f"/api/applications/{app_id}/send/approve", params=ok)).status_code == 201
        assert (await _ready(client))["total"] == 0
