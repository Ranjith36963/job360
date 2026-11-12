"""S2 of the CV benchmark (owner 2026-11-10): wording is free and human; hard facts are exact.

Rows are ``{n, verdict, evidence}``; Job360 pins every hard fact of a line to that row's own quotes, from the record
the line sits under, and stores each failing code with its line numbers (``where``).
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from src.services.applications import cv_review, review_rows
from src.services.profile.models import CVData, UserProfile
from tests._cv_review import ATS_LINES, ats_cv
from tests.test_cv_review import _setup, _stored
from tests.test_cv_review_held_lines import B1, B2, B3, CV_DATA, SUMMARY, _seed, _sha

ORBITLY = "Orbitly (orbitly.uk) | Founder & AI-Native Engineer | United Kingdom | Feb 2026 - Present"
NORTHWIND = "Northwind Labs | AI Solutions Engineer – R&D Department | Stevenage, United Kingdom | Jun 2025 - Sep 2025"
TIDEWATER = "Tidewater | AI/ML Engineer Intern | United Kingdom (Remote) | Oct 2024 - Jan 2025"
# The owner's own bullets, written in other words: every number, tool and name kept exactly.
R1 = "Created the memory layer an AI agent relies on, keeping user profiles and their history."
R2 = "Lifted relevance by 40% by building a RAG pipeline on Bedrock and ChromaDB."
R3 = "Streamlined model training by writing the data preprocessing pipelines it runs on."


def _profile() -> UserProfile:
    return UserProfile(cv_data=CVData(**CV_DATA))


def _cv(*blocks: tuple[str, str]) -> str:
    return "Ada Lovelace\nEXPERIENCE\n" + "".join(f"{h}\n- {b}\n" for h, b in blocks)


def _rows(text: str, evidence: dict[str, list[str]], verdict: str = "supported") -> list[dict[str, Any]]:
    """One row per review line: a bullet's evidence from ``evidence`` (keyed by its text), else none (held)."""
    lines = cv_review.content_lines(text, _profile())
    return [{"n": n, "verdict": verdict, "evidence": evidence.get(ln.lstrip("- "), [])}
            for n, ln in enumerate(lines, 1)]


def _judge(text: str, evidence: dict[str, list[str]], profile: UserProfile | None = None) -> dict[str, list[Any]]:
    return review_rows.judge(text, _rows(text, evidence), profile or _profile())[0]


HONEST = _cv((ORBITLY, R1), (NORTHWIND, R2), (TIDEWATER, R3))
EVIDENCE = {R1: [B1], R2: [B2], R3: [B3]}
# Through the door (rules 6): the same CV, ATS-ready; the tail's lines quote themselves, after the CV's own 6 lines.
ATS_HONEST = ats_cv(HONEST)
ATS_EVIDENCE = {**EVIDENCE, **{ln: [ln] for ln in ATS_LINES}}
N = 6 + len(ATS_LINES)


@pytest.mark.asyncio
async def test_a_reworded_true_bullet_is_strong(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv=ATS_HONEST)
        got = await _stored(client, app_id, {"artifact_id": cv_id, "sha256": _sha(ATS_HONEST), "confidence": "strong",
                                             "lines": _rows(ATS_HONEST, ATS_EVIDENCE), "reviewer": "fresh helper"})
    assert (got["confidence"], got["downgraded"], got["where"], got["rules"]) == ("strong", [], {}, cv_review.RULES)
    assert (got["lines"], got["content_lines"], got["covered"]) == (N, N, N)


def test_sentence_start_capitals_are_no_claims():
    two = "Kept user profiles and history in the memory layer of an AI agent. Built it end to end."
    assert _judge(_cv((ORBITLY, two)), {two: [B1]}) == {}
    summary = "AI engineer building agentic systems. Works with LangGraph and MCP."
    text = f"Ada Lovelace\nSUMMARY\n{summary}\n"
    assert _judge(text, {summary: [SUMMARY]}) == {}


