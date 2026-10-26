"""Owner 2026-10-26 - a form answer is the text typed into the field, never a note about it.

On production (AppZen) the written answers read "drafted", "drafted from profile facts" and
"Calnex RAG + multi-agent assistant, metrics from CV": the run recorded a summary, so the user could
not read or approve what went out. form_filled / form_swept now refuse a written or guessed answer
that describes the text instead of giving it.
"""
from __future__ import annotations

import pytest

from src.services.applications import kit
from src.services.applications.spine import SpineError


def _one(answer: str, source: str = "written") -> dict[str, str]:
    return {"question": "Why AppZen?", "answer": answer, "source": source}


@pytest.mark.parametrize("answer", [
    "drafted", "drafted from profile facts", "Calnex RAG + multi-agent assistant, metrics from CV",
    "see CV", "Drafted from profile facts (see typed text)", "As per my CV",
])
def test_a_note_about_the_text_is_refused(answer):
    with pytest.raises(SpineError) as err:
        kit.clean_answers([_one(answer)])
    assert err.value.status_code == 422 and "word for word" in str(err.value.detail)


def test_the_real_text_and_short_facts_are_kept():
    essay = ("At Calnex I built a Bedrock RAG pipeline and a multi-agent workflow; I want to do the same "
             "for AppZen's finance teams, where an agent that reads invoices saves hours a week.")
    assert kit.clean_answers([_one(essay)])[0]["answer"] == essay
    assert kit.clean_answers([_one("Yes", "guessed"), _one("No", "memory")])[1]["answer"] == "No"
    long = "My CV lists the projects; here is the one that matters most. " * 4  # a paragraph may name its CV
    assert kit.clean_answers([_one(long.strip())])[0]["answer"] == long.strip()
