"""Owner decisions 2026-10-04 — three store-only features.

1. ATS opinion on a saved CV: ``save_artifact`` takes the ASSISTANT's own
   ``ats_score`` (0-100) + ``ats_notes``; Job360 stores them on that artifact
   version and returns them everywhere artifacts are listed. Never computed.
2. ``found_via`` on contacts — a closed set, editable through the append-only
   ``contact_edits`` overlay.
3. Job facts (``country`` / ``remote`` / ``found_on``) on the user's own
   application, a closed-set receipt ``channel`` for new receipts, and four
   new stats splits (``by_country``, ``by_job_source``, ``by_channel``,
   ``by_contact_found_via``).

Every number asserted below is a hand count of the history the test builds
(rule #21: real values end to end, never a schema-presence check).
"""
from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

_AD_FR = {
    "title": "ML Engineer",
    "company": "Parisien",
    "location": "Paris",
    "apply_url": "https://parisien.example/jobs/1",
    "description": "Train and ship models. Python, PyTorch.",
}
_AD_REMOTE = {
    "title": "AI Engineer",
    "company": "Anywhere Inc",
    "location": "Remote",
    "apply_url": "https://anywhere.example/jobs/2",
    "description": "Build agents. Fully remote across the US.",
}
_AD_BARE = {
    "title": "Data Scientist",
    "company": "Quiet Ltd",
    "location": "",
    "apply_url": "https://quiet.example/jobs/3",
    "description": "Analyse things. SQL, Python.",
}


async def _bring(client: AsyncClient, ad: dict[str, Any], **facts: Any) -> dict[str, Any]:
    resp = await client.post("/api/jobs/bring", json={**ad, **facts})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _detail(client: AsyncClient, application_id: int) -> dict[str, Any]:
    resp = await client.get(f"/api/applications/{application_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _save(client: AsyncClient, application_id: int, **body: Any):
    return await client.post(f"/api/applications/{application_id}/artifacts", json=body)


# ── 1. ATS opinion ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ats_opinion_is_stored_on_the_version_and_returned_everywhere(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR))["application_id"]
        resp = await _save(
            client, app_id, kind="cv", text="My CV v1", label="v1",
            ats_score=82, ats_notes="  Tables removed\x07.\nKeywords: PyTorch‮  ",
        )
        assert resp.status_code == 201, resp.text
        saved = resp.json()
        # Control characters are removed (line break kept), then trimmed.
        assert saved["ats_score"] == 82
        assert saved["ats_notes"] == "Tables removed.\nKeywords: PyTorch"

        detail = await _detail(client, app_id)
        (art,) = [a for a in detail["artifacts"] if a["kind"] == "cv"]
        assert art["ats_score"] == 82 and art["ats_notes"] == "Tables removed.\nKeywords: PyTorch"

        row = await client.get(f"/api/applications/{app_id}/artifacts/{saved['artifact_id']}")
        assert row.status_code == 200 and row.json()["ats_score"] == 82

        export = await client.get("/api/applications/export")
        assert export.status_code == 200, export.text
        (exp_app,) = [a for a in export.json()["applications"] if a["id"] == app_id]
        (exp_art,) = exp_app["artifacts"]
        assert exp_art["ats_score"] == 82 and exp_art["ats_notes"] == "Tables removed.\nKeywords: PyTorch"


@pytest.mark.asyncio
async def test_an_ats_recheck_is_a_new_version_never_an_update(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR))["application_id"]
        assert (await _save(client, app_id, kind="cv", text="v1", ats_score=61, ats_notes="weak")).status_code == 201
        assert (await _save(client, app_id, kind="cv", text="v2", ats_score=88)).status_code == 201
        detail = await _detail(client, app_id)
        by_version = {a["version_no"]: a for a in detail["artifacts"] if a["kind"] == "cv"}
        assert (by_version[1]["ats_score"], by_version[1]["ats_notes"]) == (61, "weak")
        assert (by_version[2]["ats_score"], by_version[2]["ats_notes"]) == (88, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [-1, 101, "70", 7.5, True])
async def test_ats_score_out_of_range_or_not_an_int_is_422(authenticated_async_context, bad):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR))["application_id"]
        resp = await _save(client, app_id, kind="cv", text="cv", ats_score=bad)
        assert resp.status_code == 422, resp.text
        detail = await _detail(client, app_id)
        assert detail["artifacts"] == [], "a refused save writes nothing"


