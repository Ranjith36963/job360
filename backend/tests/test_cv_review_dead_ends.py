"""Two dead ends of the scheduled CV review run (production 2026-11-18, 12:33 UTC).

1. App 1031: the ad names no requirements (save_fit's ``jd_requirements`` is ``[]``), so the reviewer rightly listed
   every jd term as ``must: false``; ``review_jd.judge`` still stored ``jd_no_must`` on every review and the
   application ran out of tries. With a saved fit whose requirements are empty, no must-term is no fault; with
   requirements saved, or no fit at all, ``jd_no_must`` stays (checker D2).
2. Apps 1048, 1049: the reviewer claimed weak because the summary line held more facts than its 3 quotes could
   show; Job360 stored weak with NO code and no ``where``, so the fix round had nothing to act on. Every weak store
   now carries at least one code: ``line_too_dense`` (the rows at the quote cap: split or shorten that line), else
   ``reviewer_weak`` (Job360 found nothing: re-review the same CV).
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from src.services.applications import cv_review, review_jd
from tests.test_cv_review import _event, _stored
from tests.test_cv_review_benchmark import AD, HONEST, HONEST_JD, _app, _item, _payload, _profile, _seed

NO_MUST = [dict(i, must=False) for i in HONEST_JD]


async def _with_fit(client: Any, app_id: int, reqs: list[str]) -> None:
    fit = await client.put(f"/api/applications/{app_id}/fit", json={"jd_requirements": reqs})
    assert fit.status_code == 200, fit.text


# --- 1. jd_no_must on an ad with no requirements ---------------------------------------------------------------


def test_judge_exempts_no_must_only_when_the_ad_names_no_requirement():
    lines = cv_review.content_lines(HONEST, _profile())
    where, stored = review_jd.judge(NO_MUST, AD, lines, HONEST, _profile(), 0, None, no_requirements=True)
    assert "jd_no_must" not in where and stored["no_must_exempt"] is True, (where, stored)
    where, stored = review_jd.judge(NO_MUST, AD, lines, HONEST, _profile(), 0, None)
    assert "jd_no_must" in where and "no_must_exempt" not in stored, (where, stored)


@pytest.mark.asyncio
async def test_a_saved_fit_with_no_requirements_never_stores_jd_no_must(
        authenticated_async_context, fixture_user_id, caplog):
    caplog.set_level(logging.INFO, logger="src.services.applications.cv_review")
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        await _with_fit(client, app_id, [])
        got = await _stored(client, app_id, _payload(cv_id, HONEST, NO_MUST))
    assert (got["confidence"], got["downgraded"]) == ("strong", []), got
    assert got["jd"]["no_must_exempt"] is True
    logged = [r.getMessage() for r in caplog.records if "jd_no_must_exempt" in r.getMessage()]
    assert logged and f"application={app_id}" in logged[0] and "reason=no_jd_requirements" in logged[0], logged


@pytest.mark.asyncio
@pytest.mark.parametrize("reqs", [["LangGraph", "MCP", "RAG"], None])  # None: no fit saved at all
async def test_jd_no_must_stays_when_the_ad_has_requirements_or_no_fit(
        authenticated_async_context, fixture_user_id, reqs):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        if reqs is not None:
            await _with_fit(client, app_id, reqs)
        got = await _stored(client, app_id, _payload(cv_id, HONEST, NO_MUST))
    assert (got["confidence"], got["downgraded"]) == ("weak", ["jd_no_must"]), got
    assert "no_must_exempt" not in got["jd"]


# --- 2. weak with no reason code ---------------------------------------------------------------------------------


SUMMARY_N = 2  # review line 2 of HONEST is the summary (test_cv_review_benchmark)


def _weak(cv_id: int, dense: tuple[int, ...] = ()) -> dict[str, Any]:
    payload = {**_payload(cv_id, HONEST, HONEST_JD), "confidence": "weak"}
    for row in payload["lines"]:
        if row["n"] in dense:  # the reviewer spent all 3 quotes on the line and still could not hold its facts
            row["evidence"] = row["evidence"][:1] * cv_review.EVIDENCE_MAX
    return payload


@pytest.mark.asyncio
async def test_a_codeless_weak_on_a_line_at_the_quote_cap_stores_line_too_dense(
        authenticated_async_context, fixture_user_id, caplog):
    caplog.set_level(logging.INFO, logger="src.services.applications.cv_review")
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        resp = await _event(client, app_id, "cv_reviewed", _weak(cv_id, (SUMMARY_N,)))
        assert resp.status_code == 201, resp.text
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        got = next(e for e in events if e["id"] == resp.json()["event_id"])["payload"]
        view = (await client.get(f"/api/applications/{app_id}", params={"view": "review"})).json()["review"]
    assert (got["confidence"], got["downgraded"], got["where"]) == (
        "weak", ["line_too_dense"], {"line_too_dense": [SUMMARY_N]}), got
    assert (view["last_downgraded"], view["last_where"]) == (["line_too_dense"], {"line_too_dense": [SUMMARY_N]})
    said = " ".join(resp.json()["warnings"] or [])
    assert "line_too_dense: lines 2" in said and "split" in said, said
    logged = [r.getMessage() for r in caplog.records if "weak_without_codes" in r.getMessage()]
    assert logged and f"application={app_id}" in logged[0] and "code=line_too_dense" in logged[0], logged


@pytest.mark.asyncio
async def test_a_codeless_weak_with_no_dense_line_stores_reviewer_weak(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        got = await _stored(client, app_id, _weak(cv_id))
    assert (got["confidence"], got["downgraded"], got["where"]) == ("weak", ["reviewer_weak"], {}), got


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["weak_clean", "weak_dense", "weak_with_fault", "strong_with_fault", "no_must"])
async def test_every_weak_store_carries_at_least_one_code(authenticated_async_context, fixture_user_id, case):
    """The invariant: a stored weak always names a code, so the next fix round has something to act on."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _app(client, HONEST)
        payload = {"weak_clean": _weak(cv_id), "weak_dense": _weak(cv_id, (SUMMARY_N, 6)),
                   "weak_with_fault": {**_weak(cv_id), "jd": [*HONEST_JD, _item("Rust", have=True)]},
                   "strong_with_fault": _payload(cv_id, HONEST, [*HONEST_JD, _item("Rust", have=True)]),
                   "no_must": _payload(cv_id, HONEST, NO_MUST)}[case]
        got = await _stored(client, app_id, payload)
    assert got["confidence"] == "weak", got
    assert got["downgraded"] and all(c in cv_review.DOWNGRADES for c in got["downgraded"]), got
    if case != "weak_dense":
        assert "line_too_dense" not in got["downgraded"], got  # only an otherwise codeless weak names density
