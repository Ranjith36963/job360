"""Two proven bugs from the Apply duty report (2026-11-02, live blocks).

1. Oracle HCM (apps 128 and 134): the job page's "APPLY NOW" is a <button type=submit> outside any form, on a
   page that also carries a hidden "Copy Link" box holding the job's own address and the Oracle chat widget's
   hidden file box (read live 2026-11-03 off fa-evdq-saasfaprod1.fa.ocs.oraclecloud.com/.../job/13281). The
   gate read "answers + a file box" as a form and refused the opener before any form opened. On a known job-ad
   page (``sites.job_ad_page``) a button whose WHOLE label is an opener ("APPLY NOW", "Apply for This Job")
   now opens the form; every real final submit is still gated, and anything unsure stays a submit.
2. Axmed (app 336): the form said "You already have an application for this role" and the job was tried six
   more times. ``already_applied_on_site`` is a closed blocked reason: Job360's browser records it, the job
   leaves the queue, is never retried, and Needs you asks one question; the web's "I applied" closes it.
"""
from __future__ import annotations

import contextlib
import json
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import blocked as blocked_service
from src.services.browser_agent.safety import control_kind, label_words, opener_label
from src.services.browser_agent.sites import already_applied_said, job_ad_page
from tests.test_browser_agent import _ats_pool, _ref, _resolver, _serve, needs_chromium

ORACLE_HOST = "acme.fa.ocs.oraclecloud.com"
ORACLE_JOB = f"https://{ORACLE_HOST}/hcmUI/CandidateExperience/en/sites/CX_1/job/13281"
BAMBOO_HOST = "acme.bamboohr.com"
BAMBOO_JOB = f"https://{BAMBOO_HOST}/careers/194"


# ── 1. the gate: "open the form" vs "send my application" (pure) ─────────────


def _button(name: str, *, answers: bool = True, in_form: bool = False, owns: bool = False, known_ad: bool = True,
            site_knows_user: bool = False, input_type: str = "submit") -> str:
    # Oracle's live facts: tag button, type submit, not in a form, page_has_answers true (the Copy Link box).
    return control_kind("button", input_type, name, "", in_form, "", answers, owns, False, False,
                        site_knows_user, known_ad=known_ad)


@pytest.mark.parametrize("name", ["APPLY NOW", "Apply now", "Apply for This Job", "Apply", "I'm interested"])
def test_the_oracle_and_bamboohr_first_apply_buttons_are_not_refused(name):
    assert _button(name) == "field"


@pytest.mark.parametrize("name", ["Submit application", "Submit Application", "Send application", "Send", "Finish",
                                  "Submit", "Envoyer ma candidature"])
def test_real_final_submit_buttons_are_still_refused(name):
    assert _button(name) == "submit"                       # on a known job ad, with a page holding an answer
    assert _button(name, in_form=True, owns=True) == "submit"


@pytest.mark.parametrize(
    ("name", "kw"),
    [
        ("Apply with LinkedIn", {}),                  # not an opener label: the old rules decide
        ("Apply and send", {}),
        ("Quick apply", {}),
        ("Apply now", {"known_ad": False}),           # not a known job-ad page (or it asks for something)
        ("Apply now", {"in_form": True, "owns": True}),   # a form's own button
        ("Apply now", {"owns": True}),                # can submit a form
        ("Apply now", {"site_knows_user": True}),     # a saved sign-in: a one-click page may send the profile
    ],
)
def test_an_ambiguous_button_stays_refused(name, kw):
    assert _button(name, **kw) == "submit"


def test_the_opener_labels_are_whole_labels_read_word_by_word():
    assert label_words("APPLY  NOW!") == ("apply", "now")
    assert label_words("I’m interested") == ("im", "interested")
    assert opener_label("Apply for this position") and opener_label("apply")
    assert not opener_label("Apply now and submit") and not opener_label("Submit") and not opener_label("")


