"""The Needs-you badge must not redo the profile's work per open question row.

2026-11-17, production (``Server-Timing`` on ``GET /api/needs-you-count``, large owner account): the whole
request took 1145-1283 ms (it was ~850-1000 ms on 2026-11-13). ``b_derive`` was 323-373 ms
(``blockers._saved_fn`` ran ``ready._approved_record`` with no memo, one ``kit.compute_answers`` per
country, and ``ask_once.memory_question`` normalised the whole question catalog for EVERY row), ``b_flags``
99-114 ms and ``answer_once`` 281-292 ms (``compute_answers`` again, once per queued country).

Timing is not asserted (it flakes): the CALL COUNT is, and every answer must equal the un-memoised reading.
The memo lives for ONE request (``derive_memo.scope``), never across requests. Helpers are defined here.
"""
from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from httpx import AsyncClient

from src.services.applications import kit
from src.services.profile import answer_match, common_questions, country_pages
from src.services.profile.models import UserInfo, UserProfile

FORM = "https://careers.example/apply/7"
COUNTRIES = ("GB", "US", "DE")
SPONSOR_Q = "Will you now or in the future require visa sponsorship?"
# The question rows every application's ask carries: Memory-covered (GB / US have a right-to-work page, DE has
# none), a saved approved answer, a conflicting saved topic, and one nobody saved.
ROWS = [
    {"key": "sponsor", "question": SPONSOR_Q, "source": "guess", "answer": "No"},
    {"key": "notice_period", "question": "What is your notice period?", "source": "guess", "answer": "1 month"},
    {"key": "travel", "question": "Willing to travel?", "source": "guess", "answer": "Yes"},
    {"key": "salary_expectation", "question": "Expected salary?", "source": "guess", "answer": "50000"},
    {"key": "colour", "question": "Favourite colour?", "source": "guess", "answer": "Green"},
]
TYPED = [
    {"question": "What is your notice period?", "answer": "Two weeks", "source": "approved", "key": "notice_period"},
    {"question": "Willing to travel?", "answer": "Yes", "source": "memory", "key": "travel"},
    {"question": "Expected salary?", "answer": "50000", "source": "guessed", "key": "salary_expectation"},
    {"question": SPONSOR_Q, "answer": "No", "source": "memory", "key": "right_to_work.GB.needs_sponsorship"},
    {"question": "Do you have a criminal record?", "answer": "", "source": "guessed", "key": "criminal"},
]


def _saved(question: str, answer: str, topic: str, **extra: Any) -> dict[str, Any]:
    return {"question": question, "answer": answer, "approved": True, "topic": topic, **extra}


def _store(user_id: str, approve_travel: bool = True) -> None:
    """The user's saved answers, right-to-work pages and contact, written the way the web writes them."""
    from src.services.profile import edits as profile_edits
    from src.services.profile import user_info
    from src.services.profile.models import CVData
    from src.services.profile.storage import load_profile, save_profile

    if load_profile(user_id, with_overlay=False) is None:  # the base profile row the overlay edits sit on
        save_profile(UserProfile(cv_data=CVData(raw_text="Python engineer.", name="Ada")), user_id,
                     source_action="cv_upload")
    answers = [
        _saved("What is your notice period?", "One month", "notice_period"),
        _saved("Willing to travel?", "Yes", "travel", approved=approve_travel),
        _saved("Expected salary?", "50000", "salary_expectation"),
        _saved("What salary do you expect?", "60000", "salary_expectation"),  # two answers that disagree: a conflict
    ]
    rtw = {"countries": [{"country": "GB", "work_authorization": "citizen", "needs_sponsorship": False},
                         {"country": "US", "work_authorization": "needs_sponsorship", "needs_sponsorship": True}]}
    profile_edits.record_edits(
        user_id, profile_edits.WEB_ACTOR, [(user_info.USER_INFO_ANSWERS_PATH, answers), ("user_info.right_to_work", rtw),
                          ("user_info.contact", {"email": "a@example.com"})],
        enforce_rate_limit=False, source=profile_edits.SOURCE_USER_SAID,
    )


