"""Production CVs, 2026-11-15 (apps 455, 460, 461): skills lines whose items were ALL in the profile were stored weak
because the LABEL ("Infra", "CI", "Systems") was no skills-group word; the reviewer saw only quote_too_thin /
claim_not_in_quote and could not tell the label was the problem. Common group words are now generic, and a line
held back only by its label gets ``skills_label_unknown`` naming the word."""
from __future__ import annotations

from src.services.applications import cv_review, review_rows
from src.services.profile.models import CVData, UserProfile

RAW = (
    "Ada Lovelace\nEXPERIENCE\nBuilt RAG agents in Python with LangGraph at Calnex.\n"
    "SKILLS\nLanguages: Python, SQL\n"
)
CV_DATA = {
    "name": "Ada Lovelace", "raw_text": RAW,
    "skills": ["Python", "SQL", "Docker", "Linux", "Railway", "S3", "GitHub Actions", "PostgreSQL"],
    "cv_positions": [{"company": "Calnex Solutions", "title": "AI Solutions Engineer", "dates": "Jun 2025 – Sep 2025",
                      "bullets": ["Built RAG agents in Python with LangGraph at Calnex."]}],
}


def _profile() -> UserProfile:
    return UserProfile(cv_data=CVData(**CV_DATA))


def test_common_group_label_words_hold_a_line_of_profile_skills():
    book = cv_review.skill_book(_profile())
    for line in ("Infra: S3, Docker, Railway", "CI: GitHub Actions, Docker", "Systems: Linux, Docker, PostgreSQL",
                 "Tooling: Docker, Linux", "Testing: Python, SQL", "Observability: Docker, Linux",
                 "Security: Linux, Docker", "Mobile: Python, SQL", "DevTools: Docker, Linux",
                 "Monitoring: Docker, Linux", "CD: Railway, Docker"):
        assert cv_review.list_line_holds(line, book), line


def test_an_item_outside_the_book_or_a_claim_word_still_fails():
    book = cv_review.skill_book(_profile())
    for line in ("Infra: Docker, Kubernetes",  # an item the profile lacks
                 "Calnex: Docker, Linux",  # an employer word is no label
                 "Practice: Docker, Linux", "Evaluation: Docker, Linux", "Experience: Docker, Linux"):
        assert not cv_review.list_line_holds(line, book), line


def _judge(skills_line: str) -> dict[str, list]:
    text = f"Ada Lovelace\nEXPERIENCE\n- Built RAG agents in Python with LangGraph at Calnex.\nSKILLS\n{skills_line}\n"
    content = cv_review.content_lines(text, _profile())
    n = next(i for i, ln in enumerate(content, 1) if skills_line in ln)
    rows = [{"n": i, "verdict": "supported", "evidence": []} for i in range(1, len(content) + 1)]
    return review_rows.judge(text, rows, _profile())[0] | {"_n": [n]}


def test_a_line_held_back_only_by_its_label_is_named():
    where = _judge("Infra-ish: Docker, Linux")
    n = where["_n"][0]
    assert "skills_label_unknown" in cv_review.DOWNGRADES
    assert where["skills_label_unknown"] == [f"{n}:infra-ish"]
    assert n in where["no_evidence"]  # the line is not held


def test_no_label_code_for_an_item_outside_the_book_or_a_known_label():
    assert "skills_label_unknown" not in _judge("Infra-ish: Docker, Kubernetes")
    assert "skills_label_unknown" not in _judge("Infra: Docker, Linux")


def test_the_fix_round_text_names_the_label_word():
    stored = {"claimed": "strong", "confidence": "weak", "downgraded": ["skills_label_unknown"]}
    out = cv_review.weak_warning(stored, {"skills_label_unknown": ["31:infra-ish"]})
    assert out is not None
    assert "skills line 31: label word 'infra-ish' is no skills-group word; use one of: Skills, Technologies" in out
