"""Needs you: a FIXED set of filters, always present, a job in EVERY filter it has an owner need in (owner 2026-11-13).

Before: the page's chips were the kinds of each job's FIRST blocker, hidden at zero; a job with a CAPTCHA, a CV to
approve and a form to send showed under only one. Now ``GET /api/needs-you-count`` returns ``filters`` (10 keys, fixed
order, every one always present) built from ALL of a job's blockers through ONE table (``filter_for``). Pinned:
* the pure table: kind + label word -> filter; the assistant's kinds (review_cv / fix_cv) land nowhere; an unknown
  owner kind lands in Other, never nowhere;
* a real job with a CAPTCHA, an unapproved CV and an open form sits in each of those filters and counts ONCE in All;
* the strong-review view and the posting-closed stat never move ``n``, the jobs or the badge;
* ``closed_by`` text written by a caller is not proof (a forged withdrawn is not in the stat);
* value presence through the real route (rule 21): the model must carry ``filters``, not drop it.
"""
from __future__ import annotations

import hashlib
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from src.core import settings
from src.repositories import pgsync
from src.services.applications import needs_you_count
from tests._cv_review import ATS_RAW, ATS_TAIL, ats_rows, blank_ad, owners_turn

KEYS = ["all", "signin_captcha", "check_send", "answer_once", "cv_weak", "finish_by_hand", "went_out",
        "strong_cvs", "posting_closed", "other"]
KINDS = {"strong_cvs": "view", "posting_closed": "stat"}
HOST = "careers.northwind.example"


def _b(kind: str, label: str, actor: str = "you", ref: Any = None) -> dict[str, Any]:
    return {"kind": kind, "actor": actor, "label": label, "since": "2026-11-13T00:00:00+00:00", "ref": ref}


def _sql(statement: str, params: tuple = ()) -> None:
    with pgsync.connect(str(settings.DB_PATH)) as conn:
        conn.execute(statement, params)
        conn.commit()


async def _bring(client: AsyncClient, n: int = 1, **extra: Any) -> int:
    ad = {"title": f"Engineer {n}", "company": f"Company{n}", "location": "London", "country": "GB", "remote": False,
          "apply_url": f"https://{HOST}/jobs/{n}", "description": "Build things.", **extra}
    resp = await client.post("/api/jobs/bring", json=ad)
    assert resp.status_code == 200, resp.text
    return int(resp.json()["application_id"])


async def _event(client: AsyncClient, app_id: int, event_type: str, payload: dict[str, Any]) -> None:
    resp = await client.post(f"/api/applications/{app_id}/events", json={"event_type": event_type, "payload": payload})
    assert resp.status_code == 201, resp.text


async def _cv(client: AsyncClient, app_id: int, text: str = "Ada Lovelace") -> int:
    resp = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
    assert resp.status_code == 201, resp.text
    return int(resp.json()["artifact_id"])