def _profile() -> UserProfile:
    """An in-memory profile for the scope test (no database)."""
    return UserProfile(user_info=UserInfo(contact={"email": "a@example.com"}, answers=[_saved("Q?", "A", "t")]))


async def _seed(
    client: AsyncClient, start: int, n: int, rows: list[dict[str, Any]] = ROWS, typed: list[dict[str, Any]] = TYPED,
) -> None:
    for i in range(start, start + n):
        resp = await client.post("/api/jobs/bring", json={
            "title": f"Engineer {i}", "company": f"Company{i % 4}", "location": "London", "queue": True,
            "country": COUNTRIES[i % len(COUNTRIES)], "apply_url": f"https://co{i}.example/careers/{i}",
            "description": "Build things.",
        })
        assert resp.status_code == 200, resp.text
        app_id = int(resp.json()["application_id"])
        ask = await client.post("/api/asks", json={
            "application_id": app_id, "question": "Form questions", "kind": "question", "questions": rows})
        assert ask.status_code == 201, ask.text
        fill = await client.post(f"/api/applications/{app_id}/events", json={
            "event_type": "form_filled",
            "payload": {"form_url": FORM, "fields_count": len(typed), "answers": [dict(a) for a in typed]}})
        assert fill.status_code == 201, fill.text


async def _body(client: AsyncClient) -> dict[str, Any]:
    resp = await client.get("/api/needs-you-count")
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def _spy(monkeypatch: pytest.MonkeyPatch, module: Any, name: str, only: Any = None) -> list[int]:
    """Count the calls (those ``only`` accepts, when given) of ``module.name``; the real function still runs."""
    calls: list[int] = []
    real = getattr(module, name)

    def counting(*args: Any, **kwargs: Any) -> Any:
        if only is None or only(*args, **kwargs):
            calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, counting)
    return calls


def _spies(monkeypatch: pytest.MonkeyPatch) -> tuple[list[int], list[int]]:
    """(kit runs, catalog normalisations). ``answers_by_user`` is called once at the top of every real
    ``kit.compute_answers`` run and nowhere else; a catalog question's text normalised is the catalog read."""
    texts = {str(q["question"]) for q in common_questions.catalog()}
    kits = _spy(monkeypatch, country_pages, "answers_by_user")
    norms = _spy(monkeypatch, answer_match, "normalize_question", only=lambda text, *a, **k: str(text) in texts)
    return kits, norms


