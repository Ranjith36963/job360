"""Outreach pack #13 - a variant label on outreach messages, user-level
templates (``user_templates``) and stats ``by_outreach_variant``.

Helpers are COPIED, never imported (a cross-module fixture import breaks
per-test schema isolation).
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "Data Engineer",
    "company": "Northwind",
    "location": "Remote",
    "apply_url": "https://northwind.example/careers/7",
    "description": "Build the pipelines. Python, dbt, Snowflake. Fully remote.",
}
STEM = "0077_outreach_variants"


async def _bring(client: AsyncClient) -> int:
    resp = await client.post("/api/jobs/bring", json=_AD)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _linked_contact(client: AsyncClient, app_id: int, name: str = "Ann Lee") -> int:
    r = await client.post(f"/api/applications/{app_id}/contacts", json={"name": name, "email": f"{uuid.uuid4().hex[:6]}@x.example"})
    assert r.status_code == 201, r.text
    return int(r.json()["contact"]["id"])


async def _cold_contact(client: AsyncClient, name: str = "Cold Cat") -> int:
    r = await client.post("/api/contacts", json={"name": name, "company": "Northwind", "email": f"{uuid.uuid4().hex[:6]}@x.example"})
    assert r.status_code == 201, r.text
    return int(r.json()["contact"]["id"])


async def _out(client: AsyncClient, cid: int, entry: str, **body: Any):
    return await client.post(
        f"/api/contacts/{cid}/outreach", json={"entry": entry, "channel": "email", **body}
    )


def _mcp_user(fixture_user_id: str):
    from src.api import mcp_server

    return mcp_server.CurrentUser(
        id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc",
    )


async def _template_rows(client: AsyncClient) -> int:
    resp = await client.get("/api/templates")
    assert resp.status_code == 200, resp.text
    return len(resp.json()["templates"])


@pytest.mark.asyncio
async def test_label_on_linked_message_round_trips(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client)
        cid = await _linked_contact(client, app_id)
        r = await client.post(
            f"/api/applications/{app_id}/artifacts",
            json={"kind": "outreach", "contact_id": cid, "channel": "email", "text": "Hi Ann",
                  "label": "  Short-Ask v2 "},
        )
        assert r.status_code == 201, r.text
        assert r.json()["label"] == "Short-Ask v2"
        person = await client.get(f"/api/people?contact_id={cid}")
        assert person.json()["person"]["outreach"]["messages"][0]["label"] == "Short-Ask v2"


@pytest.mark.asyncio
async def test_label_on_direct_outreach_route_and_refused_on_sent(authenticated_async_context):
    async with authenticated_async_context() as client:
        cid = await _cold_contact(client)
        ok = await _out(client, cid, "message", text="Hello", label="warm-intro", channel="linkedin")
        assert ok.status_code == 201, ok.text
        assert ok.json()["outreach"]["label"] == "warm-intro"

        bad = await _out(client, cid, "sent", label="warm-intro")
        assert bad.status_code == 422
        assert "only meaningful on a message version" in bad.text

        too_long = await _out(client, cid, "message", text="Hello", label="x" * 101)
        assert too_long.status_code == 422

        # Pydantic must not out-strict the service: 100 chars plus padding is fine after strip().
        padded = await _out(client, cid, "message", text="Hello", label="x" * 100 + " ")
        assert padded.status_code == 201, padded.text

        outreach = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]["outreach"]
        assert len(outreach["messages"]) == 2
        assert outreach["sent"] == []


@pytest.mark.asyncio
async def test_mcp_cold_branch_carries_label(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m

    tools = m.build_server()._tool_manager
    token = m._current_user.set(_mcp_user(fixture_user_id))
    try:
        async with authenticated_async_context() as client:
            cid = await _cold_contact(client)
            out = await tools.get_tool("save_artifact").fn(
                kind="outreach", contact_id=cid, channel="email", text="Hi", label="Cold-B"
            )
            assert out["label"] == "Cold-B"
            person = (await client.get(f"/api/people?contact_id={cid}")).json()["person"]
            assert person["outreach"]["messages"][0]["label"] == "Cold-B"
    finally:
        m._current_user.reset(token)


@pytest.mark.asyncio
async def test_template_versions_and_get_profile_reads_newest(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    tools = m.build_server()._tool_manager
    save = tools.get_tool("save_artifact").fn
    token = m._current_user.set(_mcp_user(fixture_user_id))
    try:
        async with authenticated_async_context() as client:
            before = await tools.get_tool("get_profile").fn()
            assert before["profile_exists"] is False
            assert "outreach_templates" not in before

            v1 = await save(kind="outreach_template", label="Referral Ask", text="Hi {first_name}, v1")
            assert v1["version_no"] == 1
            v2 = await save(kind="outreach_template", label="referral ask ", text="Hi {first_name}, v2")
            assert v2["version_no"] == 2
            cold = await save(kind="outreach_template", label="Cold Pitch", text="Cold {company}")
            assert cold["version_no"] == 1

            save_profile(UserProfile(cv_data=CVData(raw_text="Jane Doe")), fixture_user_id, source_action="cv_upload")
            prof = await tools.get_tool("get_profile").fn()
            tpl = prof["outreach_templates"]
            assert len(tpl) == 2
            assert {t["label"] for t in tpl} == {"referral ask", "Cold Pitch"}
            referral = next(t for t in tpl if t["label"] == "referral ask")
            assert referral["text"] == "Hi {first_name}, v2"

            listed = await client.get("/api/templates?label=Referral%20Ask")
            assert listed.status_code == 200, listed.text
            assert [t["version_no"] for t in listed.json()["templates"]] == [2, 1]
    finally:
        m._current_user.reset(token)


@pytest.mark.asyncio
async def test_template_refusals_write_nothing(authenticated_async_context, fixture_user_id, monkeypatch):
    from src.api import mcp_server as m
    from src.core import settings

    tools = m.build_server()._tool_manager
    save = tools.get_tool("save_artifact").fn
    token = m._current_user.set(_mcp_user(fixture_user_id))
    try:
        async with authenticated_async_context() as client:
            app_id = await _bring(client)
            cid = await _cold_contact(client)
            base = {"kind": "outreach_template", "label": "L", "text": "t"}
            for extra in (
                {"application_id": app_id},
                {"contact_id": cid},
                {"channel": "email"},
                {"label": ""},
                {"ats_score": 80},
            ):
                with pytest.raises(Exception) as exc:
                    await save(**{**base, **extra})
                assert "422" in str(exc.value), extra
            assert await _template_rows(client) == 0

            bad_kind = await client.post(
                "/api/templates", json={"kind": "letter_template", "label": "L", "text": "t"}
            )
            assert bad_kind.status_code == 422
            assert "USER_TEMPLATE_KINDS" in bad_kind.text

            monkeypatch.setattr(settings, "USER_TEMPLATE_LABELS_MAX", 1)
            first = await client.post("/api/templates", json=base)
            assert first.status_code == 201, first.text
            second = await client.post("/api/templates", json={**base, "label": "Other"})
            assert second.status_code == 429

            spine = await client.post(
                f"/api/applications/{app_id}/artifacts", json={"kind": "outreach_template", "text": "t"}
            )
            assert spine.status_code == 422
    finally:
        m._current_user.reset(token)


@pytest.mark.asyncio
async def test_stats_by_outreach_variant(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m

    token = m._current_user.set(_mcp_user(fixture_user_id))
    try:
        async with authenticated_async_context() as client:
            app_id = await _bring(client)
            ann = await _linked_contact(client, app_id, "Ann")
            ben = await _cold_contact(client, "Ben")
            cy = await _cold_contact(client, "Cy")
            dee = await _cold_contact(client, "Dee")
            eve = await _cold_contact(client, "Eve")

            await _out(client, ann, "message", text="a", label="Short ask")
            await _out(client, ann, "sent")
            await _out(client, ann, "reply")
            await _out(client, ben, "message", text="b", label="short ask ")
            await _out(client, ben, "sent")
            await _out(client, cy, "message", text="c", label="Long")
            await _out(client, cy, "sent")
            assert (await _out(client, cy, "message", text="c2", label="Later")).status_code == 201
            await _out(client, dee, "message", text="d", label="Dee only")
            await _out(client, eve, "sent")

            body = (await client.get("/api/applications/stats")).json()
            by_key = {g["key"]: g for g in body["by_outreach_variant"]}
            assert by_key["short ask"] == {
                "key": "short ask", "label": "Short ask", "outreach_sent": 2, "outreach_replied": 1,
                "reply_rate": 0.5, "small_sample": True,
            }
            assert by_key["long"]["outreach_sent"] == 1
            assert "later" not in by_key and "dee only" not in by_key
            assert by_key[None]["label"] == "Not set"
            assert by_key[None]["outreach_sent"] == 1

            via_mcp = await m.build_server()._tool_manager.get_tool("stats").fn()
            assert via_mcp["by_outreach_variant"] == body["by_outreach_variant"]
    finally:
        m._current_user.reset(token)


def test_user_templates_is_append_only():
    src = Path(__file__).resolve().parent.parent / "src"
    offenders = [
        str(py) for py in src.rglob("*.py")
        if re.search(r"(UPDATE|DELETE\s+FROM)\s+user_templates", py.read_text(encoding="utf-8"), re.IGNORECASE)
    ]
    assert offenders == [], f"user_templates must be append-only: {offenders}"


async def _shape(path: str) -> tuple[bool, bool]:
    from src.repositories import pg

    async with pg.connect(path) as conn:
        cur = await conn.execute(
            "SELECT COUNT(*) FROM information_schema.columns WHERE table_schema = current_schema() "
            "AND table_name = 'contact_outreach' AND column_name = 'label'"
        )
        has_label = (await cur.fetchone())[0] == 1
        cur = await conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = current_schema() "
            "AND table_name = 'user_templates'"
        )
        has_table = (await cur.fetchone())[0] == 1
    return has_label, has_table


@pytest.mark.asyncio
async def test_migration_0077_up_down(tmp_path):
    from migrations import runner
    from src.repositories.database import JobDatabase

    path = str(tmp_path / f"m73_{uuid.uuid4().hex[:8]}.db")
    db = JobDatabase(path)
    await db.init_db()
    await db.close()
    await runner.up(path, target=STEM)
    assert await _shape(path) == (True, True)
    assert await runner.down(path) == STEM
    assert await _shape(path) == (False, False)
    await runner.up(path, target=STEM)
    assert await _shape(path) == (True, True)


def test_recipes_mention_variant_label_and_templates():
    from src.api.routes.recipes import load_recipe

    assert "label" in load_recipe("reach").text
    rules = load_recipe("rules").text
    assert "outreach_template" in rules and "by_outreach_variant" in rules
