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
        # 2026-10-17: answers get the writing checks too (chatter, stock phrases), never the PDF ones.
        assert r2.status_code == 201
        assert {c["code"] for c in r2.json()["checks"]} == {"leaked_chatter"}


def test_a_letter_date_is_never_an_unsupported_number():
    """A cover letter's letterhead date ("15 October 2026", "October 15th, 2026") is a date,
    not a claim; a real number in the body still counts."""
    from src.services.applications.cv_checks import number_tokens

    assert number_tokens("15 October 2026\nOctober 15th, 2026") == []
    assert ("15", "15") in number_tokens("I shipped it in 15 weeks")


# ── S16 - LinkedIn connection-note limit (warn only) ───────────────────────

_LI_BASE = "I build data pipelines in Python and dbt at Northwind and would value a short chat. "


def _li(n: int) -> str:
    text = (_LI_BASE * 10)[:n]
    assert len(text) == n
    return text


def test_linkedin_note_too_long_free_limit_is_200():
    assert cc.linkedin_note_too_long(_li(200), premium=False) is None
    issue = cc.linkedin_note_too_long(_li(201), premium=False)
    assert issue is not None
    assert issue["code"] == "linkedin_note_too_long"
    assert issue["severity"] == "warn"
    for needle in ("201 characters", "allows 200", "preferences.linkedin_premium"):
        assert needle in issue["detail"]


def test_linkedin_note_too_long_premium_limit_is_300():
    assert cc.linkedin_note_too_long(_li(250), premium=True) is None
    assert cc.linkedin_note_too_long(_li(300), premium=True) is None
    issue = cc.linkedin_note_too_long(_li(301), premium=True)
    assert issue is not None
    assert "301 characters" in issue["detail"]
    assert "allows 300" in issue["detail"]
    assert "preferences.linkedin_premium" not in issue["detail"]


def test_check_text_has_no_channel_check():
    codes = [c["code"] for c in cc.check_text("outreach", _li(400), None)]
    assert "linkedin_note_too_long" not in codes


# ── cv_entry_mismatch: entry lines vs cv_positions (advisory) ───────────────

_ENTRY_PROFILE_POSITIONS = [
    {"company": "Calnex Solutions Ltd", "title": "Software Engineer", "dates": "Mar 2023 - Present", "bullets": []},
    {"company": "Job360 (job360.uk)", "title": "Founder", "dates": "2021 - 2023", "bullets": []},
]


def _entry_profile(positions=None) -> UserProfile:
    pos = _ENTRY_PROFILE_POSITIONS if positions is None else positions
    return UserProfile(cv_data=CVData(raw_text="x", name="A B", cv_positions=pos), preferences=UserPreferences())


def _entry(*lines: str) -> str:
    return "A B\nEngineer\n\nEXPERIENCE\n" + "\n".join(lines) + "\n\nSKILLS\nPython\n"


def test_entry_matching_the_profile_is_silent():
    text = _entry(
        "Calnex Solutions | Software Engineer | Stevenage | 03/2023 – Now",
        "- Led a team of 12.",
        "Job360 | Founder | London | 2021 — 2023",
    )
    assert cc.cv_entry_mismatch(text, _entry_profile()) is None


def test_entry_dates_differ_from_profile():
    text = _entry("Calnex Solutions | Software Engineer | Stevenage | Jan 2020 - Present")
    issue = cc.cv_entry_mismatch(text, _entry_profile())
    assert issue is not None and issue["code"] == "cv_entry_mismatch" and issue["severity"] == "warn"
    assert 'dates: CV says "Jan 2020 - Present", profile says "Mar 2023 - Present"' in issue["detail"]
    assert "title:" not in issue["detail"]


def test_entry_end_date_present_vs_closed_differs():
    text = _entry("Job360 | Founder | London | 2021 - Present")
    issue = cc.cv_entry_mismatch(text, _entry_profile())
    assert issue is not None and 'CV says "2021 - Present", profile says "2021 - 2023"' in issue["detail"]