@pytest.mark.asyncio
async def test_ats_is_only_for_cv_and_cover_letter(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR))["application_id"]
        ok = await _save(client, app_id, kind="cover_letter", text="Dear team", ats_score=70)
        assert ok.status_code == 201, ok.text
        for kind in ("answers", "outreach"):
            resp = await _save(client, app_id, kind=kind, text="x", ats_notes="n/a")
            assert resp.status_code == 422, (kind, resp.text)
            assert "ats_score/ats_notes are only allowed" in resp.json()["detail"]
        # No ATS fields at all → every kind still saves exactly as before.
        assert (await _save(client, app_id, kind="answers", text="x")).status_code == 201


@pytest.mark.asyncio
async def test_ats_notes_are_capped_by_a_live_setting(authenticated_async_context, monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS", 10)
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR))["application_id"]
        resp = await _save(client, app_id, kind="cv", text="cv", ats_notes="x" * 11)
        assert resp.status_code == 422
        assert "APPLICATION_ARTIFACT_ATS_NOTES_MAX_CHARS" in resp.json()["detail"]
        assert (await _save(client, app_id, kind="cv", text="cv", ats_notes="x" * 10)).status_code == 201


# ── 2. found_via on contacts ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_found_via_closed_set_add_update_and_read_back(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR))["application_id"]

        bad = await client.post(
            f"/api/applications/{app_id}/contacts", json={"name": "Ann", "found_via": "carrier pigeon"}
        )
        assert bad.status_code == 422 and "CONTACT_FOUND_VIA" in bad.json()["detail"]

        add = await client.post(
            f"/api/applications/{app_id}/contacts",
            json={"name": "Ann", "email": "ann@parisien.example", "found_via": "LinkedIn"},
        )
        assert add.status_code == 201, add.text
        contact = add.json()["contact"]
        assert contact["found_via"] == "linkedin"
        cid = contact["id"]

        unset = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Bob"})
        assert unset.json()["contact"]["found_via"] is None, "unset stays null (rule #29)"

        cold = await client.post("/api/contacts", json={"name": "Cleo", "found_via": "event"})
        assert cold.status_code == 201 and cold.json()["contact"]["found_via"] == "event"

        upd = await client.patch(f"/api/contacts/{cid}", json={"found_via": "company site"})
        assert upd.status_code == 200, upd.text
        assert upd.json()["found_via"] == "company_site"
        hist = [h["value"] for h in upd.json()["edit_history"]["found_via"]]
        assert hist == ["linkedin", "company_site"], "the old value is kept"

        assert (await client.patch(f"/api/contacts/{cid}", json={"found_via": "fax"})).status_code == 422

        detail = await _detail(client, app_id)
        by_name = {c["name"]: c for c in detail["contacts"]}
        assert by_name["Ann"]["found_via"] == "company_site" and by_name["Bob"]["found_via"] is None

        people = await client.get("/api/people")
        assert people.status_code == 200, people.text
        found = {p["name"]: p["found_via"] for p in people.json()["people"]}
        assert found == {"Ann": "company_site", "Bob": None, "Cleo": "event"}

        one = await client.get("/api/people", params={"contact_id": cid})
        assert one.json()["person"]["found_via"] == "company_site"

        cleared = await client.patch(f"/api/contacts/{cid}", json={"found_via": ""})
        assert cleared.status_code == 200 and cleared.json()["found_via"] is None


# ── 3a. Job facts: bring + fix later ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_bring_stores_normalised_job_facts_on_the_application(authenticated_async_context):
    async with authenticated_async_context() as client:
        brought = await _bring(client, _AD_FR, country="fr", remote=False, found_on="Company careers")
        assert (brought["country"], brought["remote"], brought["found_on"]) == ("FR", False, "company_careers")
        detail = await _detail(client, brought["application_id"])
        job = detail["job"]
        assert (job["country"], job["remote"], job["found_on"]) == ("FR", False, "company_careers")

        listed = await client.get("/api/applications")
        (row,) = listed.json()["applications"]
        assert (row["country"], row["remote"], row["found_on"]) == ("FR", False, "company_careers")

        # A re-bring that names nothing never clears what was stored.
        again = await _bring(client, _AD_FR)
        assert again["application_id"] == brought["application_id"]
        assert (again["country"], again["remote"], again["found_on"]) == ("FR", False, "company_careers")

        bare = await _bring(client, _AD_BARE)
        assert (bare["country"], bare["remote"], bare["found_on"]) == (None, None, None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "facts",
    [{"country": "France"}, {"country": "F1"}, {"found_on": "craigslist"}, {"remote": "yes"}, {"remote": 1}],
)
async def test_bring_with_a_bad_job_fact_is_422_and_stores_nothing(authenticated_async_context, facts):
    async with authenticated_async_context() as client:
        resp = await client.post("/api/jobs/bring", json={**_AD_FR, **facts})
        assert resp.status_code == 422, resp.text
        listed = await client.get("/api/applications")
        assert listed.json()["total"] == 0, "validated before anything is written"