@pytest.mark.parametrize(("line", "code"), [
    ("Lifted relevance by 45% by building a RAG pipeline on Bedrock and ChromaDB.", "claim_not_in_quote"),  # number
    ("Lifted relevance by 40% with a RAG pipeline on Bedrock, ChromaDB and Kubernetes.", "claim_not_in_quote"),  # tool
    ("Lifted relevance by 40% at Google with a RAG pipeline on Bedrock and ChromaDB.", "claim_not_in_quote"),  # employer
    ("Lifted relevance by 40% as Lead Architect, building a RAG pipeline on Bedrock.", "claim_not_in_quote"),  # title
    ("Lifted relevance by 40% in 2023 with a RAG pipeline on Bedrock and ChromaDB.", "claim_not_in_quote"),  # date
    ("Lifted relevance by 40% for twelve teams with a RAG pipeline on Bedrock.", "claim_not_in_quote"),  # number word
    ("Lifted relevance by 40% with a RAG pipeline on Bedrock and kubernetes.", "claim_not_in_quote"),  # lowercase
    ("LangChain pipeline lifted relevance by 40% on Bedrock and ChromaDB.", "claim_not_in_quote"),  # a profile name
    ("Led the RAG pipeline work on Bedrock and ChromaDB, lifting relevance by 40%.", "claim_not_in_quote"),  # a role
    ("Lifted relevance by 40% with a RAG pipeline on Bedrock and ChromaDB, and led the migration.",
     "claim_not_in_quote"),  # a role claim mid-sentence
    ("Senior work on a RAG pipeline on Bedrock and ChromaDB lifted relevance by 40%.", "claim_not_in_quote"),
])
def test_an_invented_fact_in_a_reworded_line_is_weak(line, code):
    assert code in _judge(_cv((NORTHWIND, line)), {line: [B2]}), line


def test_a_supported_verdict_whose_quote_lacks_the_facts_is_weak():
    got = _judge(_cv((NORTHWIND, R2)), {R2: ["Engineered a RAG pipeline with Bedrock and ChromaDB"]})
    assert got == {"claim_not_in_quote": [2]}, got


def test_one_generic_quote_for_every_line_is_weak():
    lines = ["Shipped agent memory for job seekers.", "Owned search relevance end to end.",
             "Cut model training time across the team.", "Mentored engineers on agent design."]
    text = "Ada Lovelace\nSUMMARY\n" + "".join(f"{ln}\n" for ln in lines)
    got = _judge(text, {ln: [SUMMARY] for ln in lines})
    assert got["evidence_reused"] == [1, 2, 3, 4], got


def test_natural_openers_are_free_and_a_quoted_role_is_strong():
    lead = "Led a team of 4 building the memory layer for an AI agent, storing profiles and history."
    positions = [{**CV_DATA["cv_positions"][0], "bullets": [B1, lead]}, *CV_DATA["cv_positions"][1:]]
    profile = UserProfile(cv_data=CVData(**{**CV_DATA, "cv_positions": positions}))
    for line, quote in (("Hands-on engineer who builds the memory layer an AI agent keeps profiles and history in.", B1),
                        ("Across the product, kept profiles and history in an AI agent's memory layer.", B1),
                        ("Led a team of 4 on an AI agent's memory layer for profiles and history.", lead)):
        assert _judge(_cv((ORBITLY, line)), {line: [quote]}, profile) == {}, line
    # no shared words is no rule: a hard fact missing from an unrelated quote still is
    line = "Lifted relevance by 40% on Bedrock."
    assert _judge(_cv((NORTHWIND, line)), {line: [B3]}) == {"claim_not_in_quote": [2], "evidence_wrong_record": [2]}


def test_each_rule_names_its_line():
    text = _cv((ORBITLY, R1), (NORTHWIND, R2), (TIDEWATER, R3))
    rows = _rows(text, EVIDENCE)
    rows[1]["verdict"] = "unsupported"  # the reviewer's own no
    rows[3]["evidence"] = ["Lifted relevance by 40% with a RAG pipeline"]  # not in the profile
    rows[5]["evidence"] = []  # no evidence for a line that needs it
    del rows[4]  # a header with no row
    where = review_rows.judge(text, rows, _profile())[0]
    assert where == {"line_uncovered": [5], "line_unsupported": [2], "quote_not_in_profile": [4],
                     "evidence_wrong_record": [4], "claim_not_in_quote": [4], "no_evidence": [6]}, where
    span = f"{B1} {B2}"  # two fields joined
    assert _judge(text, {**EVIDENCE, R2: [span]})["quote_not_in_profile"] == [4]
    assert _judge(text, {**EVIDENCE, R2: ["RAG pipeline", B2]})["quote_too_thin"] == [4]


