"""Save-time CV checks (services/applications/cv_checks.py): each check fires on a
known-bad input and stays silent on a clean one. Pure + offline; the PDF check uses the
repo's real renderer on a tiny CV."""

from __future__ import annotations

import pytest

from src.services.applications import cv_checks as cc
from src.services.applications.kit import legal_file_stem
from src.services.profile.models import CVData, UserInfo, UserPreferences, UserProfile
from src.services.tailoring.cv_render import render_cv_pdf

SOURCE_CV = (
    "Ranjith Example\nAI Engineer\nJob360 - Founder, Jan 2025 - Present\n"
    "Built a RAG pipeline with ChromaDB, improving relevance by 40%.\n"
    "Calnex Solutions, Stevenage. 95% response accuracy. Led a team of 12.\n"
    "Skills: Python, FastAPI, Docker, PostgreSQL"
)


def _profile() -> UserProfile:
    cv = CVData(
        raw_text=SOURCE_CV,
        skills=["Python", "FastAPI", "Docker", "PostgreSQL", "ChromaDB"],
        name="Ranjith Example",
        cv_positions=[
            {"company": "Job360", "title": "Founder", "dates": "Jan 2025 - Present", "bullets": []},
            {"company": "Calnex Solutions", "title": "Engineer", "dates": "2024 - 2025", "bullets": []},
        ],
        github_languages={"Go": 3},
    )
    return UserProfile(cv_data=cv, preferences=UserPreferences())


CLEAN = (
    "Ranjith Example\nAI Engineer\n"
    "EXPERIENCE\nFounder | Job360 | Jan 2025 - Present\n- Built a RAG pipeline, improving relevance by 40%.\n"
    "Engineer | Calnex Solutions | 2024 - 2025\n- Led a team of 12 to 95% response accuracy.\n"
    "SKILLS\nLanguages: Python, Go, Docker, PostgreSQL\nContact: a@b.com, 07700 900123, +44 7442 564327\n"
)


def _codes(kind: str, text: str) -> list[str]:
    return [c["code"] for c in cc.check_text(kind, text, _profile())]


def test_clean_cv_is_silent():
    assert _codes("cv", CLEAN) == []


def test_unsupported_number_flags_37_percent_but_not_dates_phones_versions_years():
    text = CLEAN + "- Cut cost by 37% using Python 3.11 in 2024, v2.1, www.x.com/a/1234.\n"
    checks = cc.check_text("cv", text, _profile())
    detail = next(c["detail"] for c in checks if c["code"] == "unsupported_numbers")
    assert "37%" in detail
    rest = detail.replace("37%", "")
    for ok in ("40%", "95%", "2024", "3.11", "2.1", "7700", "1234"):
        assert ok not in rest


def _num_detail(text: str, source_extra: str = "") -> str:
    prof = _profile()
    prof.cv_data.raw_text += " Managed 2 teams over 2 years. " + source_extra
    checks = cc.check_text("cv", CLEAN + text, prof)
    return next((c["detail"] for c in checks if c["code"] == "unsupported_numbers"), "")


def test_data_size_units_are_claims_even_when_bare_number_is_in_profile():
    # "2" appears in the profile ("2 teams"), but "2TB" does not.
    assert "2TB" in _num_detail("- Processed 2TB of audio data.\n")
    assert "2 TB" in _num_detail("- Processed 2 TB of audio data.\n")
    assert "5PB" in _num_detail("- Indexed 5PB of logs.\n")
    assert _num_detail("- Processed 2TB of audio data.\n", "Handled 2TB of audio.") == ""
    assert _num_detail("- Processed 2 TB of audio data.\n", "Handled 2TB of audio.") == ""


def test_unsupported_terms_flags_items_missing_from_profile():
    text = CLEAN + "Platform: Python, versioned recipes, async\nJob360 | Python, Rust | 2025\n"
    checks = cc.check_text("cv", text, _profile())
    detail = next(c["detail"] for c in checks if c["code"] == "unsupported_terms")
    assert "versioned recipes" in detail and "async" in detail and "Rust" in detail
    assert "Python" not in detail and "Go" not in detail  # Go comes from GitHub languages