@pytest.mark.asyncio
async def test_job_facts_can_be_fixed_later(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR, country="FR", found_on="linkedin"))["application_id"]

        resp = await client.patch(f"/api/applications/{app_id}/job", json={"country": "de", "remote": True})
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"application_id": app_id, "country": "DE", "remote": True, "found_on": "linkedin"}

        # An explicit null clears one field; the others are left alone.
        resp = await client.patch(f"/api/applications/{app_id}/job", json={"found_on": None})
        assert resp.json() == {"application_id": app_id, "country": "DE", "remote": True, "found_on": None}

        job = (await _detail(client, app_id))["job"]
        assert (job["country"], job["remote"], job["found_on"]) == ("DE", True, None)

        assert (await client.patch(f"/api/applications/{app_id}/job", json={})).status_code == 422
        assert (await client.patch(f"/api/applications/{app_id}/job", json={"found_on": "x"})).status_code == 422
        assert (await client.patch(f"/api/applications/{app_id}/job", json={"user_id": "u"})).status_code == 422
        assert (await client.patch("/api/applications/987654321/job", json={"country": "FR"})).status_code == 404


@pytest.mark.asyncio
async def test_job_facts_are_per_user_never_on_the_shared_catalog_row(authenticated_async_context):
    """Hard rule #10: two users bringing the same ad share ONE `jobs` row, so
    one user's facts must live on their own application, never on that row."""
    from src.core import settings
    from src.repositories import pgsync

    async with authenticated_async_context() as client:
        brought = await _bring(client, _AD_FR, country="FR", remote=True, found_on="indeed")
    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        cols = {
            r[0] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = 'jobs'"
            ).fetchall()
        }
        assert not {"country", "remote", "found_on", "job_country"} & cols
        row = conn.execute(
            "SELECT job_country, job_remote, job_found_on FROM applications WHERE id = ?",
            (brought["application_id"],),
        ).fetchone()
        assert tuple(row) == ("FR", True, "indeed")
    finally:
        conn.close()


# ── 3b. Receipt channel closed set ───────────────────────────────────────────


def test_receipt_channel_normalisers():
    from src.services.applications.spine import normalize_receipt_channel, receipt_channel_key

    assert normalize_receipt_channel(None) == "" and normalize_receipt_channel("  ") == ""
    assert normalize_receipt_channel("Company site") == "company_site"
    assert normalize_receipt_channel("linkedin-easy-apply") == "linkedin_easy_apply"
    # The old habits map instead of refusing — a refusal would drop the receipt.
    assert normalize_receipt_channel("LinkedIn") == "linkedin_easy_apply"
    assert normalize_receipt_channel("Company website") == "company_site"
    assert normalize_receipt_channel("Indeed") == "job_board"
    assert normalize_receipt_channel("carrier pigeon") == "other"
    # Legacy reading: same map, anything else → other, empty → None.
    assert receipt_channel_key("Company Site") == "company_site"
    assert receipt_channel_key("LinkedIn") == "linkedin_easy_apply"
    assert receipt_channel_key("carrier pigeon") == "other"
    assert receipt_channel_key("") is None and receipt_channel_key(None) is None


@pytest.mark.asyncio
async def test_new_receipts_take_only_the_closed_channel_set(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR))["application_id"]
        # Unknown text is stored as "other", never refused (the receipt must land).
        odd = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "carrier pigeon"})
        assert odd.status_code == 201 and odd.json()["channel"] == "other"
        ok = await client.post(f"/api/applications/{app_id}/receipt", json={"channel": "LinkedIn Easy Apply"})
        assert ok.status_code == 201 and ok.json()["channel"] == "linkedin_easy_apply"
        unsaid = await client.post(f"/api/applications/{app_id}/receipt", json={})
        assert unsaid.status_code == 201 and unsaid.json()["channel"] == ""

        job_id = (await _detail(client, app_id))["job_id"]
        legacy = await client.post(f"/api/receipts/{job_id}", json={"channel": "LinkedIn"})
        assert legacy.status_code in (200, 201), "the legacy door normalises the same way"
        assert legacy.json()["channel"] == "linkedin_easy_apply"


# ── 3c. Stats splits ─────────────────────────────────────────────────────────