def test_shape_is_closed_and_small():
    good = {"artifact_id": 1, "sha256": "x", "confidence": "strong", "reviewer": "r",
            "lines": [{"n": 1, "verdict": "supported", "evidence": [B1]}]}
    assert cv_review.shape_problems(good) == []
    old = {**good, "lines": [{"cv_line": "x", "profile_quote": "y", "field_path": "z"}]}
    assert "reconnect" in " ".join(cv_review.shape_problems(old))
    for rows in ([{"n": 1, "verdict": "supported", "evidence": [B1] * 4}],
                 [{"n": 1, "verdict": "supported", "evidence": ["x" * 601]}],
                 [{"n": 1, "verdict": "supported", "evidence": ["ab"]}],
                 [{"n": 1, "verdict": "maybe", "evidence": []}],
                 [{"n": 0, "verdict": "supported", "evidence": []}],
                 [{"n": True, "verdict": "supported", "evidence": []}],
                 [{"n": 1, "verdict": "supported", "evidence": [], "cv_line": "x"}],
                 [{"n": 1, "verdict": "supported", "evidence": []}, {"n": 1, "verdict": "supported", "evidence": []}],
                 [{"n": i, "verdict": "supported", "evidence": []} for i in range(1, 202)]):
        assert cv_review.shape_problems({**good, "lines": rows}), rows


@pytest.mark.asyncio
async def test_bad_rows_are_refused_and_stored_nowhere(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv=ATS_HONEST)
        base = {"artifact_id": cv_id, "sha256": _sha(ATS_HONEST), "confidence": "strong", "reviewer": "fresh helper"}
        for rows in ([{"n": N + 1, "verdict": "supported", "evidence": []}],  # review.lines has N lines
                     [{"n": n, "verdict": "supported", "evidence": ["q" * 600] * 3} for n in range(1, 40)]):  # 64 KB+
            resp = await client.post(f"/api/applications/{app_id}/events",
                                     json={"event_type": "cv_reviewed", "payload": {**base, "lines": rows}})
            assert resp.status_code == 422, resp.text
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        assert not [e for e in events if e["event_type"] == "cv_reviewed"]