def test_known_job_ad_pages_are_exact_host_and_path_shapes():
    assert job_ad_page("https://fa-evdq-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/13281")
    assert job_ad_page("https://emit.fa.ca3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/98032/")
    assert job_ad_page("https://yomobile.bamboohr.com/careers/194")
    # The apply flow, another path, or a look-alike host is not the ad.
    assert not job_ad_page("https://emit.fa.ca3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/98032/apply/email")
    assert not job_ad_page("https://yomobile.bamboohr.com/careers/194/apply")
    assert not job_ad_page("https://oraclecloud.com.evil.example/hcmUI/CandidateExperience/en/sites/CX_1/job/1")
    assert not job_ad_page("https://evil-bamboohr.com/careers/194")
    assert not job_ad_page("https://jobs.example/careers/194")


# The gate's own wiring (agent._kind): page facts as ELEMENT_FACTS_JS returns them, the page's URL and the
# server's record of typing and sign-ins.


class _Page:
    def __init__(self, url: str) -> None:
        self.url = url

    def is_closed(self) -> bool:
        return False


class _Loc:
    def __init__(self, facts: dict[str, Any]) -> None:
        self.facts = facts

    async def evaluate(self, _js: str, *_a: Any, **_k: Any) -> dict[str, Any]:
        return dict(self.facts)


# Oracle HCM's job page as read live 2026-11-03 (fa-evdq-saasfaprod1, job 13281).
ORACLE_FACTS = {
    "tag": "button", "type": "submit", "name": "APPLY NOW", "autocomplete": "", "hints": [], "in_form": False,
    "role": "", "owns_form": False, "toggle": False, "page_has_answers": True, "choice_answered": False,
    "fields_shown": 0, "required_shown": False, "file_box": True, "shown_filled": False,
}


async def _kind_on(url: str, facts: dict[str, Any], *, signed_in: frozenset[str] = frozenset(), typed: bool = False):
    from src.services.browser_agent import agent

    s = agent.Session(context=None, page=_Page(url), signed_in=set(signed_in))
    if typed:
        agent._mark_typed(s)
    return await agent._kind(_Loc(facts), s)


@pytest.mark.asyncio
async def test_the_gate_lets_oracles_apply_now_open_the_form():
    url = "https://fa-evdq-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/13281"
    assert await _kind_on(url, ORACLE_FACTS) == "field"
    # Fail closed: typed on, signed in, a visible required field, a visible filled box, another page.
    assert await _kind_on(url, ORACLE_FACTS, typed=True) == "submit"
    assert await _kind_on(url, ORACLE_FACTS, signed_in=frozenset({"fa-evdq-saasfaprod1.fa.ocs.oraclecloud.com"})) == "submit"
    assert await _kind_on(url, {**ORACLE_FACTS, "required_shown": True}) == "submit"
    assert await _kind_on(url, {**ORACLE_FACTS, "shown_filled": True}) == "submit"
    assert await _kind_on(url, {k: v for k, v in ORACLE_FACTS.items() if k != "shown_filled"}) == "submit"
    assert await _kind_on(url + "/apply/email", ORACLE_FACTS) == "submit"
    assert await _kind_on(url, {**ORACLE_FACTS, "name": "Submit"}) == "submit"


# ── 1. the same, in a real Chromium on synthetic pages ───────────────────────

ORACLE_AD = f"""<!doctype html><html><head><title>Oracle Careers</title></head><body>
<button type="button">AMERICAN ENGLISH</button>
<h1>AI Engineer</h1><p>Build agents.</p>
<div class="job-actions"><input type="text" readonly style="display:none" aria-label="Copy Link"
  value="{ORACLE_JOB}"></div>
<button class="button apply-now-button" onclick="location.href=location.pathname + '/apply/email'">APPLY NOW</button>
<p>Responsibilities ...</p>
<button class="button apply-now-button" onclick="location.href=location.pathname + '/apply/email'">APPLY NOW</button>
<div class="cookie"><button type="button">ACCEPT ALL COOKIES</button><button type="button">REJECT ALL COOKIES</button></div>
<div class="oda-chat-widget"><textarea id="oda-chat-user-text-input"></textarea>
  <input type="file" class="oda-chat-none" style="display:none"></div>
</body></html>"""