def _legacy_channel(application_id: int, channel: str) -> None:
    """Simulate a receipt written BEFORE the closed set existed (free text).
    Test-only write: runtime code never updates a receipt."""
    from src.core import settings
    from src.repositories import pgsync

    conn = pgsync.connect(str(settings.DB_PATH))
    try:
        conn.execute("UPDATE application_receipts SET channel = ? WHERE application_id = ?", (channel, application_id))
        conn.commit()
    finally:
        conn.close()


def _by_key(groups: list[dict[str, Any]]) -> dict[Any, dict[str, Any]]:
    return {g["key"]: g for g in groups}


@pytest.mark.asyncio
async def test_stats_by_country_source_and_channel(authenticated_async_context):
    """A (FR, on-site, found on linkedin): applied via company_site, replied.
    B (US but REMOTE, found on indeed): applied — receipt channel is legacy
      free text "Company Site " (maps to company_site), then rejected.
    C (no facts): applied with a legacy "carrier pigeon" channel (→ other).
    D (no facts): brought only, no receipt (channel unset)."""
    async with authenticated_async_context() as client:
        a = (await _bring(client, _AD_FR, country="FR", remote=False, found_on="linkedin"))["application_id"]
        b = (await _bring(client, _AD_REMOTE, country="US", remote=True, found_on="indeed"))["application_id"]
        c = (await _bring(client, _AD_BARE))["application_id"]
        d = (await _bring(client, {**_AD_BARE, "title": "Analyst", "apply_url": "https://q.example/4"}))[
            "application_id"
        ]
        assert d

        assert (await client.post(f"/api/applications/{a}/receipt", json={"channel": "company_site"})).status_code == 201
        assert (await client.post(f"/api/applications/{a}/events", json={"event_type": "replied"})).status_code == 201
        assert (await client.post(f"/api/applications/{b}/receipt", json={"channel": "email"})).status_code == 201
        _legacy_channel(b, "Company Site ")
        assert (await client.post(f"/api/applications/{b}/events", json={"event_type": "rejected"})).status_code == 201
        assert (await client.post(f"/api/applications/{c}/receipt", json={})).status_code == 201
        _legacy_channel(c, "carrier pigeon")

        resp = await client.get("/api/applications/stats")
        assert resp.status_code == 200, resp.text
        body = resp.json()

    country = _by_key(body["by_country"])
    assert set(country) == {"FR", "remote", None}
    assert (country["FR"]["label"], country["FR"]["applied"], country["FR"]["replied"]) == ("FR", 1, 1)
    assert country["FR"]["reply_rate"] == 1.0 and country["FR"]["interview_rate"] == 0.0
    assert (country["remote"]["label"], country["remote"]["applied"], country["remote"]["rejected"]) == (
        "Remote", 1, 1,
    )
    assert country["remote"]["reply_rate"] == 0.0
    assert (country[None]["label"], country[None]["brought"], country[None]["applied"]) == ("Not set", 2, 1)

    source = _by_key(body["by_job_source"])
    assert set(source) == {"linkedin", "indeed", None}
    assert source["linkedin"]["brought"] == 1 and source[None]["brought"] == 2 and source[None]["label"] == "Not set"

    channel = _by_key(body["by_channel"])
    assert set(channel) == {"company_site", "other", None}
    assert channel["company_site"]["applied"] == 2, "legacy 'Company Site ' maps onto the member"
    assert channel["company_site"]["replied"] == 1 and channel["company_site"]["reply_rate"] == 0.5
    assert channel["other"]["applied"] == 1
    assert (channel[None]["brought"], channel[None]["applied"], channel[None]["reply_rate"]) == (1, 0, None)
    # Order: applied DESC, brought DESC, key ASC, the "Not set" group last on ties.
    assert [g["key"] for g in body["by_channel"]] == ["company_site", "other", None]


