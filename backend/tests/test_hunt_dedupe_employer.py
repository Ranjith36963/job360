"""The 2026-11-18 16:13 hunt run (cse_01KTmoH9qtgRPXQvJaSu2YPi) replayed: Job360 enforces what its recipe said.

* Known list by company + title: the run's own filter (recipe step 1 key) kept "Lloyds Bank" / "Machine Learning &
  AI Engineer" although application 1036 is "Lloyds Banking Group" with the same title, and Stratos / Evnek against
  "STRATOS Technologies" (892) / "Evnek Technologies" (763). bring_job created application 1059 for Lloyds (only a
  `possible_duplicate` warning) and the run unqueued it by hand. Now one key (`company_title_key`) is shared by
  bring_job's duplicate guard and the kit's `--known-ct`, and an agent re-bring of it returns the open application.
* employer_search "not_found" was sent for SKO, N-Labs, SRG and Opus after one generic web search (SKO) or none:
  it now carries `employer_searched` with at least one careers page / ATS link.
* The run marked the hunt done at 16:31 while the kit ran until 16:41: the kit writes `<out>.done.json` when it
  ends and mark_done's kit report copies it (`kit.done`).
Real routes and the MCP doors on the real Postgres; the kit runs offline on a fake HTTP layer.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.api import dependencies as api_deps

KIT_DIR = Path(__file__).resolve().parents[1] / "src" / "hunt_kit"


def _load_kit() -> ModuleType:
    if str(KIT_DIR) not in sys.path:
        sys.path.insert(0, str(KIT_DIR))
    spec = importlib.util.spec_from_file_location("hunt_kit_dedupe_under_test", KIT_DIR / "hunt_kit.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


hk = _load_kit()

_LBG_DESC = "Transaction Classification Services lab. Deploy ML models into production on GCP. " * 6
LLOYDS_1036 = {
    "title": "Machine Learning & AI Engineer", "company": "Lloyds Banking Group", "location": "London (hybrid)",
    "apply_url": "https://lbg.wd3.myworkdayjobs.com/broadbean_external/job/London/Machine-Learning---AI-Engineer_163254-3",
    "description": _LBG_DESC, "origin": "ai_hunt",
}
LLOYDS_1059 = {
    **LLOYDS_1036, "company": "Lloyds Bank", "location": "London, United Kingdom (hybrid)",
    "apply_url": "https://lbg.wd3.myworkdayjobs.com/LBG_Careers/job/London/Machine-Learning---AI-Engineer_163254-2",
    "found_on": "company_careers", "queue": True,
}
STRATOS_892 = {
    "title": "Forward Deployed Engineer", "company": "STRATOS Technologies", "location": "London (Farringdon)",
    "apply_url": "https://stratos.example/careers/fde", "description": "Palantir Foundry delivery. " * 8,
}
STRATOS_KIT = {**STRATOS_892, "title": "Forward Deployed Engineer (Graduate)", "company": "Stratos",
               "location": "London", "apply_url": "https://careers.stratos.example/jobs/195694", "origin": "ai_hunt"}
EVNEK_763 = {
    "title": "Junior Generative AI Engineer", "company": "Evnek Technologies", "location": "Bengaluru",
    "apply_url": "https://evnek.example/careers/genai-1", "description": "RAG and agents, 0-1 years. " * 8,
}
EVNEK_KIT = {**EVNEK_763, "company": "Evnek", "apply_url": "https://careers.evnek.example/jobs/171126934034",
             "origin": "ai_hunt"}


async def _agent(client: AsyncClient) -> AsyncClient:
    from src.api.main import app

    token = (await client.post("/api/tokens", json={"name": "scheduler"})).json()["token"]
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _count_apps() -> int:
    db = await api_deps.get_db()
    cur = await db._conn.execute("SELECT COUNT(*) FROM applications")
    return int((await cur.fetchone())[0])


# ── 1. the same job, another company label ───────────────────────────────────────────────────────────────────


def test_company_title_key_matches_the_three_1613_pairs_and_keeps_others_apart() -> None:
    from src.services.applications.reposts import company_title_key as key

    assert key("Lloyds Bank", "Machine Learning & AI Engineer") == key(
        "Lloyds Banking Group", "Machine Learning & AI Engineer")
    # Pattern audit finding 6 (owner rule: a pattern hints, Claude judges): a qualifier is part of the role.
    assert key("Stratos", "Forward Deployed Engineer (Graduate)") != key("STRATOS Technologies",
                                                                         "Forward Deployed Engineer")
    assert key("Evnek", "Junior Generative AI Engineer") == key("Evnek Technologies Pvt Ltd",
                                                                "Junior Generative AI Engineer")
    assert key("Capital One", "AI Engineer") != key("Capital Group", "AI Engineer")
    assert key("Lloyds Bank", "AI Engineer") != key("Lloyds Bank", "ML Engineer")
    assert key("Bank", "AI Engineer") == "bank|ai engineer|", "a name that is all suffix keeps its one word"


@pytest.mark.parametrize("pair", [
    ("Lloyds Banking Group", "Machine Learning & AI Engineer", "London (hybrid)"),
    ("Lloyds Bank", "Machine Learning & AI Engineer", "London, United Kingdom (hybrid)"),
    ("STRATOS Technologies", "Forward Deployed Engineer", "London (Farringdon), United Kingdom (Hybrid)"),
    ("Stratos", "Forward Deployed Engineer (Graduate)", "London"),
    ("Evnek Technologies", "Junior Generative AI Engineer", "Bengaluru, Karnataka, India"),
    ("Opus Recruitment Solutions Ltd", "AI Engineer", "Remote"),
    ("The Hut Group", "AI Engineer - Manchester, UK", "Manchester; UK"), ("Café Labs GmbH", "KI-Ingenieur | Berlin", ""),
    ("Acme", "Software Engineer (Backend)", "London"), ("Acme", "Software Engineer (London)", "London, UK"),
    ("Acme", "Data Engineer - Remote", ""), ("Acme", "Data Engineer - Platform (Remote)", "Leeds"),
    ("", "", ""),
])
def test_the_kit_and_bring_job_share_one_key(pair: tuple[str, str, str]) -> None:
    from src.services.applications.reposts import company_title_key

    assert hk.company_title_key(*pair) == company_title_key(*pair)
    assert hk.company_title_key(*pair[:2]) == company_title_key(*pair[:2])


def test_the_place_tells_two_known_jobs_apart_only_when_both_name_one() -> None:
    from src.services.applications.reposts import company_title_key as key
    from src.services.applications.reposts import known_keys_match as match

    london = key("Citi", "AI Engineer", "London, United Kingdom")
    assert not match(key("Citi", "AI Engineer", "Pune, India"), london), "same company and title, another city"
    assert match(key("Citigroup Bank", "AI Engineer", "London (hybrid)"), key("Citigroup", "AI Engineer", "London"))
    assert match(key("Citi", "AI Engineer", ""), london) and match(london, key("Citi", "AI Engineer", "Remote")), (
        "a side that names no place matches on company and role")
    assert not match(key("Citi", "ML Engineer", "London"), london)


def test_a_qualifier_is_another_role_and_only_place_text_is_dropped() -> None:
    from src.services.applications.reposts import company_title_key as key
    from src.services.applications.reposts import known_keys_match as match

    backend, frontend = key("Acme", "Software Engineer (Backend)", "London"), key("Acme", "Software Engineer (Frontend)",
                                                                                   "London")
    assert backend == "acme|software engineer backend|london" and not match(backend, frontend)
    assert key("Acme", "Software Engineer (London)", "London, UK") == key("Acme Ltd", "Software Engineer", "London")
    assert key("Acme", "Software Engineer (Remote)", "") == key("Acme", "Software Engineer - Remote", "") == (
        "acme|software engineer|")
    assert key("Acme", "Data Engineer - Platform (Remote)", "Leeds") == "acme|data engineer platform|leeds"


BACKEND = {"title": "Software Engineer (Backend)", "company": "Acme", "location": "London",
           "apply_url": "https://acme.example/careers/backend", "description": "Python services. " * 8,
           "origin": "ai_hunt"}
FRONTEND = {**BACKEND, "title": "Software Engineer (Frontend)", "apply_url": "https://acme.example/careers/frontend",
            "description": "React and TypeScript. " * 8}


@pytest.mark.asyncio
async def test_backend_and_frontend_at_one_company_are_two_jobs_with_a_hint(authenticated_async_context):
    async with authenticated_async_context() as client, await _agent(client) as agent:
        first = (await agent.post("/api/jobs/bring", json=BACKEND)).json()
        second = await agent.post("/api/jobs/bring", json=FRONTEND)
        apps = await _count_apps()
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["existing"] is False and body["duplicate_of"] is None and apps == 2, "never refused"
    hints = [w for w in body["warnings"] if w["code"] == "possible_duplicate"]
    assert hints and hints[0]["application_ids"] == [first["application_id"]], "Claude compares the two ads"


CITI_LONDON = {"title": "AI Engineer", "company": "Citi", "location": "London, United Kingdom",
               "apply_url": "https://citi.wd5.myworkdayjobs.com/2/job/London/AI-Engineer_1",
               "description": "Agentic AI in the controls team. " * 8, "origin": "ai_hunt"}


@pytest.mark.asyncio
async def test_bring_job_keeps_the_same_role_in_another_city_and_refuses_it_in_the_same_city(
    authenticated_async_context,
):
    async with authenticated_async_context() as client, await _agent(client) as agent:
        first = (await agent.post("/api/jobs/bring", json=CITI_LONDON)).json()
        pune = await agent.post("/api/jobs/bring", json={
            **CITI_LONDON, "company": "Citi Bank", "location": "Pune, Maharashtra, India",
            "apply_url": "https://citi.wd5.myworkdayjobs.com/2/job/Pune/AI-Engineer_2"})
        london = await agent.post("/api/jobs/bring", json={
            **CITI_LONDON, "company": "Citi Bank", "location": "London (hybrid)",
            "apply_url": "https://citi.wd5.myworkdayjobs.com/2/job/London/AI-Engineer_3"})
        apps = await _count_apps()
    assert pune.status_code == 200 and pune.json()["existing"] is False, pune.text
    assert london.json()["duplicate_of"] == first["application_id"] and apps == 2


@pytest.mark.asyncio
async def test_the_lloyds_rebring_under_another_label_returns_application_1036(authenticated_async_context):
    async with authenticated_async_context() as client, await _agent(client) as agent:
        first = (await agent.post("/api/jobs/bring", json=LLOYDS_1036)).json()
        again = await agent.post("/api/jobs/bring", json=LLOYDS_1059)
        apps = await _count_apps()
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["existing"] is True and body["duplicate_of"] == first["application_id"]
    assert body["matched_on"] == "same_company_and_role" and apps == 1, "nothing new was made"
    assert "not_duplicate_of" in body["next"] and "not_duplicate_reason" in body["next"]


@pytest.mark.asyncio
async def test_evnek_stays_caught_and_the_stratos_graduate_role_is_a_hint(authenticated_async_context):
    async with authenticated_async_context() as client, await _agent(client) as agent:
        s0 = (await agent.post("/api/jobs/bring", json=STRATOS_892)).json()
        e0 = (await agent.post("/api/jobs/bring", json=EVNEK_763)).json()
        s1 = await agent.post("/api/jobs/bring", json=STRATOS_KIT)
        e1 = await agent.post("/api/jobs/bring", json=EVNEK_KIT)
        apps = await _count_apps()
    # "(Graduate)" is a qualifier: brought, with a possible_duplicate hint naming 892's twin (finding 6).
    assert s1.json()["duplicate_of"] is None
    assert [w["application_ids"] for w in s1.json()["warnings"] if w["code"] == "possible_duplicate"] == [
        [s0["application_id"]]]
    assert e1.status_code in (200, 409) and str(e0["application_id"]) in e1.text
    assert apps == 3


@pytest.mark.asyncio
async def test_a_stated_difference_overrides_the_guard_and_logs_codes_only(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    async with authenticated_async_context() as client, await _agent(client) as agent:
        first = (await agent.post("/api/jobs/bring", json=LLOYDS_1036)).json()
        no_reason = await agent.post("/api/jobs/bring", json={**LLOYDS_1059, "not_duplicate_of": first["application_id"]})
        bad_reason = await agent.post("/api/jobs/bring", json={**LLOYDS_1059, "not_duplicate_of": first["application_id"],
                                                               "not_duplicate_reason": "I think so"})
        wrong_id = await agent.post("/api/jobs/bring", json={**LLOYDS_1059, "not_duplicate_of": 999999,
                                                             "not_duplicate_reason": "different_team"})
        ok = await agent.post("/api/jobs/bring", json={**LLOYDS_1059, "not_duplicate_of": first["application_id"],
                                                       "not_duplicate_reason": "different_team"})
        apps = await _count_apps()
    assert no_reason.status_code == 422 and bad_reason.status_code == 422
    assert wrong_id.status_code == 200 and wrong_id.json()["duplicate_of"] == first["application_id"]
    assert ok.status_code == 200 and ok.json()["existing"] is False and apps == 2
    logged = [r for r in caplog.records if r.getMessage() == "job_bring_duplicate_override"]
    assert len(logged) == 1 and logged[0].reason == "different_team"
    assert logged[0].duplicate_of == first["application_id"]
    assert "Lloyds" not in str(logged[0].__dict__), "codes and ids only"


def test_the_kit_drops_known_company_and_title_and_writes_its_done_file(tmp_path, monkeypatch) -> None:
    long_text = "<p>" + "Build agents in Python with RAG and evaluation. " * 30 + "</p>"
    texts = {"acme": long_text, "beta": "<p>Build agents.</p>", "gamma": long_text}
    # gamma is known in Pune only: the London ad is another job and stays (2026-11-18 review of #1665).

    def _get(url: str, params: Any = None, retries: int = 0, data: Any = None) -> tuple[int, Any]:
        token = url.split("/boards/")[1].split("/")[0]
        return 200, {"jobs": [{"title": "AI Engineer", "absolute_url": f"https://boards.example/{token}/1",
                               "location": {"name": "London, United Kingdom"}, "content": texts[token],
                               "company_name": token.upper()}]}

    monkeypatch.setattr(hk, "load_companies", lambda p: [
        {"name": t.upper(), "ats": "greenhouse", "token": t, "source": "user"} for t in texts])
    monkeypatch.setattr(hk, "http_get_json", _get)
    known_ct = tmp_path / "known_ct.txt"
    known_ct.write_text("Acme Technologies Ltd ||| AI Engineer ||| 7 ||| London (hybrid)\n"
                        "Gamma Labs ||| AI Engineer ||| 8 ||| Pune, India\n", encoding="utf-8")
    out = tmp_path / "found.jsonl"
    argv = ["--sources", "greenhouse", "--no-universe", "--work-countries", "GB", "--titles", "ai engineer",
            "--history", "none", "--workers", "1", "--out", str(out), "--known-ct", str(known_ct)]
    assert hk.main(argv) == 0
    kept = [json.loads(line)["company"] for line in out.read_text(encoding="utf-8").splitlines()]
    assert sorted(kept) == ["BETA", "GAMMA"]
    done = json.loads(Path(str(out) + ".done.json").read_text(encoding="utf-8"))
    assert done == {"results": 2, "full": 1, "snippet": 1, "empty": 0, "known_skipped": 0,
                    "known_ct_skipped": 1, "complete": True, "stop_reason": None}


def test_the_kit_reads_old_three_column_rows_and_place_rows(tmp_path) -> None:
    rows = tmp_path / "known_ct.txt"
    rows.write_text("Citi ||| AI Engineer ||| 1\nLloyds Banking Group ||| Machine Learning & AI Engineer ||| 1036 "
                    "||| London (hybrid)\n", encoding="utf-8")
    known = hk.load_known_ct(str(rows))
    assert hk.known_ct_hit(known, "Citi", "AI Engineer", "Pune, India"), "an old row has no place: company + role"
    assert hk.known_ct_hit(known, "Lloyds Bank", "Machine Learning & AI Engineer", "London, United Kingdom")
    assert not hk.known_ct_hit(known, "Lloyds Bank", "Machine Learning & AI Engineer", "Leeds, United Kingdom")


def test_the_kit_drops_only_an_identical_key_and_hints_a_qualifier(tmp_path, monkeypatch) -> None:
    long_text = "<p>" + "Build services in Python with tests and reviews. " * 30 + "</p>"
    jobs = {"acme": "Software Engineer (Frontend)", "lloyds": "Machine Learning & AI Engineer"}
    names = {"acme": "Acme", "lloyds": "Lloyds Bank"}

    def _get(url: str, params: Any = None, retries: int = 0, data: Any = None) -> tuple[int, Any]:
        token = url.split("/boards/")[1].split("/")[0]
        return 200, {"jobs": [{"title": jobs[token], "absolute_url": f"https://boards.example/{token}/1",
                               "location": {"name": "London, United Kingdom"}, "content": long_text,
                               "company_name": names[token]}]}

    monkeypatch.setattr(hk, "load_companies", lambda p: [
        {"name": names[t], "ats": "greenhouse", "token": t, "source": "user"} for t in jobs])
    monkeypatch.setattr(hk, "http_get_json", _get)
    known_ct = tmp_path / "known_ct.txt"
    known_ct.write_text("Acme ||| Software Engineer (Backend) ||| 7 ||| London\n"
                        "Lloyds Banking Group ||| Machine Learning & AI Engineer ||| 1036 ||| London (hybrid)\n",
                        encoding="utf-8")
    out = tmp_path / "found.jsonl"
    argv = ["--sources", "greenhouse", "--no-universe", "--work-countries", "GB", "--every-title", "--titles",
            "engineer", "--history", "none", "--workers", "1", "--out", str(out), "--known-ct", str(known_ct)]
    assert hk.main(argv) == 0
    kept = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert [(j["company"], j["title"]) for j in kept] == [("Acme", "Software Engineer (Frontend)")], "Lloyds dropped"
    assert kept[0]["possible_duplicate"] == ["7"], "a hint for Claude to compare, never a drop"
    assert json.loads(Path(str(out) + ".done.json").read_text(encoding="utf-8"))["known_ct_skipped"] == 1


def test_the_kit_text_class_reads_snippets_and_cuts() -> None:
    assert hk.text_class("") == "empty" and hk.text_class("   ") == "empty"
    assert hk.text_class("x" * 500) == "snippet", "freehire cuts at exactly 500 characters"
    assert hk.text_class("x" * 900 + "…") == "snippet" and hk.text_class("x" * 900 + "...") == "snippet"
    assert hk.text_class("x" * 501) == "full"


# ── 2. employer_search "not_found" shows what was searched ──────────────────────────────────────────────────

SKO_1060 = {"title": "Trainee AI Engineer", "company": "SKO Systems", "location": "Pune, India",
            "apply_url": "https://www.shine.com/jobs/ai-engineer/sko-systems/455621",
            "description": "Support the strategic and technical implementation of AI initiatives. " * 4,
            "origin": "ai_hunt", "employer_search": "not_found"}


@pytest.mark.asyncio
async def test_not_found_without_a_careers_or_ats_search_is_refused(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    only_search_and_boards = ["SKO Systems Pune Trainee AI Engineer careers",
                              "https://www.google.com/search?q=sko+systems+careers",
                              "https://www.shine.com/jobs/ai-engineer/sko-systems/455621"]
    async with authenticated_async_context() as client, await _agent(client) as agent:
        bare = await agent.post("/api/jobs/bring", json=SKO_1060)
        weak = await agent.post("/api/jobs/bring", json={**SKO_1060, "employer_searched": only_search_and_boards})
        apps = await _count_apps()
    for resp in (bare, weak):
        assert resp.status_code == 422, resp.text
        assert "employer_searched" in resp.json()["detail"] and "careers" in resp.json()["detail"]
    assert apps == 0
    refused = [r for r in caplog.records if r.getMessage() == "job_bring_board_refused"]
    assert [r.reason for r in refused] == ["employer_search_unproven", "employer_search_unproven"]
    assert (refused[1].urls, refused[1].careers_urls, refused[1].queries) == (2, 0, 1)
    assert "sko" not in str(refused[1].__dict__).lower(), "counts only, never caller text"


@pytest.mark.asyncio
async def test_not_found_with_a_careers_page_or_ats_search_brings(authenticated_async_context):
    searched = ["SKO Systems careers", "https://skosystems.example/careers",
                "https://boards.greenhouse.io/skosystems"]
    async with authenticated_async_context() as client, await _agent(client) as agent:
        brought = await agent.post("/api/jobs/bring", json={**SKO_1060, "employer_searched": searched})
    assert brought.status_code == 200, brought.text
    assert [w["code"] for w in brought.json()["warnings"]] == ["board_link"]


@pytest.mark.asyncio
async def test_the_mcp_bring_job_takes_the_new_fields(authenticated_async_context, fixture_user_id):
    from src.api import mcp_server as m
    from src.api.auth_deps import CurrentUser

    tools = m.build_server()._tool_manager
    token = m._current_user.set(
        CurrentUser(id=fixture_user_id, email="e2e@example.com", email_verified=True, auth_via="token", actor_name="cc")
    )
    try:
        async with authenticated_async_context():
            fn = tools.get_tool("bring_job").fn
            first = await fn(**{k: v for k, v in LLOYDS_1036.items()})
            dup = await fn(**{k: v for k, v in LLOYDS_1059.items()})
            other = await fn(**{**LLOYDS_1059, "location": "Edinburgh, United Kingdom",
                                "apply_url": "https://lbg.wd3.myworkdayjobs.com/LBG_Careers/job/Edinburgh/MLAI_9"})
            sko = await fn(**SKO_1060, employer_searched=["https://jobs.lever.co/skosystems"])
    finally:
        m._current_user.reset(token)
    assert dup["duplicate_of"] == first["application_id"]
    assert other["existing"] is False and sko["application_id"]


# ── 3. the kit finishes inside the run ──────────────────────────────────────────────────────────────────────

_RAN = {"calls": 1, "ads": 30, "brought": 1}
REPORT_1613: dict[str, Any] = {
    "kit": {"new": 336, "prefilter_passed": 336, "read_in_full": 274, "results": 369, "snippet_only": 62,
            "unreadable": 0},
    "sources": {"browser": {"ads": 25, "brought": 0, "calls": 2}, "connectors": {"ads": 8, "brought": 0, "calls": 2},
                "kit": {"ads": 369, "brought": 5, "calls": 1}, "mail": {"ads": 10, "brought": 0, "calls": 2},
                "web": {"ads": 0, "brought": 0, "calls": 3}},
}
DONE_FILE = {"results": 372, "full": 300, "snippet": 63, "empty": 9, "complete": False}


async def _done(agent: AsyncClient, report: dict[str, Any]):
    return await agent.post("/api/schedule/done", json={"duty": "hunt", "outcome": "partial", "report": report})


@pytest.mark.asyncio
async def test_the_1613_report_without_the_kits_done_file_is_refused(authenticated_async_context, caplog):
    caplog.set_level(logging.INFO)
    finished = {**REPORT_1613, "kit": {**REPORT_1613["kit"], "results": 372, "done": DONE_FILE}}
    bad_sum = {**finished, "kit": {**finished["kit"], "done": {**DONE_FILE, "empty": 0}}}
    stale = {**finished, "kit": {**finished["kit"], "results": 369}}
    async with authenticated_async_context() as client, await _agent(client) as agent:
        replay = await _done(agent, REPORT_1613)
        summed = await _done(agent, bad_sum)
        mismatch = await _done(agent, stale)
        ok = await _done(agent, finished)
    assert replay.status_code == 422 and ".done.json" in replay.json()["detail"]
    assert "last log line" in replay.json()["detail"]
    assert summed.status_code == 422 and "full + snippet + empty" in summed.json()["detail"]
    assert mismatch.status_code == 422 and "372" in mismatch.json()["detail"]
    assert ok.status_code == 200, ok.text
    refused = [r.codes for r in caplog.records if r.getMessage() == "hunt_report_refused"]
    assert refused == ["kit_not_done", "kit_done_sum", "kit_done_results"]
    logged = [r for r in caplog.records if r.getMessage() == "hunt_report_ok"][0]
    assert (logged.kit_done_full, logged.kit_done_snippet, logged.kit_done_empty) == (300, 63, 9)
    assert logged.kit_done_complete is False


@pytest.mark.asyncio
async def test_a_skipped_kit_needs_no_done_file(authenticated_async_context):
    report = {"sources": {**REPORT_1613["sources"], "kit": {"skipped": {"code": "tool_missing",
                                                                         "detail": "no Python in this client"}}}}
    async with authenticated_async_context() as client, await _agent(client) as agent:
        assert (await _done(agent, report)).status_code == 200


# ── 4. the recipe ───────────────────────────────────────────────────────────────────────────────────────────


def test_the_hunt_recipe_starts_the_kit_first_and_waits_for_its_done_file():
    from src.api.routes.recipes import load_recipe

    text = load_recipe("hunt").text
    flat = " ".join(text.split())
    assert len(text.splitlines()) <= 70
    assert "--known-ct known_ct.txt" in flat and "--known known.txt" in flat
    assert "--max-minutes 20" in flat and "FIRST" in flat and "2 minutes" in flat
    assert ".done.json" in flat and "kit.done" in flat
    assert "employer_searched" in flat and "greenhouse" in flat and "lever" in flat and "ashby" in flat
    assert "not_duplicate_of" in flat
    # 3b: no-key MCP servers answer plain JSON-RPC; freehire's needs a token and the kit reads it already.
    assert "https://www.foundrole.com/ojcp/mcp" in flat and "search_jobs" in flat
    assert "https://mcp.himalayas.app/mcp" in flat and "get_jobs" in flat and "tools/call" in flat
    assert "freehire.me/api/v1/mcp" not in flat and "missing connector" in flat