ORACLE_EMAIL = """<!doctype html><html><head><title>Apply</title></head><body>
<label>Email Address <input type="text" name="email" required></label>
<label><input type="checkbox" name="terms"> I agree with the terms</label>
<button type="button" onclick="document.title = 'NEXT'">Next</button>
</body></html>"""

# BambooHR's job page as the browser read it live (#1227): the share box holds the job's own address.
BAMBOO_AD = f"""<!doctype html><html><head><title>BambooHR</title></head><body>
<h1>Applied LLM Engineer</h1><p>You write the prompts and ship the code.</p>
<button type="button" id="apply" onclick="openForm()">Apply for This Job</button>
<label>Link to This Job <input type="text" value="{BAMBOO_JOB}"></label>
<div id="form"></div>
<script>
function openForm() {{
  document.getElementById('apply').remove();
  document.getElementById('form').innerHTML = '<label>Resume <input type="file" name="resume" required></label>'
    + '<button type="button" onclick="document.title = \\'SENT\\'">Submit Application</button>';
}}
</script>
</body></html>"""

# A real final page at a known job-ad address: the opened form, required fields, one answer in, and both
# "Submit Application" and an "Apply" that sends it.
BAMBOO_FILLED = """<!doctype html><html><head><title>BambooHR</title></head><body>
<label>First Name <input type="text" name="first" value="Ada" required></label>
<label>Email <input type="text" name="email" required></label>
<button type="button" onclick="document.title = 'SENT'">Submit Application</button>
<button type="button" onclick="document.title = 'SENT'">Apply</button>
</body></html>"""

# Axmed's form after the CV upload (app 336, 2026-10-31), and a page whose help line only mentions it.
ALREADY = """<!doctype html><html><head><title>Apply</title></head><body>
<p>You already have an application for this role with this email.</p>
<button type="button">Send me a link to my application</button></body></html>"""
HELP_LINE = """<!doctype html><html><head><title>Apply</title></head><body>
<p>If you have already applied for this job, sign in to see its status.</p>
<label>Email <input type="text" name="email"></label></body></html>"""

PAGES = {
    "/roles/x/apply": ALREADY,
    "/roles/y/apply": HELP_LINE,
    "/hcmUI/CandidateExperience/en/sites/CX_1/job/13281": ORACLE_AD,
    "/hcmUI/CandidateExperience/en/sites/CX_1/job/13281/apply/email": ORACLE_EMAIL,
    "/careers/194": BAMBOO_AD,
    "/careers/195": BAMBOO_FILLED,
}


@pytest.fixture
async def ats_site():
    from aiohttp import web

    def handler(html):
        async def serve(_):
            return web.Response(text=html, content_type="text/html")
        return serve

    app = web.Application()
    for path, html in PAGES.items():
        app.router.add_get(path, handler(html))
    # Both hosts over https, as the real sites are (bamboohr.com is on Chromium's HSTS preload list).
    runner, port, tls_port = await _serve(app)
    yield port, tls_port
    await runner.cleanup()


@pytest.fixture
async def pool(ats_site):
    resolve, _calls = _resolver({ORACLE_HOST: "93.184.216.34", BAMBOO_HOST: "93.184.216.34"})
    p = _ats_pool(resolve, ats_site)
    yield p
    await p.shutdown()


