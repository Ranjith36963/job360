"""The ad's requirement terms are judged by their SKILL words (production 2026-11-12, owner: "Python coding and Python
language are the same capability").

B) Reviewers copied whole ad lines as the jd term ("Python – Strong knowledge"); the CV line had to say the whole
phrase, so a CV saying "Python" covered nothing (must-have coverage 0 on apps 811 882 449 455 459 460). The CV is
now matched by the term's core (cut at a dash, "(" or ":", closed filler words trimmed); the ad check stays literal.
D) A re-review of the same application dropped a must-have or flipped it to false to pass (apps 793, 150): every
earlier must-term must come back as a must, else weak ``jd_terms_dropped``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from src.services.applications import cv_review, review_jd
from src.services.profile.models import CVData, UserProfile
from tests.test_cv_review import _stored
from tests.test_cv_review_benchmark import HONEST, HONEST_JD, _app, _item, _payload, _seed

AD = "Python – Strong knowledge\nRAG and Vector Databases\nFastAPI / REST API Development"
LINES = ["Engineering: Python, FastAPI", "- Engineered a RAG pipeline with AWS Bedrock."]
PROFILE = UserProfile(cv_data=CVData(skills=["Python", "RAG", "FastAPI"]))


def _judge(jd: list[dict[str, Any]], ad: str = AD, lines: Any = None, earlier: Any = None) -> tuple[Any, Any]:
    lines = LINES if lines is None else lines
    more = {} if earlier is None else {"earlier": earlier}
    return review_jd.judge(jd, ad, lines, "\n".join(lines), PROFILE, 0, **more)


def test_whole_ad_lines_as_terms_are_covered_by_their_skill_words():
    jd = [_item("Python – Strong knowledge", evidence=["Python"], n=[1]),
          _item("RAG and Vector Databases", evidence=["RAG"], n=[2]),
          _item("FastAPI / REST API Development", evidence=["FastAPI"], n=[1])]
    where, stored = _judge(jd)
    assert (where, stored["must"], stored["covered"]) == ({}, 3, 3)
    assert stored["terms"] == [{"term": "Python", "must": True}, {"term": "RAG and Vector Databases", "must": True},
                               {"term": "FastAPI / REST API", "must": True}]


@pytest.mark.parametrize("term", ["Python coding", "Python language", "Strong Python programming skills"])
def test_python_coding_and_python_language_are_the_same_capability(term):
    where, stored = _judge([_item(term, evidence=["Python"], n=[1])], ad=f"Must have: {term}.")
    assert (where, stored["covered"], stored["terms"]) == ({}, 1, [{"term": "Python", "must": True}])


def test_java_still_never_matches_javascript():
    where, stored = _judge([_item("Java programming", evidence=["Python"], n=[1])], ad="Java programming",
                           lines=["Skills: JavaScript, Python"])
    assert (where["jd_term_not_in_cv"], stored["covered"]) == ([1], 0)


def test_a_gap_is_stored_and_checked_as_its_core():
    where, stored = _judge([_item("Python", evidence=["Python"], n=[1]), _item("Kubernetes – essential", have=False)],
                           ad="Python\nKubernetes – essential")
    assert (where, stored["gaps"]) == ({}, ["Kubernetes"])
    where, _ = _judge([_item("Python coding", have=False)], ad="Python coding")
    assert where["jd_gap_claimed"] == [1]  # the CV says Python: a gap it claims


@pytest.mark.parametrize("term", ["Design and deploy scalable data platforms", "Strong knowledge"])
def test_a_sentence_or_an_empty_core_is_a_shape_problem(term):
    problems = review_jd.shape_problems([_item("Python"), _item(term)], 10)
    assert problems == ["jd[1] is a sentence, not one requirement: give the skill's own words from the ad (1-4 words)"]


def test_python_coding_and_python_language_in_one_jd_are_one_requirement():
    problems = review_jd.shape_problems([_item("Python coding"), _item("Python language")], 10)
    assert problems == ["jd[1] is a duplicate of jd[0] (one item per requirement)"]


def test_keeping_a_must_term_with_have_false_is_not_dropped():
    where, _ = _judge([_item("Python coding", have=False), _item("RAG", evidence=["RAG"], n=[2])],
                      ad="Python coding, RAG", lines=["- Engineered a RAG pipeline."],
                      earlier=[{"term": "Python", "must": True}, {"term": "FastAPI", "must": False}])
    assert "jd_terms_dropped" not in where


async def _two_reviews(ctx: Any, user_id: str, second: list[dict[str, Any]], strip_terms: bool = False) -> Any:
    from src.api import dependencies as api_deps

    async with ctx() as client:
        _seed(user_id)
        app_id, cv_id = await _app(client, HONEST)
        first = await _stored(client, app_id, _payload(cv_id, HONEST, HONEST_JD))
        assert first["confidence"] == "strong"
        if strip_terms:  # a review stored before this rule: its jd block has no "terms"
            db = await api_deps.get_db()
            cur = await db._db.execute("SELECT id, payload FROM application_events WHERE application_id = ? AND "
                                       "event_type = 'cv_reviewed'", (app_id,))
            (event_id, raw), = await cur.fetchall()
            payload = raw if isinstance(raw, dict) else json.loads(raw)
            payload["jd"].pop("terms")
            await db._db.execute("UPDATE application_events SET payload = ? WHERE id = ?",
                                 (json.dumps(payload), event_id))
            await db._db.commit()
        return await _stored(client, app_id, _payload(cv_id, HONEST, second))


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [
    [x for x in HONEST_JD if x["term"] != "RAG"],  # app 793: must 5/covered 4 -> must 4/covered 4
    [dict(x, must=False) if x["term"] == "RAG" else x for x in HONEST_JD],  # app 150: a must flipped to false
])
async def test_a_re_review_that_drops_a_must_term_is_weak(authenticated_async_context, fixture_user_id, second):
    got = await _two_reviews(authenticated_async_context, fixture_user_id, second)
    assert (got["confidence"], got["downgraded"], got["where"]) == ("weak", ["jd_terms_dropped"],
                                                                     {"jd_terms_dropped": ["RAG"]})
    assert "jd_terms_dropped: RAG." in cv_review.weak_warning(got, got["where"])


@pytest.mark.asyncio
async def test_an_earlier_review_without_terms_is_not_compared(authenticated_async_context, fixture_user_id):
    second = [x for x in HONEST_JD if x["term"] != "RAG"]
    got = await _two_reviews(authenticated_async_context, fixture_user_id, second, strip_terms=True)
    assert (got["confidence"], got["downgraded"]) == ("strong", [])