def test_dropped_present_role_flags():
    text = CLEAN.replace("Job360", "Acme")
    assert "missing_current_roles" in _codes("cv", text)
    assert "missing_current_roles" not in _codes("cv", CLEAN)


def test_leaked_chatter_flags_and_clean_text_does_not():
    assert "leaked_chatter" in _codes("cv", "Here is the revised CV\n" + CLEAN)
    assert "leaked_chatter" in _codes("cover_letter", "Dear team,\nI hope this finds you well. Let me know.")
    assert "leaked_chatter" not in _codes("cv", CLEAN)


def test_cover_letter_skips_cv_only_checks():
    codes = _codes("cover_letter", CLEAN.replace("Job360", "Acme") + "Platform: Rust, Zig, Nim\n")
    assert "missing_current_roles" not in codes and "unsupported_terms" not in codes


def test_no_profile_means_only_chatter_runs():
    assert cc.check_text("cv", CLEAN, None) == []


# -- PDF parse check, real renderer ------------------------------------------

TINY = (
    "RANJITH EXAMPLE\nAI Engineer\nLondon | +44 7442 564327 | ranjith@example.com\n\n"
    "SUMMARY\nBuilds agent systems.\n\nSKILLS\nLanguages: Python, Go, Docker\n"
)


def test_pdf_check_reports_spare_room_and_no_other_problem():
    pdf = render_cv_pdf(TINY, None)
    codes = {c["code"]: c for c in cc.check_pdf(pdf, TINY, template_pages=1)}
    assert set(codes) == {"pdf_space"}
    assert codes["pdf_space"]["severity"] == "info" and "more lines" in codes["pdf_space"]["detail"]


def test_pdf_check_flags_contact_missing_from_pdf_and_too_many_pages():
    pdf = render_cv_pdf(TINY, None)
    codes = {c["code"] for c in cc.check_pdf(pdf, TINY + "other@example.org +44 1632 960000\n", template_pages=1)}
    assert "pdf_contact" in codes
    long_cv = TINY + "\n".join(f"- Line {i} of a very long bullet list to overflow the page." for i in range(150))
    pages_pdf = render_cv_pdf(long_cv, {"page": {"pages": 3}})
    assert "pdf_pages" in {c["code"] for c in cc.check_pdf(pages_pdf, long_cv, template_pages=1)}


def test_garbled_detector():
    assert cc.garbled("a (cid:12) b") and cc.garbled("bad � char")
    assert not cc.garbled("fine text")


def test_pdf_unreadable_bytes_do_not_raise():
    assert cc.check_pdf(b"not a pdf", "x")[0]["code"] == "pdf_unreadable"


# -- filename ----------------------------------------------------------------


def test_legal_file_stem_prefers_legal_names_then_cv_name_and_is_ascii():
    p = _profile()
    assert legal_file_stem(p, "cv") == "Ranjith_Example_CV"  # falls back to cv_data.name
    p.user_info = UserInfo(contact={"legal_first_name": "Zoë", "legal_last_name": "O'Brien Müller"})
    assert legal_file_stem(p, "cv") == "Zoe_O-Brien-Muller_CV"
    assert legal_file_stem(p, "cover_letter") == "Zoe_O-Brien-Muller_Cover_Letter"
    assert legal_file_stem(p, "answers") is None
    assert legal_file_stem(UserProfile(), "cv") is None


# -- end to end: the save reply carries the checks ------------------------------

@pytest.mark.asyncio
async def test_save_artifact_reply_carries_checks(authenticated_async_context):
    from tests.test_application_spine import _AD  # plain constant, not a fixture

    async with authenticated_async_context() as client:
        brought = await client.post("/api/jobs/bring", json=_AD)
        app_id = brought.json()["application_id"]
        text = "Here is the revised CV\n" + TINY
        r = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
        assert r.status_code == 201, r.text
        codes = {c["code"] for c in r.json()["checks"]}
        assert "leaked_chatter" in codes and "pdf_space" in codes
        r2 = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "answers", "text": text})
        assert r2.status_code == 201 and r2.json()["checks"] == []
