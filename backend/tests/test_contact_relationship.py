"""Outreach pack #12 — ``relationship`` (closed set) and ``shared`` (a pointer to
a REAL profile record) on contacts, plus the ``by_contact_relationship`` stats
group. Job360 stores; the agent judges. Every assertion uses real, non-default
values (rule #21)."""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

_AD = {
    "title": "ML Engineer",
    "company": "Parisien",
    "location": "Paris",
    "apply_url": "https://parisien.example/jobs/1",
    "description": "Train and ship models. Python, PyTorch.",
}
_POSITION = "cv_data.cv_positions[0]: Northwind Ltd — Data Analyst"
_EDU = "cv_data.education[0]: MSc Data Science — University of Leeds | 2020"


def _seed_profile(user_id: str) -> None:
    from src.services.profile.models import CVData, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(
                raw_text="x",
                cv_positions=[
                    {"company": "Northwind Ltd", "title": "Data Analyst", "dates": "2019 - 2022", "location": "",
                     "bullets": []}
                ],
                cv_projects=[{"name": "Tide Gauge", "description": "", "technologies": [], "dates": ""}],
                education=["MSc Data Science — University of Leeds | 2020"],
            )
        ),
        user_id,
        source_action="cv_upload",
    )


async def _bring(client: AsyncClient, ad: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_key(groups: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    return {g["key"]: g for g in groups}


def test_validate_relationship_normalises_and_refuses():
    from src.services.applications.contacts import _validate_relationship
    from src.services.applications.spine import SpineError

    assert _validate_relationship("Former colleague") == "former_colleague"
    assert _validate_relationship("HIRING-MANAGER") == "hiring_manager"
    assert _validate_relationship("none") == "none", "none is an explicit member, not unset"
    assert _validate_relationship("") is None
    assert _validate_relationship(None) is None
    with pytest.raises(SpineError) as exc:
        _validate_relationship("nemesis")
    assert exc.value.status_code == 422 and "CONTACT_RELATIONSHIP" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_relationship_add_update_clear_and_read_back(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD))["application_id"]
        add = await client.post(
            f"/api/applications/{app_id}/contacts",
            json={"name": "Ann", "email": "ann@parisien.example", "relationship": "Former colleague"},
        )
        assert add.status_code == 201, add.text
        contact = add.json()["contact"]
        assert contact["relationship"] == "former_colleague"
        cid = contact["id"]

        bare = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Bob"})
        assert bare.json()["contact"]["relationship"] is None

        none_ = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Nan", "relationship": "none"})
        assert none_.json()["contact"]["relationship"] == "none"

        cold = await client.post("/api/contacts", json={"name": "Cleo", "company": "Northwind", "relationship": "alumni"})
        assert cold.status_code == 201 and cold.json()["contact"]["relationship"] == "alumni"

        upd = await client.patch(f"/api/contacts/{cid}", json={"relationship": "hiring manager"})
        assert upd.status_code == 200, upd.text
        assert upd.json()["relationship"] == "hiring_manager"
        hist = [h["value"] for h in upd.json()["edit_history"]["relationship"]]
        assert hist == ["former_colleague", "hiring_manager"]

        assert (await client.patch(f"/api/contacts/{cid}", json={"relationship": "nemesis"})).status_code == 422

        people = await client.get("/api/people")
        rel = {p["name"]: p["relationship"] for p in people.json()["people"]}
        assert rel == {"Ann": "hiring_manager", "Bob": None, "Nan": "none", "Cleo": "alumni"}
        detail = (await client.get(f"/api/applications/{app_id}")).json()
        assert {c["name"]: c["relationship"] for c in detail["contacts"]}["Ann"] == "hiring_manager"

        cleared = await client.patch(f"/api/contacts/{cid}", json={"relationship": ""})
        assert cleared.status_code == 200 and cleared.json()["relationship"] is None


@pytest.mark.asyncio
async def test_shared_is_read_from_the_profile_never_the_caller(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        app_id = (await _bring(client, _AD))["application_id"]
        add = await client.post(
            f"/api/applications/{app_id}/contacts",
            json={"name": "Ann", "email": "ann@parisien.example", "shared": "cv_data.cv_positions[0]"},
        )
        assert add.status_code == 201, add.text
        assert add.json()["contact"]["shared"] == _POSITION
        cid = add.json()["contact"]["id"]

        forged = await client.patch(f"/api/contacts/{cid}", json={"shared": "cv_data.education[0]: Oxford"})
        assert forged.status_code == 200, forged.text
        assert forged.json()["shared"] == _EDU, "the forged suffix is ignored; the label is re-read"

        proj = await client.patch(f"/api/contacts/{cid}", json={"shared": "cv_data.cv_projects[0]"})
        assert proj.json()["shared"] == "cv_data.cv_projects[0]: Tide Gauge"
        assert [h["value"] for h in proj.json()["edit_history"]["shared"]] == [
            _POSITION, _EDU, "cv_data.cv_projects[0]: Tide Gauge",
        ]

        cleared = await client.patch(f"/api/contacts/{cid}", json={"shared": ""})
        assert cleared.status_code == 200 and cleared.json()["shared"] is None


@pytest.mark.asyncio
async def test_shared_refuses_a_record_the_profile_lacks_and_stores_nothing(
    authenticated_async_context, fixture_user_id
):
    async with authenticated_async_context() as client:
        # A user with NO profile: nothing to point at.
        empty = await client.post("/api/contacts", json={"name": "Zed", "company": "Northwind", "shared": "cv_data.education[0]"})
        assert empty.status_code == 422 and "no record in your profile" in empty.json()["detail"]

        _seed_profile(fixture_user_id)
        missing = await client.post("/api/contacts", json={"name": "Eve", "company": "Northwind", "shared": "cv_data.cv_positions[5]"})
        assert missing.status_code == 422 and "no record in your profile" in missing.json()["detail"]
        for bad in ("Northwind Ltd", "cv_data.linkedin_positions[0]"):
            r = await client.post("/api/contacts", json={"name": "Eve", "company": "Northwind", "shared": bad})
            assert r.status_code == 422 and "shared must name a profile record" in r.json()["detail"]
        assert (await client.get("/api/people")).json()["people"] == [], "nothing was stored"

        ok = await client.post("/api/contacts", json={"name": "Ann", "company": "Northwind", "shared": "cv_data.cv_positions[0]"})
        assert ok.status_code == 201, ok.text
        cid = ok.json()["contact"]["id"]
        bad_patch = await client.patch(f"/api/contacts/{cid}", json={"shared": "cv_data.cv_positions[9]"})
        assert bad_patch.status_code == 422
        again = await client.patch(f"/api/contacts/{cid}", json={"notes": "met at a talk"})
        assert len(again.json()["edit_history"]["shared"]) == 1, "only the base entry"


@pytest.mark.asyncio
async def test_shared_profile_unavailable_is_503(authenticated_async_context, monkeypatch):
    async with authenticated_async_context() as client:
        def boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("db down")

        monkeypatch.setattr("src.services.profile.storage.load_profile", boom)
        r = await client.post("/api/contacts", json={"name": "Ann", "company": "Northwind", "shared": "cv_data.education[0]"})
        assert r.status_code == 503, r.text
        monkeypatch.undo()
        assert (await client.get("/api/people")).json()["people"] == []


@pytest.mark.asyncio
async def test_stats_by_contact_relationship(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD))["application_id"]

        async def add(name: str, relationship: Any = None, cold: bool = False) -> int:
            body: dict[str, Any] = {"name": name}
            if cold:
                body["company"] = "Northwind"
            if relationship:
                body["relationship"] = relationship
            url = "/api/contacts" if cold else f"/api/applications/{app_id}/contacts"
            r = await client.post(url, json=body)
            assert r.status_code == 201, r.text
            return int(r.json()["contact"]["id"])

        async def mark(cid: int, entry: str) -> None:
            r = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": entry, "channel": "email"})
            assert r.status_code == 201, r.text

        ann, ben = await add("Ann", "former_colleague"), await add("Ben", "former_colleague")
        cy, _dee = await add("Cy", "recruiter"), await add("Dee", cold=True)
        await mark(ann, "sent")
        await mark(ann, "reply")
        await mark(ben, "sent")
        assert (await client.patch(f"/api/contacts/{cy}", json={"relationship": "classmate"})).status_code == 200

        body = (await client.get("/api/applications/stats")).json()

    by = _by_key(body["by_contact_relationship"])
    assert set(by) == {"former_colleague", "classmate", None}, "the edit overlay wins over the base row"
    assert by["former_colleague"] == {
        "key": "former_colleague", "label": "former_colleague", "contacts": 2, "reachable": 0,
        "outreach_sent": 2, "outreach_replied": 1, "reply_rate": 0.5,
    }
    assert by["classmate"]["contacts"] == 1 and by[None]["label"] == "Not set"
    assert "by_contact_found_via" in body


@pytest.mark.asyncio
async def test_mcp_add_and_update_contact_carry_relationship_and_shared(authenticated_async_context, fixture_user_id):
    import json

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        _seed_profile(fixture_user_id)
        minted = await client.post("/api/tokens", json={"name": "agent"})
        assert minted.status_code == 201, minted.text
        token = minted.json()["token"]
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            def payload(res: Any) -> dict[str, Any]:
                assert not res.is_error, res.content[0].text
                return json.loads(res.content[0].text)

            added = payload(
                await mcp.call_tool(
                    "add_contact",
                    {"name": "Ann", "company": "Northwind", "relationship": "classmate", "shared": "cv_data.education[0]"},
                )
            )
            assert added["contact"]["relationship"] == "classmate"
            assert added["contact"]["shared"] == _EDU
            edited = payload(
                await mcp.call_tool(
                    "update_contact", {"contact_id": added["contact"]["id"], "relationship": "friend"}
                )
            )
            assert edited["relationship"] == "friend"
            bad = await mcp.call_tool("add_contact", {"name": "Bob", "company": "Northwind", "shared": "cv_data.cv_positions[3]"})
            assert bad.is_error and "422" in bad.content[0].text
            stats = payload(await mcp.call_tool("stats", {}))
            assert _by_key(stats["by_contact_relationship"])["friend"]["contacts"] == 1


def test_reach_recipe_mentions_relationship_and_shared():
    from src.api.routes.recipes import load_recipe

    text = load_recipe("reach").text
    assert "relationship" in text and "shared" in text