@pytest.mark.asyncio
async def test_the_kit_and_the_catalog_are_read_once_however_many_rows_wait(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uid = authenticated_async_context.fixture_user_id
    _store(uid)
    kits, norms = _spies(monkeypatch)
    async with authenticated_async_context() as client:
        await _seed(client, 0, 3)
        small = await _body(client)
        small_kits, small_norms = len(kits), len(norms)
        kits.clear()
        norms.clear()
        await _seed(client, 3, 9)  # 12 applications, 60 open question rows
        large = await _body(client)
    assert small["answer_once"] >= 1 and large["by_kind"], (small, large)  # a real reading, not an empty one
    # GB, US, DE and the no-country kit: one run each, never one per row, fill or country-fill.
    assert small_kits <= 4, f"compute_answers ran {small_kits} times for 3 applications"
    assert len(kits) <= 4, f"compute_answers ran {len(kits)} times for 12 applications"
    # The same five questions on every form: the catalog is read for each distinct one, not once per row.
    assert len(norms) == small_norms, f"catalog normalised {small_norms} times for 3 apps, {len(norms)} for 12"


@pytest.mark.asyncio
async def test_the_whole_body_equals_the_unmemoised_reading(
    authenticated_async_context: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.services.applications import derive_memo

    uid = authenticated_async_context.fixture_user_id
    _store(uid)
    async with authenticated_async_context() as client:
        await _seed(client, 0, 6)
        memoised = await _body(client)
        monkeypatch.setattr(derive_memo, "ENABLED", False)  # the reading every request had before the memo
        plain = await _body(client)
    assert json.dumps(memoised, sort_keys=True) == json.dumps(plain, sort_keys=True)
    # value-presence (rule #21): the seed really reached the answer
    assert memoised["by_kind"].get("answers", 0) + memoised["by_kind"].get("check_send", 0) > 0, memoised["by_kind"]
    assert memoised["answer_once"] >= 1, memoised["answer_once"]


@pytest.mark.asyncio
async def test_blockers_equal_the_unmemoised_reading_and_the_cache_is_never_mutated(
    authenticated_async_context: Any,
) -> None:
    from src.api.routes import browser as browser_routes
    from src.services.applications import blockers, derive_memo

    uid = authenticated_async_context.fixture_user_id
    _store(uid)
    async with authenticated_async_context() as client:
        await _seed(client, 0, 6)
    async with browser_routes._db() as db:  # noqa: SLF001
        cur = await db._db.execute("SELECT id FROM applications WHERE user_id = ? ORDER BY id", (uid,))  # noqa: SLF001
        ids = [int(r[0]) for r in await cur.fetchall()]
        plain = await blockers.blockers_for(db, uid, ids)
        with derive_memo.scope() as memo:
            first = await blockers.blockers_for(db, uid, ids)
            kept = {k: copy.deepcopy(v[2]) for k, v in memo.answers.items()}
            again = await blockers.blockers_for(db, uid, ids)  # a second read in the scope (its own profile read)
            assert {k: memo.answers[k][2] for k in kept} == kept, "a cached kit result was mutated by a reader"
            assert memo.answers, "the scope cached nothing: the memo never ran"
    assert first == plain and again == plain
    assert any(b["kind"] in ("answers", "check_send") for bl in plain.values() for b in bl)


@pytest.mark.asyncio
async def test_a_second_request_sees_a_profile_changed_between_requests(authenticated_async_context: Any) -> None:
    from src.api.routes import browser as browser_routes
    from src.services.applications import blockers, derive_memo

    uid = authenticated_async_context.fixture_user_id
    _store(uid, approve_travel=False)
    async with authenticated_async_context() as client:
        await _seed(client, 0, 3, ROWS[2:3], [])  # one row: the travel answer, saved by the user
        before_body = await _body(client)
    async with browser_routes._db() as db:  # noqa: SLF001
        cur = await db._db.execute("SELECT id FROM applications WHERE user_id = ? ORDER BY id", (uid,))  # noqa: SLF001
        ids = [int(r[0]) for r in await cur.fetchall()]
        with derive_memo.scope():
            before = await blockers.blockers_for(db, uid, ids)
        _store(uid, approve_travel=True)  # the travel answer approved
        with derive_memo.scope():
            after = await blockers.blockers_for(db, uid, ids)
        _store(uid, approve_travel=False)
        with derive_memo.scope():
            back = await blockers.blockers_for(db, uid, ids)
    assert before != after, "approving a saved answer changed nothing: the second request read a stale cache"
    assert back == before
    async with authenticated_async_context() as client:  # the whole request path, a new request each time
        assert await _body(client) == before_body


def test_outside_a_scope_nothing_is_cached() -> None:
    from src.services.applications import derive_memo

    assert derive_memo.MEMO.get() is None
    profile, overlay = _profile(), []
    a = kit.compute_answers(profile, overlay, "GB")
    b = kit.compute_answers(profile, overlay, "GB")
    assert a == b and a is not b  # no scope = computed afresh each time, exactly as before
    with derive_memo.scope() as memo:
        c = kit.compute_answers(profile, overlay, "GB")
        assert kit.compute_answers(profile, overlay, "GB") is c
        assert kit.compute_answers(_profile(), overlay, "GB") is not c  # another profile object: its own entry
        assert len(memo.answers) == 2
    assert derive_memo.MEMO.get() is None
