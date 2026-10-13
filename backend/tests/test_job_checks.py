"""Job checks - deterministic facts about ONE brought job (owner decision 2026-10-13).

Pure tests for ``services/applications/job_checks.py`` plus route-level tests:
bring returns ``checks``, the two ad dates are stored and returned, the same
link with tracking parameters is the same job, ``preferences.target_companies``
round-trips, and migration 0054 adds / drops its two columns.

Rule #21: every assertion is on a real value built by the test.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications.job_checks import apply_host, canonical_url, classify_host, job_checks

TODAY = date(2026, 10, 13)


def _by(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["code"]: c for c in checks}


def _run(desc: str = "word " * 200, url: str = "", posted: Any = None, employer: Any = None) -> dict[str, Any]:
    return _by(job_checks(desc, url, posted, employer, TODAY))


# ── description completeness ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("words", "band"), [(0, "title_only"), (39, "title_only"), (40, "partial"), (149, "partial"), (150, "full")]
)
def test_completeness_bands(words: int, band: str):
    got = _run(desc=" ".join(["w"] * words))["description_completeness"]
    assert got["value"] == band and got["ok"] is (band == "full")
    assert ("fetch the full ad" in got["reason"]) is (band != "full")


# ── apply host: strict suffix match ─────────────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "https://boards.greenhouse.io/acme/jobs/1", "https://greenhouse.io/x", "https://jobs.lever.co/acme/1",
        "https://jobs.ashbyhq.com/acme", "https://acme.myworkdayjobs.com/en/x", "https://jobs.smartrecruiters.com/A",
        "https://apply.workable.com/acme", "https://acme.bamboohr.com/careers", "https://acme.recruitee.com/o/x",
        "https://acme.teamtailor.com/jobs/1", "https://acme.jobs.personio.de/job/1", "https://acme.personio.com/x",
        "https://BOARDS.Greenhouse.IO:443/acme?gh_jid=1",
    ],
)
def test_official_ats_hosts(url: str):
    got = _run(url=url)["apply_host"]
    assert got["value"] == "official_ats" and got["ok"] is True and got["host"] == apply_host(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil-greenhouse.io/jobs/1",
        "https://greenhouse.io.evil.com/jobs/1",
        "https://user@greenhouse.io@evil.com/jobs/1",
        "https://greenhouse.io@evil.com/jobs/1",
        "https://notlever.co/x",
        "https://lever.co.evil.com/x",
        "https://myworkdayjobs.com.attacker.net/x",
        "greenhouse.io/no-scheme",
        "javascript://greenhouse.io/%0Aalert(1)",
    ],
)
def test_look_alike_hosts_are_never_official(url: str):
    got = _run(url=url)["apply_host"]
    assert got["value"] != "official_ats" and got["ok"] is not True


def test_the_userinfo_trick_reads_the_real_host():
    assert apply_host("https://user@greenhouse.io@evil.com/jobs/1") == "evil.com"
    assert apply_host("https://greenhouse.io@evil.com/") == "evil.com"


def test_job_boards_unknown_and_missing():
    for url in ("https://www.linkedin.com/jobs/view/1", "https://uk.indeed.com/viewjob?jk=1", "https://indeed.co.uk/x",
                "https://de.indeed.com/x"):
        got = _run(url=url)["apply_host"]
        assert got["value"] == "job_board" and got["ok"] is False, url
    assert classify_host("indeed.com.evil.com") == "unknown"
    assert classify_host("evilindeed.com") == "unknown"
    assert classify_host("linkedin.com.evil.io") == "unknown"
    unknown = _run(url="https://careers.acme.example/job/1")["apply_host"]
    assert unknown["value"] == "unknown" and unknown["ok"] is None and unknown["host"] == "careers.acme.example"
    none = _run(url="")["apply_host"]
    assert none["value"] == "unknown" and none["host"] == "" and none["ok"] is None


# ── age and repost ──────────────────────────────────────────────────────────


def test_ad_age_and_repost():
    assert _run()["ad_age_days"]["value"] is None
    assert "reposted" not in _run(posted="2026-10-01")  # one date alone cannot show a repost
    assert _run(posted="2026-10-03")["ad_age_days"]["value"] == 10
    both = _run(posted="2026-10-10", employer="2026-08-01")
    assert both["ad_age_days"]["value"] == 3  # newest date given
    assert both["reposted"]["value"] is True and both["reposted"]["ok"] is False
    assert "2026-10-10" in both["reposted"]["reason"] and "2026-08-01" in both["reposted"]["reason"]
    near = _run(posted="2026-10-10", employer="2026-09-26")  # exactly 14 days apart
    assert near["reposted"]["value"] is False and near["reposted"]["ok"] is True
    assert _run(posted="2026-10-10", employer="2026-09-25")["reposted"]["value"] is True  # 15 days
    assert _run(posted="not-a-date")["ad_age_days"]["value"] is None  # never raises


# ── pay + sponsorship: presence only ────────────────────────────────────────


def test_pay_and_sponsorship_presence():
    quiet = _run(desc="We build agents. Python.")
    assert quiet["pay_stated"]["value"] is False and quiet["sponsorship_mentioned"]["value"] is False
    for text in ("Salary £60,000 a year", "$120k", "€70 000", "EUR 50000 - 70000", "45000 GBP", "90k per year"):
        assert _run(desc=text)["pay_stated"]["value"] is True, text
    for text in ("Version 3 of 5 users", "Team of 10 people"):
        assert _run(desc=text)["pay_stated"]["value"] is False, text
    for text in ("We offer visa sponsorship", "No Sponsors accepted", "Visa support available"):
        assert _run(desc=text)["sponsorship_mentioned"]["value"] is True, text
    assert _run(desc="Visa-free travel to the Advisary")["sponsorship_mentioned"]["value"] is True  # word "Visa"
    assert _run(desc="advisable revisal")["sponsorship_mentioned"]["value"] is False


# ── canonical URL ───────────────────────────────────────────────────────────


def test_canonical_url_folds_tracking_and_noise():
    base = canonical_url("https://boards.greenhouse.io/acme/jobs/1")
    assert base == "https://boards.greenhouse.io/acme/jobs/1"
    for noisy in (
        "https://Boards.Greenhouse.io/acme/jobs/1/",
        "https://boards.greenhouse.io/acme/jobs/1?utm_source=x&utm_medium=y",
        "https://boards.greenhouse.io/acme/jobs/1#apply",
        "http://boards.greenhouse.io/acme/jobs/1?gclid=a&fbclid=b&ref=c&trackingId=d&refId=e",
        "https://www.boards.greenhouse.io/acme/jobs/1",
    ):
        assert canonical_url(noisy) == base, noisy
    assert canonical_url("https://x.example/j?id=2&a=1") == canonical_url("https://x.example/j?a=1&id=2")
    assert canonical_url("https://x.example/j?id=2") != canonical_url("https://x.example/j?id=3")
    assert canonical_url("") == "" and canonical_url("ftp://x.example/a") == "" and canonical_url("nonsense") == ""


# ── route level ─────────────────────────────────────────────────────────────

_AD = {
    "title": "ML Engineer", "company": "Parisien", "location": "Paris",
    "apply_url": "https://boards.greenhouse.io/parisien/jobs/4501234",
    "description": "Train and ship models. Python, PyTorch. Salary 70000 EUR. Visa sponsorship offered.",
}


async def _bring(client: AsyncClient, ad: dict[str, Any] | None = None, **extra: Any):
    return await client.post("/api/jobs/bring", json={**(ad or _AD), **extra})


@pytest.mark.asyncio
async def test_bring_returns_checks_and_stores_the_two_dates(authenticated_async_context):
    async with authenticated_async_context() as client:
        resp = await _bring(client, posted_at="2026-10-01", employer_published_at="2026-07-01")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert (body["ad_posted_at"], body["employer_published_at"]) == ("2026-10-01", "2026-07-01")
        checks = _by(body["checks"])
        assert checks["description_completeness"]["value"] == "title_only"
        assert checks["apply_host"]["value"] == "official_ats"
        assert checks["reposted"]["value"] is True
        assert checks["pay_stated"]["value"] is True and checks["sponsorship_mentioned"]["value"] is True

        detail = await client.get(f"/api/applications/{body['application_id']}")
        job = detail.json()["job"]
        assert (job["ad_posted_at"], job["employer_published_at"]) == ("2026-10-01", "2026-07-01")

        # a re-bring that names no date never clears them
        again = (await _bring(client)).json()
        assert again["ad_posted_at"] == "2026-10-01" and again["existing"] is True

        fixed = await client.patch(
            f"/api/applications/{body['application_id']}/job", json={"employer_published_at": "2026-09-30"}
        )
        assert fixed.status_code == 200, fixed.text
        assert fixed.json()["employer_published_at"] == "2026-09-30" and fixed.json()["ad_posted_at"] == "2026-10-01"
        cleared = await client.patch(f"/api/applications/{body['application_id']}/job", json={"posted_at": ""})
        assert cleared.json()["ad_posted_at"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", ["2026-13-01", "13/10/2026", "2026-1-1", "yesterday", "2999-01-01", (date.today() + timedelta(days=3)).isoformat()]
)
async def test_bad_or_future_dates_are_422_and_store_nothing(authenticated_async_context, bad):
    async with authenticated_async_context() as client:
        assert (await _bring(client, posted_at=bad)).status_code == 422
        assert (await _bring(client, employer_published_at=bad)).status_code == 422
        assert (await client.get("/api/applications")).json()["applications"] == []


@pytest.mark.asyncio
async def test_tomorrow_is_tolerated(authenticated_async_context):
    async with authenticated_async_context() as client:
        ok = await _bring(client, posted_at=(date.today() + timedelta(days=1)).isoformat())
        assert ok.status_code == 200, ok.text


@pytest.mark.asyncio
async def test_same_link_with_tracking_params_is_the_same_job(authenticated_async_context):
    async with authenticated_async_context() as client:
        first = (await _bring(client)).json()
        assert first["existing"] is False
        retitled = {
            **_AD, "title": "Machine Learning Engineer (Remote)", "company": "Parisien SAS",
            "apply_url": "https://Boards.Greenhouse.io/parisien/jobs/4501234/?utm_source=linkedin&gclid=zz#apply",
        }
        second = (await _bring(client, retitled)).json()
        assert second["existing"] is True
        assert second["job"]["id"] == first["job"]["id"]
        assert second["application_id"] == first["application_id"]
        other = (await _bring(client, {**_AD, "title": "Other", "apply_url": "https://boards.greenhouse.io/parisien/jobs/4501299"})).json()
        assert other["existing"] is False and other["application_id"] != first["application_id"]
        assert len((await client.get("/api/applications")).json()["applications"]) == 2


@pytest.mark.asyncio
async def test_a_shared_careers_link_never_merges_two_jobs(authenticated_async_context):
    """A board root / careers home page is shared by many jobs: two different
    titles there are two applications (CI 2026-10-13 caught the merge)."""
    async with authenticated_async_context() as client:
        shared = "https://northwind.example/careers/7"
        a = (await _bring(client, {**_AD, "title": "Data Engineer", "apply_url": shared})).json()
        b = (await _bring(client, {**_AD, "title": "ML Engineer", "apply_url": shared})).json()
        assert a["existing"] is False and b["existing"] is False
        assert a["application_id"] != b["application_id"]


def test_job_specific_url():
    from src.services.applications.job_checks import is_job_specific_url

    assert is_job_specific_url("https://boards.greenhouse.io/acme/jobs/4501234")
    assert is_job_specific_url("https://jobs.ashbyhq.com/rifa/bd6366c8-893a-49fd-9b9a-31cb75a397d0/application")
    assert is_job_specific_url("https://in.indeed.com/viewjob?jk=d56bf1dbda3ddc79")
    assert not is_job_specific_url("https://northwind.example/careers/7")
    assert not is_job_specific_url("https://jobs.lever.co/acme")
    assert not is_job_specific_url("https://acme.com/careers")


@pytest.mark.asyncio
async def test_checks_on_get_job_and_the_kit_job_block(authenticated_async_context):
    pytest.importorskip("mcp")
    import json

    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from src.api.main import app
    from src.api.mcp_server import mcp_runtime

    async with authenticated_async_context() as client:
        token = (await client.post("/api/tokens", json={"name": "agent"})).json()["token"]
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://test", headers={"Authorization": f"Bearer {token}"}
    )
    async with mcp_runtime():
        async with Client(streamable_http_client("http://test/api/mcp", http_client=http)) as mcp:
            def payload(res: Any) -> dict[str, Any]:
                assert not res.is_error, res.content[0].text
                return json.loads(res.content[0].text)

            brought = payload(await mcp.call_tool("bring_job", {**_AD, "posted_at": "2026-10-01"}))
            assert _by(brought["checks"])["apply_host"]["value"] == "official_ats"
            assert brought["ad_posted_at"] == "2026-10-01"
            job = payload(await mcp.call_tool("get_job", {"job_id": brought["job_id"]}))
            assert _by(job["checks"])["description_completeness"]["value"] == "title_only"
            assert _by(job["checks"])["apply_host"]["host"] == "boards.greenhouse.io"
            assert job["ad_posted_at"] == "2026-10-01"
            fixed = payload(await mcp.call_tool(
                "update_job", {"application_id": brought["application_id"], "employer_published_at": "2026-09-01"}
            ))
            assert fixed["employer_published_at"] == "2026-09-01"
            bad = await mcp.call_tool("update_job", {"application_id": brought["application_id"], "posted_at": "soon"})
            assert bad.is_error and "422" in bad.content[0].text
            kit = payload(await mcp.call_tool("get_application_kit", {"application_id": brought["application_id"]}))
            kit_checks = _by(kit["job"]["checks"])
            assert kit_checks["reposted"]["value"] is True
            assert kit["job"]["employer_published_at"] == "2026-09-01"


@pytest.mark.asyncio
async def test_target_companies_round_trip(authenticated_async_context, fixture_user_id):
    from src.services.profile.models import CVData, UserPreferences, UserProfile
    from src.services.profile.storage import save_profile

    save_profile(
        UserProfile(
            cv_data=CVData(raw_text="Python engineer.", name="Ada", skills=["Python"]),
            preferences=UserPreferences(target_job_titles=["AI Engineer"]),
        ),
        fixture_user_id,
        source_action="cv_upload",
    )
    async with authenticated_async_context() as client:
        assert (await client.get("/api/profile")).json()["preferences"]["target_companies"] == []
        ok = await client.patch(
            "/api/profile", json={"edits": [{"path": "preferences.target_companies", "value": ["Mistral AI", "Hugging Face"]}]}
        )
        assert ok.status_code == 200, ok.text
        assert (await client.get("/api/profile")).json()["preferences"]["target_companies"] == ["Mistral AI", "Hugging Face"]
        bad = await client.patch("/api/profile", json={"edits": [{"path": "preferences.target_companies", "value": "Mistral"}]})
        assert bad.status_code == 422
        cleared = await client.patch("/api/profile", json={"edits": [{"path": "preferences.target_companies", "value": None}]})
        assert cleared.status_code == 200, cleared.text
        assert (await client.get("/api/profile")).json()["preferences"]["target_companies"] == []


def test_migration_0054_adds_and_drops_exactly_the_two_columns():
    folder = Path(__file__).resolve().parent.parent / "migrations"
    up = (folder / "0054_job_ad_dates.up.sql").read_text(encoding="utf-8")
    down = (folder / "0054_job_ad_dates.down.sql").read_text(encoding="utf-8")
    for col in ("ad_posted_at", "employer_published_at"):
        assert f"ADD COLUMN IF NOT EXISTS {col} TEXT" in up
        assert f"DROP COLUMN IF EXISTS {col}" in down
    assert up.count("ADD COLUMN") == 2 and down.count("DROP COLUMN") == 2