def _gate(calls: list[str]):
    from src.services.browser_agent.agent import Gate

    async def check(form_url):
        calls.append(form_url)
        return {"decision": "fill", "reason": "form_not_recorded", "detail": "Fill the form first."}

    async def mark():
        calls.append("submit_attempted")

    return Gate(check=check, mark_attempt=mark)


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_oracle_apply_now_opens_the_apply_flow_ungated_in_chromium(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url=ORACLE_JOB)
    calls: list[str] = []
    out = await run(pool, "u1", "click", ref=_ref(page, "APPLY NOW"), gate=_gate(calls))
    assert out["status"] == "ok", out
    assert calls == []                                   # check_submit was never asked: it opens the form
    assert out["url"].endswith("/job/13281/apply/email")
    assert any(e["name"].startswith("Email Address") for e in out["elements"])


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_bamboohr_apply_for_this_job_opens_the_form_and_its_submit_stays_gated(pool):
    from src.services.browser_agent.agent import run

    page = await run(pool, "u1", "open", url=BAMBOO_JOB)
    calls: list[str] = []
    out = await run(pool, "u1", "click", ref=_ref(page, "Apply for This Job"), gate=_gate(calls))
    assert out["status"] == "ok" and calls == [], out
    refused = await run(pool, "u1", "click", ref=_ref(out, "Submit Application"), gate=_gate(calls))
    assert refused["status"] == "refused" and refused["reason"] == "check_submit", refused
    assert calls == [BAMBOO_JOB] and (await run(pool, "u1", "look"))["title"] == "BambooHR"   # never sent


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_a_filled_page_at_a_job_ad_address_keeps_every_submit_gated(pool):
    from src.services.browser_agent.agent import run

    url = f"https://{BAMBOO_HOST}/careers/195"
    for name in ("Submit Application", "Apply"):
        page = await run(pool, "u1", "open", url=url)
        calls: list[str] = []
        out = await run(pool, "u1", "click", ref=_ref(page, name), gate=_gate(calls))
        assert out["status"] == "refused" and calls == [url], (name, out)
        assert (await run(pool, "u1", "look"))["title"] == "BambooHR"


@pytest.mark.parametrize(("fields_shown", "is_ad"), [(1, True), (2, True), (3, False), (7, False)])
def test_an_opened_form_at_the_ads_address_is_not_a_known_ad(monkeypatch, fields_shown, is_ad):
    """The opened form lives at the same /careers/<id>: MIN_LABELLED_FIELDS or more visible fields, nothing
    required or typed. ``_known_ad`` reads it as a form (as ``page_is_ad`` does), so the opener rule never
    applies to it; the share box of the real ad (one field) still reads as the ad."""
    from types import SimpleNamespace

    from src.services.browser_agent import agent

    monkeypatch.setattr(agent, "_opener_allowed", lambda s: True)
    monkeypatch.setattr(agent, "_live_page", lambda s: SimpleNamespace(url=f"https://{BAMBOO_HOST}/careers/196"))
    facts = {"required_shown": False, "shown_filled": False, "fields_shown": fields_shown}
    assert agent._known_ad(SimpleNamespace(), facts) is is_ad
    assert agent._known_ad(SimpleNamespace(), {k: v for k, v in facts.items() if k != "fields_shown"}) is False


# ── 2. "the site says you already applied" ───────────────────────────────────


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("Upload CV. You already have an application for this role with this email.", True),   # Axmed, live
        ("You have already applied for this job.", True),
        ("You’ve already applied to this position", True),
        ("It looks like you already applied for this role.", True),
        ("If you have already applied for this job, sign in to see its status.", False),   # a help line
        ("Check whether you have already applied for this role.", False),
        ("Apply now. Already a candidate? Sign in.", False),
        ("", False),
    ],
)
def test_the_already_applied_phrases_are_read_word_by_word(text, said):
    assert already_applied_said(text) is said


