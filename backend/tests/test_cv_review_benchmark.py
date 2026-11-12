"""CV benchmark wiring (owner 2026-11-10): strong = true AND ATS-ready, and the ad's must-haves the user has are in it.

The reviewer judges meaning (does the profile support this requirement); Job360 checks only what needs no
understanding: the term is literally in the frozen ad, literally in a named CV line, its evidence quotes are in ONE
profile field, a gap never appears in the CV, and the CV passes ``cv_format``. No ad text: ``jd_no_ad``, not weak.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import cv_review
from src.services.profile.models import AssistantSettings, CVData, UserProfile
from tests.test_cv_review import _save_cv, _stored, sha
from tests.test_cv_review_held_lines import B1, B2, B3, CV_DATA, RAW, SUMMARY

EDU = "BSc Computer Science, University of Leeds, 2024"
PROFILE_DATA: dict[str, Any] = {**CV_DATA, "raw_text": RAW + f"EDUCATION\n{EDU}\n", "education": [EDU]}
AD = ("AI Engineer. Must have: LangGraph, MCP, RAG, ChromaDB and Bedrock. Kubernetes is a must. "
      "Slack is a plus.")
HEAD = ("Ada Lovelace\nAI Engineer | Agentic Systems\nLondon, United Kingdom | +44 7700 900123 | ada@example.com\n"
        "github.com/ada | linkedin.com/in/ada\n\nSUMMARY\n" + SUMMARY + "\n\nEXPERIENCE\n"
        "Orbitly (orbitly.uk) | Founder & AI-Native Engineer | United Kingdom | Feb 2026 - Present\n- " + B1 + "\n"
        "Northwind Labs | AI Solutions Engineer – R&D Department | Stevenage, United Kingdom | Jun 2025 - Sep 2025\n"
        "- " + B2 + "\nTidewater | AI/ML Engineer Intern | United Kingdom (Remote) | Oct 2024 - Jan 2025\n- " + B3 +
        "\n\nPROJECTS\nSupport Agent | LangGraph, MCP, Gmail, Slack\n\n"
        "SKILLS\nAgents & LLMs: LangGraph, LangChain, MCP, RAG\n")
# The owner's CV shape (app 351) with an EDUCATION section: review lines 2 = summary, 6 = the RAG bullet, 9 =
# the project line (it names Slack), 11 = education.
HONEST = HEAD + f"\nEDUCATION\n{EDU}\n"


def _item(term: str, have: bool = True, evidence: Any = None, n: Any = None, must: bool = True) -> dict[str, Any]:
    return {"term": term, "must": must, "have": have, "evidence": evidence or [], "n": n or []}


HONEST_JD = [
    _item("LangGraph", evidence=[SUMMARY], n=[2]), _item("MCP", evidence=[SUMMARY], n=[2]),
    _item("RAG", evidence=[B2], n=[6]), _item("ChromaDB", evidence=[B2], n=[6]), _item("Kubernetes", have=False),
]


def _profile() -> UserProfile:
    return UserProfile(cv_data=CVData(**PROFILE_DATA))


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(_profile(), user_id, source_action="cv_upload")
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")


async def _app(client: Any, cv: str, ad: str = AD) -> tuple[int, int]:
    tag = sha(cv + ad)[:8]
    brought = await client.post("/api/jobs/bring", json={
        "title": f"AI Engineer ({tag})", "company": "Northwind", "location": "London", "country": "GB",
        "remote": False, "apply_url": f"https://northwind.example/careers/{tag}", "description": ad})
    assert brought.status_code == 200, brought.text
    app_id = int(brought.json()["application_id"])
    return app_id, await _save_cv(client, app_id, cv)


def _payload(cv_id: int, text: str, jd: Any = None) -> dict[str, Any]:
    rows = [{"n": n, "verdict": "supported", "evidence": [ln.strip().lstrip(cv_review._BULLETS + " \t")]}
            for n, ln in enumerate(cv_review.content_lines(text, _profile()), 1)]
    out = {"artifact_id": cv_id, "sha256": sha(text), "confidence": "strong", "lines": rows, "reviewer": "fresh"}
    return out if jd is None else {**out, "jd": jd}


async def _review(ctx: Any, user_id: str, jd: Any, cv: str = HONEST, ad: str = AD) -> dict[str, Any]:
    async with ctx() as client:
        _seed(user_id)
        app_id, cv_id = await _app(client, cv, ad)
        return await _stored(client, app_id, _payload(cv_id, cv, jd))


@pytest.mark.asyncio
async def test_an_honest_owner_shaped_cv_with_an_honest_jd_is_strong(authenticated_async_context, fixture_user_id):
    got = await _review(authenticated_async_context, fixture_user_id, HONEST_JD)
    assert (got["confidence"], got["downgraded"], got["where"], got["rules"]) == ("strong", [], {}, cv_review.RULES)
    assert got["jd"] == {"state": "judged", "must": 4, "covered": 4, "bar": 0.8, "gaps": ["Kubernetes"]}


@pytest.mark.asyncio
async def test_a_format_finding_is_weak_ats_format(authenticated_async_context, fixture_user_id):
    got = await _review(authenticated_async_context, fixture_user_id, HONEST_JD, cv=HEAD)  # no EDUCATION section
    assert (got["confidence"], got["downgraded"]) == ("weak", ["ats_format"])
    assert got["where"] == {"ats_format": ["missing_section:education"]}


@pytest.mark.asyncio
async def test_a_jd_term_not_in_the_ad_is_weak(authenticated_async_context, fixture_user_id):
    got = await _review(authenticated_async_context, fixture_user_id, [*HONEST_JD, _item("Terraform", have=False)])
    assert (got["confidence"], got["downgraded"], got["where"]) == ("weak", ["jd_term_not_in_ad"],
                                                                     {"jd_term_not_in_ad": [6]})


@pytest.mark.asyncio
async def test_a_had_term_missing_from_its_named_cv_line_is_weak(authenticated_async_context, fixture_user_id):
    jd = [_item("LangGraph", evidence=[SUMMARY], n=[4]), *HONEST_JD[1:]]  # line 4 is the memory-layer bullet
    got = await _review(authenticated_async_context, fixture_user_id, jd)
    # 3 of 4 had must-haves named: the coverage bar fails too
    assert (got["confidence"], got["downgraded"], got["where"]) == ("weak", ["jd_term_not_in_cv", "jd_coverage_low"],
                                                                     {"jd_term_not_in_cv": [1]})


@pytest.mark.asyncio
async def test_fake_evidence_for_a_had_term_is_weak(authenticated_async_context, fixture_user_id):
    jd = [*HONEST_JD[:3], _item("ChromaDB", evidence=["Ran ChromaDB clusters for five years"], n=[6]), HONEST_JD[4]]
    got = await _review(authenticated_async_context, fixture_user_id, jd)
    assert (got["confidence"], got["where"]) == ("weak", {"jd_evidence_not_in_profile": [4]})
    assert got["downgraded"] == ["jd_evidence_not_in_profile", "jd_coverage_low"]  # an unshown term covers nothing


@pytest.mark.asyncio
async def test_a_had_term_with_no_evidence_is_weak(authenticated_async_context, fixture_user_id):
    jd = [*HONEST_JD[:3], _item("ChromaDB", n=[6]), HONEST_JD[4]]
    got = await _review(authenticated_async_context, fixture_user_id, jd)
    assert got["where"] == {"jd_evidence_not_in_profile": [4]}


@pytest.mark.asyncio
async def test_a_gap_the_cv_claims_is_weak(authenticated_async_context, fixture_user_id):
    got = await _review(authenticated_async_context, fixture_user_id,
                        [*HONEST_JD, _item("Slack", have=False, must=False)])  # the project line names Slack
    assert (got["confidence"], got["downgraded"], got["where"]) == ("weak", ["jd_gap_claimed"],
                                                                     {"jd_gap_claimed": [6]})


@pytest.mark.asyncio
async def test_coverage_below_the_bar_is_weak(authenticated_async_context, fixture_user_id):
    jd = [*HONEST_JD[:3], _item("ChromaDB", evidence=[B2]), HONEST_JD[4]]  # had, but used in no CV line: 3 of 4
    got = await _review(authenticated_async_context, fixture_user_id, jd)
    assert (got["confidence"], got["downgraded"], got["where"]) == ("weak", ["jd_coverage_low"], {})
    assert (got["jd"]["must"], got["jd"]["covered"]) == (4, 3)


@pytest.mark.asyncio
async def test_coverage_at_the_bar_is_strong(authenticated_async_context, fixture_user_id):
    jd = [*HONEST_JD[:3], _item("ChromaDB", evidence=[B2]), _item("Bedrock", evidence=[B2], n=[6]), HONEST_JD[4]]
    got = await _review(authenticated_async_context, fixture_user_id, jd)  # 4 of 5 = 0.8
    assert (got["confidence"], got["downgraded"]) == ("strong", [])
    assert (got["jd"]["must"], got["jd"]["covered"]) == (5, 4)


@pytest.mark.asyncio
async def test_no_ad_text_stores_jd_no_ad_and_is_not_weak(authenticated_async_context, fixture_user_id):
    from src.api import dependencies as api_deps

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        db = await api_deps.get_db()
        await db._db.execute("UPDATE applications SET job_description_snapshot = '' WHERE id = ?", (app_id,))
        await db._db.commit()
        got = await _stored(client, app_id, _payload(cv_id, HONEST))
    assert (got["confidence"], got["downgraded"], got["jd"]) == ("strong", [], {"state": "jd_no_ad"})


@pytest.mark.asyncio
@pytest.mark.parametrize("jd", [None, []])
async def test_ad_text_with_no_jd_is_weak_jd_not_judged(authenticated_async_context, fixture_user_id, jd):
    got = await _review(authenticated_async_context, fixture_user_id, jd)
    assert (got["confidence"], got["downgraded"], got["jd"]) == ("weak", ["jd_not_judged"], {"state": "jd_not_judged"})


@pytest.mark.asyncio
@pytest.mark.parametrize("jd", [
    [_item("x" * 81)], [_item("Python")] * 41, [{"term": "Python"}], [{**_item("MCP"), "n": [0]}],
    [{**_item("MCP"), "have": "yes"}], [{**_item("MCP"), "evidence": ["a" * 301]}], "MCP",
])
async def test_a_malformed_jd_is_refused(authenticated_async_context, fixture_user_id, jd):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        resp = await client.post(f"/api/applications/{app_id}/events",
                                 json={"event_type": "cv_reviewed", "payload": _payload(cv_id, HONEST, jd)})
    assert resp.status_code == 422, resp.text
    assert "jd" in resp.text


# Checker findings 2026-11-12 (D1 dilution, D2 omission, D3 word form).
@pytest.mark.asyncio
@pytest.mark.parametrize("dups", [
    [HONEST_JD[0]] * 20,  # D1: 20 copies of a covered term would make 1 miss of 24 pass the bar
    [_item("langgraph", evidence=[SUMMARY], n=[2])], [_item("LangGraphs", evidence=[SUMMARY], n=[2])],
])
async def test_a_duplicate_jd_term_is_refused(authenticated_async_context, fixture_user_id, dups):
    jd = [*HONEST_JD[:3], _item("ChromaDB", evidence=[B2]), HONEST_JD[4], *dups]
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        resp = await client.post(f"/api/applications/{app_id}/events",
                                 json={"event_type": "cv_reviewed", "payload": _payload(cv_id, HONEST, jd)})
    assert resp.status_code == 422, resp.text
    assert "jd[5]" in resp.text and "duplicate" in resp.text


def test_judge_counts_each_distinct_term_once():
    from src.services.applications import review_jd

    jd = [*HONEST_JD[:3], _item("ChromaDB", evidence=[B2]), HONEST_JD[4], *[HONEST_JD[0]] * 20]
    lines = cv_review.content_lines(HONEST, _profile())
    where, stored = review_jd.judge(jd, AD, lines, HONEST, _profile())
    assert (stored["must"], stored["covered"], "jd_coverage_low" in where) == (4, 3, True)


@pytest.mark.asyncio
@pytest.mark.parametrize("jd", [[dict(i, must=False) for i in HONEST_JD], [_item("Kubernetes", have=False, must=False)]])
async def test_a_jd_with_no_must_have_is_weak(authenticated_async_context, fixture_user_id, jd):
    got = await _review(authenticated_async_context, fixture_user_id, jd)  # D2: the reviewer must name the must-haves
    assert (got["confidence"], got["downgraded"]) == ("weak", ["jd_no_must"])


@pytest.mark.asyncio
@pytest.mark.parametrize(("saved", "jd", "weak"), [
    (["LangGraph", "MCP", "RAG", "ChromaDB", "Kubernetes"], [HONEST_JD[0], HONEST_JD[1]], True),  # 2 < 3
    (["LangGraph", "MCP", "RAG", "ChromaDB", "Kubernetes"], HONEST_JD[:3], False),
    (["LangGraph", "MCP"], [HONEST_JD[0], HONEST_JD[1]], False),  # min(3, 2 saved)
    (["LangGraph", "MCP"], [HONEST_JD[0]], True),
])
async def test_a_jd_shorter_than_the_saved_requirements_is_weak(authenticated_async_context, fixture_user_id,
                                                                saved, jd, weak):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        fit = await client.put(f"/api/applications/{app_id}/fit", json={"jd_requirements": saved})
        assert fit.status_code == 200, fit.text
        got = await _stored(client, app_id, _payload(cv_id, HONEST, jd))
    assert (got["confidence"], got["downgraded"]) == (("weak", ["jd_too_few"]) if weak else ("strong", []))


@pytest.mark.asyncio
async def test_the_ads_word_form_is_not_a_false_weak(authenticated_async_context, fixture_user_id):
    """D3: ad 'RAG pipelines' / 'agentic system design' / 'Lang Graph'; the CV says 'RAG pipeline' / 'agentic
    systems' / 'LangGraph'."""
    ad = "AI Engineer. Must have: RAG pipelines, agentic system design and Lang Graph."
    jd = [_item("RAG pipelines", evidence=[B2], n=[6]), _item("agentic system", evidence=[SUMMARY], n=[2]),
          _item("Lang Graph", evidence=[SUMMARY], n=[2])]
    got = await _review(authenticated_async_context, fixture_user_id, jd, ad=ad)
    assert (got["confidence"], got["downgraded"], got["where"]) == ("strong", [], {})
    assert (got["jd"]["must"], got["jd"]["covered"]) == (3, 3)


def test_java_in_the_ad_is_not_javascript_in_the_cv():
    from src.services.applications import review_jd

    cv = HONEST.replace("LangGraph, LangChain, MCP, RAG", "LangGraph, LangChain, MCP, RAG, JavaScript")
    where, stored = review_jd.judge([*HONEST_JD, _item("Java", have=False, must=False)], AD + " Java is a plus.",
                                    cv_review.content_lines(cv, _profile()), cv, _profile())
    assert (where, stored["gaps"]) == ({}, ["Kubernetes", "Java"])