def test_entry_title_differs():
    text = _entry("Calnex Solutions | Data Scientist | Stevenage | Mar 2023 - Present")
    issue = cc.cv_entry_mismatch(text, _entry_profile())
    assert issue is not None
    assert 'title: CV says "Data Scientist", profile says "Software Engineer"' in issue["detail"]
    assert "dates:" not in issue["detail"]


def test_entry_employer_not_in_profile():
    text = _entry("Initech | Engineer | Leeds | 2019 - 2020", "Job360 | Founder | London | 2021 - 2023")
    issue = cc.cv_entry_mismatch(text, _entry_profile())
    assert issue is not None
    assert "\"Initech\": employer is in none of the profile's positions" in issue["detail"]
    assert "Job360" not in issue["detail"]


def test_fuzzy_company_suffix_case_and_month_names_match():
    text = _entry(
        "CALNEX SOLUTIONS INC. | senior software engineer | Stevenage | March 2023 - Current",
        "Founder | job360 | London | Jan 2021 - Dec 2023",
    )
    assert cc.cv_entry_mismatch(text, _entry_profile()) is None


def test_en_dash_and_hyphen_dates_compare_equal():
    a = cc.cv_entry_mismatch(_entry("Job360 | Founder | London | 2021 – 2023"), _entry_profile())
    b = cc.cv_entry_mismatch(_entry("Job360 | Founder | London | 2021 - 2023"), _entry_profile())
    assert a is None and b is None


def test_bullets_and_other_sections_are_not_checked():
    text = _entry("Job360 | Founder | London | 2021 - 2023", "- Initech | Engineer | 2010 - 2011") + (
        "PROJECTS\nInitech | Rust, Go | 2012 - 2013\n"
    )
    assert cc.cv_entry_mismatch(text, _entry_profile()) is None


def test_no_positions_is_silent():
    text = _entry("Initech | Engineer | Leeds | 2019 - 2020")
    assert cc.cv_entry_mismatch(text, _entry_profile([])) is None
    assert cc.cv_entry_mismatch(text, None) is None
    assert "cv_entry_mismatch" not in [c["code"] for c in cc.check_text("cv", text, _entry_profile([]))]


def test_profile_position_without_dates_or_title_skips_that_part():
    pos = [{"company": "Initech", "title": "", "dates": ""}]
    assert cc.cv_entry_mismatch(_entry("Initech | Anything | Leeds | 2019 - 2020"), _entry_profile(pos)) is None


def test_cv_entry_mismatch_runs_in_check_text_for_cv_only_and_does_not_gate_submit():
    from src.services.applications import kit

    text = _entry("Initech | Engineer | Leeds | 2019 - 2020")
    assert "cv_entry_mismatch" in [c["code"] for c in cc.check_text("cv", text, _entry_profile())]
    assert "cv_entry_mismatch" not in [c["code"] for c in cc.check_text("cover_letter", text, _entry_profile())]
    assert "cv_entry_mismatch" not in kit._UNSUPPORTED_CODES  # advisory: check_submit never asks on it


@pytest.mark.asyncio
async def test_save_reply_carries_cv_entry_mismatch(authenticated_async_context, fixture_user_id):
    from src.services.profile.storage import save_profile
    from tests.test_application_spine import _AD

    async with authenticated_async_context() as client:
        save_profile(_entry_profile(), fixture_user_id, source_action="cv_upload")
        app_id = (await client.post("/api/jobs/bring", json=_AD)).json()["application_id"]
        text = _entry("Calnex Solutions | Software Engineer | Stevenage | Jan 2020 - Present")
        r = await client.post(f"/api/applications/{app_id}/artifacts", json={"kind": "cv", "text": text})
        assert r.status_code == 201, r.text
        hit = [c for c in r.json()["checks"] if c["code"] == "cv_entry_mismatch"]
        assert len(hit) == 1 and "Jan 2020 - Present" in hit[0]["detail"]