def test_already_applied_is_a_closed_reason_never_retried():
    assert blocked_service.ALREADY_APPLIED == "already_applied_on_site"
    assert "already_applied_on_site" in blocked_service.BLOCKED_REASONS
    assert "already_applied_on_site" in blocked_service.NEVER_RETRIED
    assert blocked_service.ask_question({"reason": "already_applied_on_site", "page_host": "x.example"}) == (
        "The site says you already applied. Mark it applied?"
    )


async def _bring(client: AsyncClient) -> tuple[int, int]:
    resp = await client.post("/api/jobs/bring", json={
        "title": "AI Engineer", "company": "Axmed", "location": "Remote", "country": "GB", "remote": True,
        "apply_url": "https://axmed.firststage.co/roles/x/apply", "description": "Build agents. " * 5,
    })
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"]), int(resp.json()["job"]["id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]):
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})


async def _rows(user_id: str, app_id: int, event_type: str) -> list[dict[str, Any]]:
    from src.api.dependencies import get_request_db

    async with contextlib.asynccontextmanager(get_request_db)() as db:
        cur = await db._db.execute(
            "SELECT payload FROM application_events WHERE user_id = ? AND application_id = ? AND event_type = ? "
            "ORDER BY id", (user_id, app_id, event_type),
        )
        return [json.loads(r["payload"]) for r in await cur.fetchall()]


async def _open_asks(client: AsyncClient, app_id: int) -> list[dict[str, Any]]:
    resp = await client.get("/api/asks", params={"status": "open"})
    assert resp.status_code == 200, resp.text
    return [a for a in resp.json()["asks"] if a["application_id"] == app_id]


@needs_chromium
@pytest.mark.real_sleep
@pytest.mark.asyncio
async def test_job360s_browser_reads_an_already_applied_page_as_a_stop(pool):
    from src.services.browser_agent.agent import run

    out = await run(pool, "u1", "open", url=f"https://{BAMBOO_HOST}/roles/x/apply")
    assert (out["status"], out["reason"]) == ("needs_you", "already_applied"), out
    help_line = await run(pool, "u1", "open", url=f"https://{BAMBOO_HOST}/roles/y/apply")
    assert help_line["status"] == "ok", help_line


@pytest.mark.asyncio
async def test_an_already_applied_page_records_the_reason(authenticated_async_context, monkeypatch):
    """Job360's browser reads the page (look), stops, and the route records the closed reason itself."""
    from src.core import settings
    from src.services.browser_agent import agent

    monkeypatch.setattr(settings, "BROWSER_AGENT_ENABLED", True)
    page = "https://axmed.firststage.co/roles/x/apply"

    async def fake_run(_pool, _user_id, _action, **_kw):  # what look() answers on Axmed's page
        assert already_applied_said("You already have an application for this role")
        return {"status": "needs_you", "reason": agent.ALREADY_APPLIED, "detail": agent.ALREADY_APPLIED_DETAIL,
                "url": page, "elements": [], "text": ""}

    monkeypatch.setattr(agent, "run", fake_run)
    async with authenticated_async_context() as client:
        app_id, _job = await _bring(client)
        assert (await _event(client, app_id, "queued", {})).status_code == 201
        out = (await client.post("/api/browser", json={"action": "look", "application_id": app_id})).json()
        assert out["blocked_recorded"] is True
        again = (await client.post("/api/browser", json={"action": "look", "application_id": app_id})).json()
        assert again["blocked_recorded"] is False                       # once per open block
        user_id = authenticated_async_context.fixture_user_id
        (row,) = await _rows(user_id, app_id, "blocked")
        assert row["reason"] == "already_applied_on_site" and row["by"] == "browser"
        assert row["page_host"] == "axmed.firststage.co"
        assert await _rows(user_id, app_id, "unqueued") == [{"reason": "blocked"}]   # it left the queue