async def _count(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _by_key(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["key"]: f for f in body["filters"]}


def _above(app_id: int) -> None:
    _sql("UPDATE applications SET fit_score = 90, fit_recorded_at = '2026-11-13T00:00:00+00:00' WHERE id = ?", (app_id,))


@pytest.fixture(autouse=True)
def _line(monkeypatch):
    from src.services.applications import spine

    async def fifty(_user_id: str) -> int:
        return 50

    monkeypatch.setattr(spine, "user_score_line", fifty)


# ---- the pure table ----

def test_the_table_maps_kind_and_label_word_to_one_filter():
    f = needs_you_count.filter_for
    assert [x[0] for x in needs_you_count.FILTERS] == KEYS
    assert f(_b("site_step", "solve the CAPTCHA")) == "signin_captcha"
    assert f(_b("site_step", "sign in")) == "signin_captcha"
    assert f(_b("site_step", "create an account")) == "signin_captcha"
    assert f(_b("site_step", "attach your CV")) == "signin_captcha"
    assert f(_b("site_step", "enter the code")) == "signin_captcha"
    assert f(_b("site_step", "finish on the site")) == "finish_by_hand"
    assert f(_b("site_step", "mark it applied")) == "went_out"
    assert f(_b("confirm_sent", "employer confirmed - did it go out?")) == "went_out"
    assert f(_b("send_yes", "Send")) == "check_send"
    assert f(_b("check_send", "Check & send")) == "check_send"
    assert f(_b("answers", "OK 1 answer", ref={"ask_id": 1, "fill_event_id": 9})) == "check_send"
    assert f(_b("answers", "answer 1 question", ref={"ask_id": 1})) == "answer_once"
    assert f(_b("approve_cv", "approve the CV")) == "cv_weak"
    assert f(_b("apply_by_hand", "apply on the site")) == "finish_by_hand"
    assert f(_b("outreach_send", "send 1 note")) == "other"
    assert f(_b("posting_closed", "skip or keep")) == "other"


def test_the_assistants_kinds_land_nowhere_and_an_unknown_owner_kind_lands_in_other():
    f = needs_you_count.filter_for
    for kind in ("review_cv", "fix_cv", "assistant_step", "queued", "follow_up", "waiting_employer"):
        assert f(_b(kind, "x", actor="assistant")) is None
    assert f(_b("queued", "apply on the site", actor="your_pc")) is None
    assert f(_b("waiting_employer", "waiting 3 d", actor="employer")) is None
    assert f(_b("brand_new_kind", "something", actor="you")) == "other"


def test_one_job_with_four_needs_is_in_four_filters_and_in_all_once():
    need = [_b("site_step", "solve the CAPTCHA"), _b("approve_cv", "approve the CV"), _b("send_yes", "Send"),
            _b("outreach_send", "send 1 note")]
    got = needs_you_count.filters_of(need)
    assert got == {"signin_captcha", "cv_weak", "check_send", "other"}


# ---- through the real route ----

@pytest.mark.asyncio
async def test_an_empty_user_has_every_filter_at_zero_in_the_fixed_order(authenticated_async_context):
    async with authenticated_async_context() as client:
        body = await _count(client)
        assert [f["key"] for f in body["filters"]] == KEYS
        for f in body["filters"]:
            assert f["count"] == 0 and f["job_ids"] == [] and f["label"]
            assert f["kind"] == KINDS.get(f["key"], "todo")
        assert body["n"] == 0


@pytest.mark.asyncio
async def test_a_captcha_job_with_an_unapproved_cv_and_an_open_form_is_in_each_filter_and_counts_once(
    authenticated_async_context,
):
    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        app_id = await _bring(client, 1)
        _above(app_id)
        await _cv(client, app_id)
        await owners_turn(uid, app_id)  # no tries left: the CV waits on the owner
        body = {"application_id": app_id, "question": "Form questions", "kind": "question",
                "questions": [{"key": "referred", "question": "Were you referred?", "source": "guess", "answer": "No"}]}
        assert (await client.post("/api/asks", json=body)).status_code == 201
        await _event(client, app_id, "form_filled", {"form_url": "https://careers.example/apply/7", "fields_count": 3, "answers": []})
        await _event(client, app_id, "blocked", {"reason": "captcha", "step": "open_form"})
        got = await _count(client)
        by = _by_key(got)
        assert got["n"] == 1 and got["jobs"] == 1
        assert by["all"]["count"] == 1 and by["all"]["job_ids"] == [app_id]
        for key in ("signin_captcha", "cv_weak", "check_send"):
            assert by[key]["job_ids"] == [app_id] and by[key]["count"] == 1, key
        for key in ("answer_once", "finish_by_hand", "went_out", "strong_cvs", "posting_closed", "other"):
            assert by[key]["count"] == 0, key
        # The old first-blocker answers still say the same thing (the badge, the dashboard).
        assert got["by_kind"] == {"site_step": 1} and got["job_ids_by_kind"] == {"site_step": [app_id]}


@pytest.mark.asyncio
async def test_an_answer_with_no_form_is_answer_once_and_a_joblesss_ask_is_answer_once_but_n_counts_it_once(
    authenticated_async_context,
):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        body = {"application_id": app_id, "question": "Form questions", "kind": "question",
                "questions": [{"key": "equality.gender", "question": "What is your gender?", "source": "memory", "answer": "Male"}]}
        assert (await client.post("/api/asks", json=body)).status_code == 201
        before = await _count(client)
        assert _by_key(before)["answer_once"]["job_ids"] == [app_id]
        assert _by_key(before)["check_send"]["count"] == 0
        assert (await client.post("/api/asks", json={"question": "Notice period?"})).status_code == 201
        after = await _count(client)
        by = _by_key(after)
        assert after["n"] == before["n"] + 1 and by["all"]["count"] == after["n"]
        assert by["answer_once"]["count"] == 2 and by["other"]["count"] == 0
        assert by["all"]["job_ids"] == [app_id]


@pytest.mark.asyncio
async def test_a_note_and_a_job_without_a_job_need_land_in_other(authenticated_async_context):
    async with authenticated_async_context() as client:
        app_id = await _bring(client, 1)
        _above(app_id)
        resp = await client.post(f"/api/applications/{app_id}/contacts", json={"name": "Rita", "company": "Acme", "found_via": "event"})
        cid = int(resp.json()["contact"]["id"] if "contact" in resp.json() else resp.json()["id"])
        msg = await client.post(f"/api/contacts/{cid}/outreach", json={"entry": "message", "channel": "linkedin", "text": "Hi"})
        assert msg.status_code == 201
        _sql("UPDATE applications SET status = 'applied' WHERE id = ?", (app_id,))
        got = await _count(client)
        by = _by_key(got)
        assert by["other"]["job_ids"] == [app_id] and by["all"]["job_ids"] == [app_id]
        assert by["all"]["count"] == got["n"] == 1


# ---- the view and the stat ----

CV = ("Ada Lovelace\nada@example.com | +44 7700 900123 | linkedin.com/in/ada\nEXPERIENCE\n"
      "- Data engineer at Northwind since 2019.\n"
      "- Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n" + ATS_TAIL)
RAW = ("Ada Lovelace\nEXPERIENCE\nData engineer at Northwind since 2019.\n"
       "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n" + ATS_RAW)
L1 = {"n": 1, "verdict": "supported", "evidence": ["Data engineer at Northwind since 2019."]}
L2 = {"n": 2, "verdict": "supported", "evidence": ["Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."]}


@pytest.mark.asyncio
async def test_a_strong_review_is_a_view_and_never_moves_n(authenticated_async_context):
    from src.services.profile.models import AssistantSettings, CVData, UserProfile
    from src.services.profile.storage import save_assistant_settings, save_profile

    async with authenticated_async_context() as client:
        uid = authenticated_async_context.fixture_user_id
        save_profile(UserProfile(cv_data=CVData(raw_text=RAW, name="Ada Lovelace")), uid, source_action="cv_upload")
        save_assistant_settings(uid, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")
        edits = [{"path": "assistant_settings.submit_mode", "value": "auto_when_sure"},
                 {"path": "assistant_settings.cv_auto_approve", "value": True}]
        assert (await client.patch("/api/profile", json={"edits": edits})).status_code == 200
        app_id = await _bring(client, 1, queue=True)
        await blank_ad(app_id)
        cv_id = await _cv(client, app_id, CV)
        await _event(client, app_id, "blocked", {"reason": "captcha"})
        base = await _count(client)
        assert _by_key(base)["strong_cvs"]["count"] == 0
        sha = hashlib.sha256(CV.encode("utf-8")).hexdigest()
        await _event(client, app_id, "cv_reviewed", {"artifact_id": cv_id, "sha256": sha, "confidence": "strong",
                                                    "lines": [L1, L2, *ats_rows(3)], "reviewer": "fresh helper"})
        after = await _count(client)
        by = _by_key(after)
        assert by["strong_cvs"]["job_ids"] == [app_id] and by["strong_cvs"]["count"] == 1
        assert by["strong_cvs"]["kind"] == "view"
        assert by["cv_weak"]["count"] == 0
        for key in ("n", "jobs", "other", "by_actor", "job_ids"):
            assert after[key] == base[key], key
        assert by["all"]["count"] == after["n"]


async def _legacy_unproven_block(user_id: str, app_id: int) -> None:
    from src.api.routes import browser as browser_routes
    from src.services.applications import spine

    async with browser_routes._db() as db:  # noqa: SLF001
        await spine.append_event(
            db, user_id=user_id, application_id=app_id, event_type="blocked",
            payload={"reason": "posting_closed", "step": "open the ad", "page_host": HOST, "detail": "",
                     "assistant": "token:claude-code"},
            occurred_at=spine.parse_occurred_at(None), recorded_by="token:claude-code",
        )


async def _agent(web: AsyncClient) -> AsyncClient:
    from src.api.main import app

    resp = await web.post("/api/tokens", json={"name": "claude-code"})
    assert resp.status_code == 201, resp.text
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {resp.json()['token']}"})


@pytest.mark.asyncio
async def test_posting_closed_is_a_stat_proven_closes_and_open_blocks_count_but_forged_text_does_not(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as web:
        proven, unproven, forged, quiet = [await _bring(web, n) for n in range(1, 5)]
        async with await _agent(web) as agent:
            base = {"reason": "posting_closed", "step": "open the ad", "page_host": HOST}
            r = await agent.post(f"/api/applications/{proven}/events", json={"event_type": "blocked", "payload": {**base, "evidence": "http_404"}})
            assert r.status_code == 201 and r.json()["status"] == "withdrawn", r.text
            # No door takes a closed posting without proof now (#1600, 422); a block written before that still counts.
            r = await agent.post(f"/api/applications/{unproven}/events", json={"event_type": "blocked", "payload": base})
            assert r.status_code == 422, r.text
        await _legacy_unproven_block(fixture_user_id, unproven)
        # Caller-written text: a withdrawn that only SAYS it was closed by the posting check.
        await _event(web, forged, "withdrawn", {"closed_by": "posting_closed", "blocked_event_id": 999999})
        got = await _count(web)
        by = _by_key(got)
        assert by["posting_closed"]["kind"] == "stat"
        assert sorted(by["posting_closed"]["job_ids"]) == sorted([proven, unproven])
        assert by["posting_closed"]["count"] == 2
        assert forged not in by["posting_closed"]["job_ids"] and quiet not in by["posting_closed"]["job_ids"]
        # The stat moves nothing else: the proven job is not in All; the unproven one is still the owner's choice.
        assert proven not in by["all"]["job_ids"] and unproven in by["all"]["job_ids"]
        assert by["all"]["count"] == got["n"]


@pytest.mark.asyncio
async def test_site_signin_jobs_group_into_one_card_counted_once_in_n_and_signin_captcha(authenticated_async_context):
    """Merge of the fixed filters with diagnosis D: two jobs on one sign-in are no rows, ONE card, counted once."""
    async with authenticated_async_context() as web:
        one, two, cap = [await _bring(web, n) for n in range(1, 4)]
        for app_id, reason in ((one, "login_needed"), (two, "login_needed"), (cap, "captcha")):
            await _event(web, app_id, "blocked", {"reason": reason, "step": "open_form", "page_host": HOST})
        got = await _count(web)
        by = _by_key(got)
        grouped = {one, two}
        assert grouped.isdisjoint(got["job_ids"])
        for key, f in by.items():
            assert grouped.isdisjoint(f["job_ids"]), key
        assert len(got["site_signins"]) == 1
        card = got["site_signins"][0]
        assert card["host"] == HOST and sorted(card["app_ids"]) == sorted(grouped)
        assert cap in got["job_ids"]
        assert got["n"] == len(got["job_ids"]) + 1 + got["other"] + (1 if got["answer_once"] else 0)
        assert by["all"]["count"] == got["n"]
        # the card once, plus the CAPTCHA job (a site step too) = 2; never 3 (the two jobs) + 1
        assert by["signin_captcha"]["count"] == 2
        assert by["signin_captcha"]["job_ids"] == [cap]