async def _post_rows(client: Any, app_id: int, cv_id: int, rows: list[dict[str, Any]]) -> Any:
    payload = {"artifact_id": cv_id, "sha256": _sha(ATS_HONEST), "confidence": "strong", "reviewer": "fresh helper",
               "lines": rows}
    return await client.post(f"/api/applications/{app_id}/events", json={"event_type": "cv_reviewed", "payload": payload})


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_n", ["duplicate", 0, N + 1])
async def test_duplicate_zero_and_beyond_last_n_are_refused_over_http(authenticated_async_context, fixture_user_id, bad_n):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv=ATS_HONEST)
        rows = _rows(ATS_HONEST, ATS_EVIDENCE)
        rows.append({**rows[1]} if bad_n == "duplicate" else {"n": bad_n, "verdict": "supported", "evidence": []})
        resp = await _post_rows(client, app_id, cv_id, rows)
        assert resp.status_code == 422, resp.text
        events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
        assert not [e for e in events if e["event_type"] == "cv_reviewed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [2, 1])
async def test_a_line_with_no_row_is_stored_weak_and_named(authenticated_async_context, fixture_user_id, missing):
    """Line 2 is a bullet (needs evidence); line 1 is a held header. TODAY a held line with no row is WEAK too:
    ``line_uncovered`` names it (only a line WITH a row is held without evidence)."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        app_id, cv_id = await _setup(client, cv=ATS_HONEST)
        rows = [r for r in _rows(ATS_HONEST, ATS_EVIDENCE) if r["n"] != missing]
        got = await _stored(client, app_id, {"artifact_id": cv_id, "sha256": _sha(ATS_HONEST), "confidence": "strong",
                                             "lines": rows, "reviewer": "fresh helper"})
    assert (got["confidence"], got["where"]) == ("weak", {"line_uncovered": [missing]})
    assert got["covered"] == N - 1


def test_judge_is_bounded_on_hostile_text():
    raw = ("Ada Lovelace\n" + ",-( " * 75 + "\n") * 300
    profile = UserProfile(cv_data=CVData(**{**CV_DATA, "raw_text": CV_DATA["raw_text"] + raw}))
    text = "Ada Lovelace\nEXPERIENCE\n" + "".join(f"- {',-( ' * 75} {i}\n" for i in range(200))
    rows = [{"n": n, "verdict": "supported", "evidence": [",-( " * 150, B1, "x y z " * 100]} for n in range(1, 201)]
    start = time.process_time()
    where = review_rows.judge(text, rows, profile)[0]
    assert time.process_time() - start < 2.0 and where


@pytest.mark.asyncio
async def test_many_reviews_go_in_one_record_events_call_under_the_same_check(
    authenticated_async_context, fixture_user_id,
):
    """A 60-CV run records its reviews in batches (no per-call limit applies to record_event; the 20-review run's 429
    came from outside Job360). Each item is verified exactly as record_event verifies it."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        moved = ats_cv(_cv((ORBITLY, R1), (TIDEWATER, R2)))
        (a1, c1), (a2, c2) = await _setup(client, cv=ATS_HONEST), await _setup(client, cv=moved)
        items = [{"application_id": a, "event_type": "cv_reviewed", "payload": {
            "artifact_id": c, "sha256": _sha(t), "confidence": "strong", "reviewer": "fresh helper",
            "lines": _rows(t, ATS_EVIDENCE)}} for a, c, t in ((a1, c1, ATS_HONEST), (a2, c2, moved))]
        resp = await client.post("/api/applications/record-events", json={"events": items})
        assert resp.status_code == 200, resp.text
        assert [r["ok"] for r in resp.json()["events"]] == [True, True]
        got = [(await _stored_payload(client, a)) for a in (a1, a2)]
    assert (got[0]["confidence"], got[1]["confidence"]) == ("strong", "weak")
    assert got[1]["where"] == {"evidence_wrong_record": [4]}


async def _stored_payload(client: Any, app_id: int) -> dict[str, Any]:
    events = (await client.get(f"/api/applications/{app_id}")).json()["events"]
    return next(e for e in events if e["event_type"] == "cv_reviewed")["payload"]


# ── Attack round on edaafb7eb (checker 2026-11-10): the code is the hard-fact backstop ─────────────────────────────


@pytest.mark.parametrize("line", [
    "Lifted relevance by 40% with a RAG pipeline on Bedrock and ChromaDB. Amazon adopted it.",  # B1 2nd sentence
    "Lifted relevance by 40% with a RAG pipeline on Bedrock and ChromaDB: Amazon adopted it.",
    "Lifted relevance by 40% with a RAG pipeline on Bedrock and ChromaDB. Kubernetes is its home.",  # a name ending -s
    "Lifted relevance by 40% as a staff engineer on a RAG pipeline on Bedrock and ChromaDB.",  # B2
    "Oversaw a RAG pipeline on Bedrock and ChromaDB, lifting relevance by 40%.",
    "Lifted relevance by 40% as the person responsible for a RAG pipeline on Bedrock and ChromaDB.",
    "Lifted relevance by 40% as team-lead on a RAG pipeline on Bedrock and ChromaDB.",
    "Lifted relevance by 40% in the manager's seat on a RAG pipeline on Bedrock and ChromaDB.",
    "Lifted relevance by 40% in charge of a RAG pipeline on Bedrock and ChromaDB.",
    "Le​d a RAG pipeline on Bedrock and ChromaDB, lifting relevance by 40%.",  # B3 zero-width space
    "Sеnior work on a RAG pipeline on Bedrock and ChromaDB lifted relevance by 40%.",  # Cyrillic e
    "Lifted relevance by 4​1% with a RAG pipeline on Bedrock and ChromaDB.",
    "Lifted relevance by 40% for a three-person team with a RAG pipeline on Bedrock and ChromaDB.",  # B6
    "Lifted relevance by 40% with zero downtime on a RAG pipeline on Bedrock and ChromaDB.",
    "Lifted relevance by 40% in a fortnight with a RAG pipeline on Bedrock and ChromaDB.",
    "Lifted relevance by 40% since march with a RAG pipeline on Bedrock and ChromaDB.",
    "Lifted relevance by 40% twelve-fold with a RAG pipeline on Bedrock and ChromaDB.",
    "Lifted relevance by 40% over a decade with a RAG pipeline on Bedrock and ChromaDB.",
])
def test_attack_round_facts_are_weak(line):
    assert "claim_not_in_quote" in _judge(_cv((NORTHWIND, line)), {line: [B2]}), line


def test_a_summary_line_naming_a_job_takes_its_facts_from_that_job():  # B4
    line = "Lifted relevance by 40% with a RAG pipeline on Bedrock and ChromaDB at Tidewater."
    text = f"Ada Lovelace\nSUMMARY\n{line}\n"
    assert _judge(text, {line: [B2, "Tidewater | AI/ML Engineer Intern Oct 2024 – Jan 2025 | Remote"]}) == {
        "evidence_wrong_record": [1]}
    assert _judge(text.replace(" at Tidewater", ""), {line.replace(" at Tidewater", ""): [B2]}) == {}


def test_a_skills_list_under_a_job_is_held_only_by_that_jobs_own_text():  # B5
    text = _cv((TIDEWATER, B3), (NORTHWIND, "Agents & LLMs: LangGraph, LangChain, MCP, RAG"))
    assert _judge(text, {B3: [B3]}) == {"no_evidence": [4]}
    held = _cv((TIDEWATER, B3), ("Support Agent | LangGraph, MCP, Gmail, Slack", "LangGraph, MCP"))
    assert _judge(held, {B3: [B3]}) == {}


def test_symbols_and_fullwidth_digits_are_no_false_weak():
    for line in ("Lifted relevance by 40% with a RAG pipeline on Bedrock + ChromaDB.",
                 "Lifted relevance by ４０% with a RAG pipeline on Bedrock and ChromaDB.",
                 "Lifted relevance by 40% with a RAG pipeline on Bedrock and ChromaDB. Built it end to end.",
                 "Lifted relevance by 40% with a RAG pipeline on Bedrock and ChromaDB. Hands-on throughout."):
        assert _judge(_cv((NORTHWIND, line)), {line: [B2]}) == {}, line


# ── PR #1554 review [P2]: view="review" `held` and judge() decide "held" with ONE helper ───────────────────────────
UNDER_JOB = _cv((TIDEWATER, B3), (NORTHWIND, "Agents & LLMs: LangGraph, LangChain, MCP, RAG"))  # Northwind lacks MCP
UNDER_PROJECT = _cv((TIDEWATER, B3), ("Support Agent | LangGraph, MCP, Gmail, Slack", "LangGraph, MCP"))


@pytest.mark.asyncio
async def test_review_view_held_leaves_out_a_list_its_job_does_not_name(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        held = {}
        for name, text in (("job", UNDER_JOB), ("project", UNDER_PROJECT)):
            app_id, _ = await _setup(client, cv=text)
            held[name] = (await client.get(f"/api/applications/{app_id}", params={"view": "review"})).json()[
                "review"]["held"]
    assert 4 not in held["job"] and 4 in held["project"], held


def test_every_held_line_verifies_with_no_evidence():
    from tests.test_cv_review_held_lines import OWNER_SHAPES

    for text in (HONEST, UNDER_JOB, UNDER_PROJECT, *OWNER_SHAPES.values()):
        held = review_rows.held_numbers(text, _profile())
        lines = cv_review.content_lines(text, _profile())
        rows = [{"n": n, "verdict": "supported", "evidence": [] if n in held else [ln.lstrip("- ")]}
                for n, ln in enumerate(lines, 1)]
        where = review_rows.judge(text, rows, _profile())[0]
        assert not set(where.get("no_evidence", [])) & set(held), (text[:60], held, where)