@pytest.mark.asyncio
async def test_the_job_leaves_the_queue_and_is_not_retried(authenticated_async_context):
    from src.api.dependencies import get_request_db
    from src.services.applications import queue_preflight, spine

    async with authenticated_async_context() as client:
        app_id, _job = await _bring(client)
        assert (await _event(client, app_id, "queued", {})).status_code == 201
        # The apply run records it itself (record_event blocked).
        resp = await _event(client, app_id, "blocked", {"reason": "already_applied_on_site", "step": "upload CV",
                                                         "page_host": "axmed.firststage.co"})
        assert resp.status_code == 201, resp.text
        user_id = authenticated_async_context.fixture_user_id
        async with contextlib.asynccontextmanager(get_request_db)() as db:
            assert app_id not in await spine.queued_at_by_app(db, user_id, [app_id])
            assert app_id not in {r["id"] for r in await queue_preflight.fillable_rows(db, user_id)}
            # Clearing it never puts it back on the queue.
            assert await queue_preflight.back_to_queue(
                db, user_id, app_id, "already_applied_on_site", recorded_by="web") is False
        retried = await _event(client, app_id, "unblocked", {"resolution": "retried"})
        assert retried.status_code == 422 and "never retried" in retried.text
        # A second report while it is open opens no second ask and no second unqueue.
        await _event(client, app_id, "blocked", {"reason": "already_applied_on_site"})
        assert len(await _open_asks(client, app_id)) == 1
        assert await _rows(user_id, app_id, "unqueued") == [{"reason": "blocked"}]


@pytest.mark.asyncio
async def test_needs_you_shows_the_one_question_and_i_applied_closes_it(authenticated_async_context):
    from src.api import dependencies as api_deps
    from src.services.applications import blockers, needs_you_count

    async with authenticated_async_context() as client:
        app_id, job_id = await _bring(client)
        await _event(client, app_id, "queued", {})
        resp = await _event(client, app_id, "blocked", {"reason": "already_applied_on_site",
                                                         "page_host": "axmed.firststage.co"})
        assert resp.status_code == 201, resp.text
        (ask,) = await _open_asks(client, app_id)
        assert ask["question"] == "The site says you already applied. Mark it applied?"
        user_id = authenticated_async_context.fixture_user_id
        db = await api_deps.get_db()
        rows = (await blockers.blockers_for(db, user_id, [app_id]))[app_id]
        assert [(b["kind"], b["actor"], b["label"]) for b in rows] == [("site_step", "you", "mark it applied")]
        assert rows[0]["ref"]["ask_id"] == ask["id"]
        # The user's web "I applied" moves the job on; the page closes the question on its next load.
        assert (await client.post(f"/api/receipts/{job_id}", json={"note": "the site said so"})).status_code == 201
        assert await _open_asks(client, app_id) == []
        counted = await needs_you_count.needs_you_count(db, user_id)
        assert app_id not in counted.get("job_ids", [])


@pytest.mark.asyncio
async def test_only_the_user_can_withdraw_the_already_applied_question(authenticated_async_context):
    """Owner rule (2026-11-01, #1442): an assistant may not clear a question only the user can answer. The
    "already applied" ask has no kind and no keyed questions, so it was outside that rule (agent got 200)."""
    from tests.test_application_kit import _bearer_client, _mint_token

    async with authenticated_async_context() as client:
        app_id, _job_id = await _bring(client)
        token = await _mint_token(client)
        async with _bearer_client(token) as agent:
            await _event(agent, app_id, "blocked", {"reason": "already_applied_on_site"})
            (ask,) = await _open_asks(client, app_id)
            assert ask["question"] == blocked_service.ALREADY_APPLIED_QUESTION
            refused = await agent.post(f"/api/asks/{ask['id']}/withdraw", json={"reason": "stale"})
            assert refused.status_code == 409, refused.text
            assert len(await _open_asks(client, app_id)) == 1, "the question stays for the user"
        mine = await client.post(f"/api/asks/{ask['id']}/withdraw", json={"reason": "not needed"})
        assert mine.status_code == 200, mine.text
        assert await _open_asks(client, app_id) == []