@pytest.mark.asyncio
async def test_stats_by_contact_found_via(authenticated_async_context):
    """linkedin: Ann (sent + reply), Ben (sent only) → 2 contacts, 2 sent,
    1 replied, rate 0.5. apollo→event via an EDIT: Cy (no outreach). Unset:
    Dee (cold, reply recorded without a sent mark → replied 1, rate null)."""
    async with authenticated_async_context() as client:
        app_id = (await _bring(client, _AD_FR))["application_id"]

        async def add(name: str, found_via: Any = None, cold: bool = False) -> int:
            body: dict[str, Any] = {"name": name}
            if found_via:
                body["found_via"] = found_via
            url = "/api/contacts" if cold else f"/api/applications/{app_id}/contacts"
            r = await client.post(url, json=body)
            assert r.status_code == 201, r.text
            return int(r.json()["contact"]["id"])

        async def mark(cid: int, entry: str) -> None:
            r = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": entry, "channel": "email"})
            assert r.status_code == 201, r.text

        ann, ben = await add("Ann", "linkedin"), await add("Ben", "linkedin")
        cy, dee = await add("Cy", "apollo"), await add("Dee", cold=True)
        await mark(ann, "sent")
        await mark(ann, "reply")
        await mark(ben, "sent")
        await mark(dee, "reply")
        assert (await client.patch(f"/api/contacts/{cy}", json={"found_via": "event"})).status_code == 200

        resp = await client.get("/api/applications/stats")
        assert resp.status_code == 200, resp.text
        groups = resp.json()["by_contact_found_via"]

    by = _by_key(groups)
    assert set(by) == {"linkedin", "event", None}, "the edit overlay wins over the base row"
    assert by["linkedin"] == {
        "key": "linkedin", "label": "linkedin", "contacts": 2, "outreach_sent": 2,
        "outreach_replied": 1, "reply_rate": 0.5,
    }
    assert by["event"]["contacts"] == 1 and by["event"]["outreach_sent"] == 0 and by["event"]["reply_rate"] is None
    assert by[None]["label"] == "Not set" and by[None]["outreach_replied"] == 1 and by[None]["reply_rate"] is None
    assert [g["key"] for g in groups] == ["linkedin", "event", None]


# ── MCP parity + the assistant's instructions ────────────────────────────────


@pytest.mark.asyncio
async def test_mcp_tools_carry_the_new_params(authenticated_async_context):
    pytest.importorskip("mcp")
    import json

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    # Local copies of test_mcp_server's helpers — never import across test
    # modules (it breaks per-test schema isolation).
    async with authenticated_async_context() as client:
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

            brought = payload(
                await mcp.call_tool("bring_job", {**_AD_FR, "country": "fr", "remote": False, "found_on": "linkedin"})
            )
            assert (brought["country"], brought["remote"], brought["found_on"]) == ("FR", False, "linkedin")
            app_id = brought["application_id"]

            job = payload(await mcp.call_tool("get_job", {"job_id": brought["job_id"]}))
            assert (job["country"], job["remote"], job["found_on"]) == ("FR", False, "linkedin")

            fixed = payload(await mcp.call_tool("update_job", {"application_id": app_id, "country": "DE"}))
            assert fixed["country"] == "DE" and fixed["found_on"] == "linkedin"
            bad = await mcp.call_tool("update_job", {"application_id": app_id, "found_on": "craigslist"})
            assert bad.is_error and "422" in bad.content[0].text

            saved = payload(
                await mcp.call_tool(
                    "save_artifact",
                    {"application_id": app_id, "kind": "cv", "text": "cv", "ats_score": 77, "ats_notes": "ok"},
                )
            )
            assert saved["ats_score"] == 77 and saved["ats_notes"] == "ok"
            refused = await mcp.call_tool(
                "save_artifact", {"application_id": app_id, "kind": "answers", "text": "x", "ats_score": 5}
            )
            assert refused.is_error and "422" in refused.content[0].text

            person = payload(
                await mcp.call_tool("add_contact", {"name": "Ann", "application_id": app_id, "found_via": "apollo"})
            )
            assert person["contact"]["found_via"] == "apollo"
            edited = payload(
                await mcp.call_tool("update_contact", {"contact_id": person["contact"]["id"], "found_via": "email"})
            )
            assert edited["found_via"] == "email"

            receipt = await mcp.call_tool("record_application", {"job_id": brought["job_id"], "channel": "pigeon"})
            assert not receipt.is_error, "an odd channel never costs the user their receipt"

            stats = payload(await mcp.call_tool("stats", {}))
            assert stats["by_country"][0]["key"] == "DE"
            assert stats["by_job_source"][0]["key"] == "linkedin"
            assert _by_key(stats["by_contact_found_via"])["email"]["contacts"] == 1


def test_instructions_and_recipes_tell_the_assistant_about_the_new_params():
    from src.api.mcp_server import INSTRUCTIONS
    from src.api.routes.recipes import load_recipe

    for word in ("ats_score", "ats_notes", "found_via", "country", "remote", "found_on", "update_job"):
        assert word in INSTRUCTIONS, word
    apply = load_recipe("apply").text
    assert "ats_score" in apply and "ats_notes" in apply and "EVERY CV" in apply
    assert "linkedin_easy_apply" in apply
    assert "found_via" in load_recipe("reach").text
    hunt = load_recipe("hunt").text
    assert "`country`" in hunt and "`remote`" in hunt and "`found_on`" in hunt
    assert "found_on" in load_recipe("setup").text
