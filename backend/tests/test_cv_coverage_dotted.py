"""An ad's spelling and the CV's differ only by dots, hyphens, spaces or case: still the same term (app 479, 2026-11-15).

The ad said "NextJs", the CV said "Next.js": ``Tokens.match`` kept "next.js" and "nextjs" apart, so a must-have the CV
names counted as missing (``jd_term_not_in_cv``). ``claims`` already read both as one spelling, ``match`` did not. A fold
happens only inside one word-run (the pieces ``Next`` + ``JS`` of one phrase), never across ``/ | : •`` ("CI/CD"), and
never for ``.NET`` / ``C#`` / 1-2 letter terms.
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import review_jd
from src.services.applications.cv_coverage import Tokens
from src.services.profile.models import CVData, UserProfile
from tests.test_cv_review_benchmark import _item

SPELLINGS = ["Next.js", "NextJs", "NEXTJS", "Next JS", "next-js", "Next.JS"]


@pytest.mark.parametrize("term", SPELLINGS)
@pytest.mark.parametrize("text", SPELLINGS)
def test_next_js_in_any_spelling_matches_any_other(term, text):
    assert Tokens(f"Frameworks: React, {text}, FastAPI").match(term), (term, text)
    assert Tokens(f"Frameworks: React, {text}, FastAPI").claims(term)


@pytest.mark.parametrize(("term", "text"), [
    ("Node.js", "Backend in NodeJS and Python"), ("NodeJS", "Backend in Node.js and Python"),
    ("Node JS", "Backend in Node.js"), ("Vue.js", "Built with VueJS"), ("D3.js", "charts in D3 JS"),
])
def test_other_dotted_names_fold_the_same_way(term, text):
    assert Tokens(text).match(term) != ""


def test_the_match_returns_the_texts_own_spelling():
    assert Tokens("Frameworks: React, Next.js, FastAPI").match("NextJs") == "Next.js"
    assert Tokens("Frameworks: Next JS").match("Next.js") == "Next JS"


@pytest.mark.parametrize(("term", "text"), [
    ("CICD", "We use CI/CD pipelines"),  # a slash is a real word boundary: match stays as it was (claims is looser)
    ("CICD", "ran CI | CD jobs"),
    (".NET", "uses NET tools"), ("C#", "wrote C code"), ("Go", "g.o and G O"),
    ("Java", "JavaScript"), ("Next", "Next.js"), ("Next JS", "Next to JS"),
    ("NextJs", "Next, JS"), ("NextJs", "(Next) JS"), ("NextJs", "Next. JS"),
])
def test_no_fold_across_real_word_boundaries_or_into_other_words(term, text):
    assert Tokens(text).match(term) == "", (term, text)


def test_a_fold_never_reads_a_longer_word_as_the_term():
    assert Tokens("JavaScript developer").match("Java") == ""
    assert Tokens("Nextjsx and Reactive").match("NextJs") == ""


def test_the_review_counts_a_dotted_spelling_as_named():
    profile = UserProfile(cv_data=CVData(skills=["Python", "Next.js"]))
    lines: list[Any] = ["Frameworks: React, Next.js, FastAPI"]
    jd = [_item("NextJs", evidence=["Next.js"], n=[1])]
    where, stored = review_jd.judge(jd, "Must have: NextJs", lines, "\n".join(lines), profile, 0)
    assert (where, stored["must"], stored["covered"]) == ({}, 1, 1)
