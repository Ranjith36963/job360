"""False-weak CV reviews (production 2026-11-09/10): CVs whose every claim IS in the profile were stored weak.

Two runs of 40 CVs: 74 of 80 needed a rewrite; the live queue then held 231 weak vs 37 strong (app 478: 30 of 30
lines covered, claimed strong, stored weak ``[claim_not_in_quote, quote_too_thin]``). The shapes, each true to
the profile: "UK" for "United Kingdom"; a "London, UK | +44 ... | email" header; a "github.com/x | ... | job360.uk"
links line; a "Calnex Solutions | AI Solutions Engineer, R&D | Jun 2025 - Sep 2025 | Stevenage" role header; a
"Support Agent | LangGraph, MCP, Gmail, Slack" project header; "Agents & LLMs: LangGraph, ..." skills lines that
reorder, cut or merge profile skill lines under another label; "EDUCATION AND CERTIFICATION" for "... & ...".
Each must now be stored strong, and every attack below must stay weak. The false weaks had also spent MAX_WEAK:
a weak review judged under older rules no longer counts toward ``retry_limit``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.services.applications import cv_review
from src.services.profile.models import AssistantSettings, CVData, UserProfile
from tests.test_cv_review import _event, _review, _save_cv, _setup, _stored

RAW = (
    "Ada Lovelace\nLondon, United Kingdom | +44 7700 900123 | ada@example.com\nEXPERIENCE\n"
    "Built agent evals for test equipment at Calnex.\n"
    "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
    "Relocated to the United Kingdom in 2019 to join Northwind.\n"
    "Backend engineering with FastAPI.\n"
    "SKILLS\nLanguages: Python, SQL, TypeScript\nData: Snowflake, dbt, Airflow, Postgres\n"
    "Agents & LLMs: LangGraph, LangChain, Claude API, MCP\n"
    "EDUCATION & CERTIFICATION\nMSc Computer Science | University of Leeds | 2018\n"
    # app 478's profile: the PDF reader joined the title and the dates ("Intern Oct 2024")
    "Nethermind | AI/ML Engineer Intern Oct 2024 – Jan 2025 | Remote\n"
)
CV_DATA = {
    "name": "Ada Lovelace", "raw_text": RAW, "location": "London, United Kingdom",
    "links": ["github.com/ada", "linkedin.com/in/ada", "ada.github.io", "job360.uk"],
    "skills": ["Python", "SQL", "TypeScript", "Snowflake", "dbt", "Airflow", "Postgres", "FastAPI"],
    "cv_positions": [
        {"company": "Calnex Solutions", "title": "AI Solutions Engineer, R&D", "dates": "Jun 2025 – Sep 2025",
         "location": "Stevenage", "bullets": ["Built agent evals for test equipment at Calnex."]},
        {"company": "Northwind Analytics", "title": "Data Engineer", "dates": "Jan 2019 – May 2025",
         "location": "London", "bullets": ["Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."]},
        {"company": "Job360 (job360.uk)", "title": "Founder & AI-Native Engineer", "dates": "Feb 2026 – Mar 2026",
         "location": "United Kingdom (Remote)"},
    ],
    "cv_projects": [{"name": "Human-in-the-Loop Support Agent", "stack": ["LangGraph", "MCP", "Gmail", "Slack"]}],
}
ETL = "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%."
BASE = f"Ada Lovelace\nEXPERIENCE\n- {ETL}\n"
ETL_ROW = {"cv_line": f"- {ETL}", "profile_quote": ETL, "field_path": "cv_data.raw_text"}
# The owner's shapes, every one true to the profile above.
TRUE_LINES = (
    "Calnex Solutions | AI Solutions Engineer, R&D | Jun 2025 - Sep 2025 | Stevenage",
    "Northwind Analytics | Data Engineer | London",
    "Human-in-the-Loop Support Agent | LangGraph, MCP, Gmail, Slack",
    "Backend & Data: Python, SQL, Postgres, Snowflake, dbt",
    "Agents & LLMs: MCP, Claude API, LangGraph",
    "Engineering: Python, FastAPI",
    "MSc Computer Science | University of Leeds | 2018",
    "- Relocated to the UK in 2019 to join Northwind.",
)


def _profile() -> UserProfile:
    return UserProfile(cv_data=CVData(**CV_DATA))


def _seed(user_id: str) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(_profile(), user_id, source_action="cv_upload")
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")


async def _verdict(client: Any, line: str, quote: str) -> tuple[str, list[str]]:
    """One CV = BASE + ``line``, reviewed strong with ``quote`` for that line: the stored verdict."""
    cv = BASE + line + "\n"
    app_id, cv_id = await _setup(client, cv=cv)
    got = await _stored(client, app_id, _review(cv_id, cv, lines=[
        ETL_ROW, {"cv_line": line, "profile_quote": quote, "field_path": "cv_data.raw_text"}]))
    return got["confidence"], got["downgraded"]


# ── Pure: which lines need a row ────────────────────────────────────────────


def test_contact_location_links_headers_and_and_headings_need_no_row():
    """A: "London, UK | +44 | email" (the profile's own location), C: "EDUCATION AND CERTIFICATION", and a links
    line of the profile's own sites need no quote; a place alone, an unknown site or a claim beside them still do."""
    text = ("Ada Lovelace\nLondon, UK | +44 7700 900123 | ada@example.com\n"
            "github.com/ada | linkedin.com/in/ada | ada.github.io | job360.uk\nEDUCATION AND CERTIFICATION\n"
            "EDUCATION & CERTIFICATION\nLondon, UK\nevil.example | ada@example.com\n"
            "Leeds | ada@example.com\nNorthwind | +44 7700 900123\nUK 2019 | ada@example.com\n"
            "Stevenage, UK | ada@example.com\nRemote | ada@example.com\n")  # a JOB's place is not the person's
    assert cv_review.content_lines(text, _profile()) == [
        "London, UK", "evil.example | ada@example.com", "Leeds | ada@example.com", "Northwind | +44 7700 900123",
        "UK 2019 | ada@example.com", "Stevenage, UK | ada@example.com", "Remote | ada@example.com"]
    # the person's residence in user_info.contact counts; a city and a country code they gave
    lives = UserProfile(cv_data=CVData(name="Ada Lovelace"))
    lives.user_info.contact = {"residence_city": "Leeds", "residence_country": "GB"}
    assert cv_review.content_lines("Ada Lovelace\nLeeds, UK | ada@example.com\n", lives) == []


def test_one_profile_record_holds_a_header_line_and_two_never_combine():
    records = cv_review.profile_records(_profile())
    for line in TRUE_LINES[:3] + ("MSc Computer Science | University of Leeds | 2018",
                                  "Stevenage | Calnex Solutions | R&D",
                                  "Nethermind | AI/ML Engineer Intern | Oct 2024 - Jan 2025 | Remote",  # a bar added
                                  "**AI Solutions Engineer, R&D, Calnex Solutions** | Jun 2025 - Sep 2025 | Stevenage",
                                  "Job360 (job360.uk) | Founder and AI-Native Engineer | Remote"):  # "&" = "and"
        assert cv_review.record_line_holds(line, records), line
    for line in ("Calnex Solutions | Data Engineer",  # a title moved to another employer
                 "Northwind Analytics | AI Solutions Engineer, R&D | Jun 2025 - Sep 2025",
                 "Calnex Solutions | Senior AI Solutions Engineer, R&D",  # a word the record lacks
                 "Calnex Solutions | AI Solutions Engineer, R&D | Jun 2024 - Sep 2025",  # a date it lacks
                 "Calnex Solutions | AI Solutions Engineer, R&D | Jun 2025",  # half a date range
                 "Human-in-the-Loop Support Agent | LangGraph, Kubernetes",  # a tool the project lacks
                 "Nethermind | AI/ML Engineer Intern | Oct 2024",  # half the joined range
                 "Nethermind | Intern | Oct 2024 - Jan 2025",  # a cut title
                 "Job360 (job360.uk) | Data Engineer | Remote",
                 "Calnex Solutions, AI Solutions Engineer, R&D",  # commas only: a quote must hold it
                 "Calnex Solutions"):  # one piece is a plain line: a quote must hold it
        assert not cv_review.record_line_holds(line, records), line


# Checker, 2026-11-10: each was stored strong on b9c8e74cc (a label of ANY profile word): skills tied to an employer,
# title, project, degree or section the profile never tied them to.
LABEL_ATTACKS = (
    "Calnex: Python, SQL", "Northwind: Python, SQL", "Founder: Python, SQL", "Data Engineer: Python, SQL",
    "MSc: Python, SQL", "Leeds: Python, SQL", "Human-in-the-Loop Support Agent: Python, SQL",
    "Founder Engineer: Python, SQL", "Agents: Python, SQL", "Education: Python, SQL",
)


def test_a_list_line_holds_only_whole_profile_items_under_a_profile_label():
    book = cv_review.skill_book(_profile())
    for line in TRUE_LINES[3:6] + ("Python, SQL", "- Data: dbt | Airflow", "Languages: SQL, Python",
                                   "Agents & LLMs: MCP, LangGraph", "Technical Skills: Python, dbt, MCP"):
        assert cv_review.list_line_holds(line, book), line
    for line in LABEL_ATTACKS + ("Data: Python, SQL", "Agents & LLMs: Python, MCP"):  # a named group lends only its own
        assert not cv_review.list_line_holds(line, book), line
    for line in ("Backend & Data: Python, SQL, Kubernetes",  # an invented skill among true ones
                 "Languages: Python (7 years), SQL",  # a number inside an item
                 "Languages: Python 3, SQL",
                 "Microsoft: Python, SQL",  # an employer the profile lacks as the label
                 "Staff Engineer at Google: Python, SQL",  # a title the profile lacks as the label
                 "Led the Snowflake migration, cut costs, Python",  # prose disguised with commas
                 "Data: Snowflake, dbt and Airflow pipelines saving 30%",
                 "Python"):  # one item is no list
        assert not cv_review.list_line_holds(line, book), line


def test_a_huge_header_line_is_bounded_and_weak():
    """Checker: "Stevenage | London | " x1000 took 7.4 s of CPU inside the request; x2500 ran for minutes."""
    import time

    records = cv_review.profile_records(_profile())
    start = time.process_time()
    assert not cv_review.record_line_holds("Stevenage | London | " * 2500, records)
    assert not cv_review.record_line_holds(" | ".join(["Stevenage", "London"] * 7), records)  # 14 pieces
    assert time.process_time() - start < 1.0


def test_a_one_line_raw_text_is_never_one_record():
    """Checker: a CV text that is ONE long line held "Calnex Solutions | Data Engineer Jan 2019 – May 2025" (another
    job's title and dates). A text line over RAW_RECORD_MAX_CHARS is a paragraph, never one record."""
    one = " | ".join([*RAW.split("\n"), "Calnex Solutions", "AI Solutions Engineer, R&D Jun 2025 – Sep 2025",
                      "Stevenage", "Northwind Analytics", "Data Engineer Jan 2019 – May 2025", "London"])
    records = cv_review.profile_records(UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text=one)))
    assert not cv_review.record_line_holds("Calnex Solutions | Data Engineer Jan 2019 – May 2025", records)
    assert not cv_review.record_line_holds("Calnex Solutions | Stevenage", records)


def test_quotes_compare_word_for_word_and_the_profile_says_its_location_and_bold_is_no_name():
    source = cv_review._norm(RAW)
    words = f' {" ".join(cv_review.evidence._words(source))} '
    assert cv_review.quote_in_profile("in Python and Snowflake cutting warehouse costs 30%", source, words)
    for fake in ("in Python and Snowflake cutting warehouse costs 60%", "in Python Snowflake cutting warehouse costs 30%",
                 "Snowflake and Python"):
        assert not cv_review.quote_in_profile(fake, source, words), fake
    assert "London, United Kingdom" in cv_review.cv_checks.profile_source_text(
        UserProfile(cv_data=CVData(location="London, United Kingdom")))
    text = "Ada Lovelace\nEXPERIENCE\n- Built agent evals for test equipment at **Calnex Solutions**.\n"  # app 441
    assert cv_review.cv_checks.check_text("cv", text, _profile()) == []
    assert [i["code"] for i in cv_review.cv_checks.check_text("cv", text.replace("Calnex", "Acme"), _profile())] == [
        "unsupported_terms"], "bold never hides an unknown name"


# ── Through the real door ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_owners_true_lines_are_stored_strong(authenticated_async_context, fixture_user_id):
    """Each shape with the quote a reviewer gave it (a piece of the profile, never one quote holding the line)."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        quotes = ("Calnex Solutions", "Data Engineer", "Human-in-the-Loop Support Agent",
                  "Data: Snowflake, dbt, Airflow, Postgres", "LangGraph, LangChain, Claude API, MCP",
                  "Python, SQL, TypeScript", "MSc Computer Science",
                  "Relocated to the United Kingdom in 2019 to join Northwind.")
        for line, quote in zip(TRUE_LINES, quotes):
            assert await _verdict(client, line, quote) == ("strong", []), line
        # a quote that dropped a comma of the profile's sentence (app 579) is still the profile's sentence
        line = "- Built ETL pipelines in Python and Snowflake cutting warehouse costs 30%."
        assert await _verdict(client, line, line[2:]) == ("strong", [])
        # the London, UK header and the heading are no review lines: no row needed, and a row for them is refused
        cv = BASE + "London, UK | +44 7700 900123 | ada@example.com\nEDUCATION AND CERTIFICATION\n"
        app_id, cv_id = await _setup(client, cv=cv)
        got = await _stored(client, app_id, _review(cv_id, cv, lines=[ETL_ROW]))
        assert (got["confidence"], got["downgraded"], got["content_lines"]) == ("strong", [], 1)
        extra = {"n": 2, "verdict": "supported", "evidence": ["London, United Kingdom"]}
        assert (await _event(client, app_id, "cv_reviewed", _review(cv_id, cv, lines=[ETL_ROW, extra]))).status_code == 422


@pytest.mark.asyncio
async def test_attacks_stay_weak(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        for line, quote in (
            ("Backend & Data: Python, SQL, Kubernetes", "Languages: Python, SQL, TypeScript"),
            ("Languages: Python (7 years), SQL", "Languages: Python, SQL, TypeScript"),
            ("Staff Engineer at Google: Python, SQL", "Languages: Python, SQL, TypeScript"),
            ("Microsoft: Python, SQL", "Languages: Python, SQL, TypeScript"),
            ("Led the Snowflake migration, cut costs, Python", ETL),
            ("Calnex Solutions | Data Engineer | Jun 2025 - Sep 2025", "Calnex Solutions"),
            ("Calnex Solutions | Lead AI Solutions Engineer, R&D", "AI Solutions Engineer, R&D"),
            # "UK" never smuggles another claim: the quote must say the United Kingdom, and the rest still counts
            ("- Relocated to the UK in 2017 to join Northwind.",
             "Relocated to the United Kingdom in 2019 to join Northwind."),
            ("- Relocated to the UK in 2019 to join Google.",
             "Relocated to the United Kingdom in 2019 to join Northwind."),
            ("- Relocated to the US in 2019 to join Northwind.",
             "Relocated to the United Kingdom in 2019 to join Northwind."),
            *((line, "Languages: Python, SQL, TypeScript") for line in LABEL_ATTACKS),
        ):
            got = await _verdict(client, line, quote)
            assert got[0] == "weak" and got[1], (line, got)


@pytest.mark.asyncio
async def test_job_places_beside_an_email_need_a_row_and_a_one_line_raw_text_holds_no_header(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        for line in ("Stevenage, UK | ada@example.com", "Remote | ada@example.com"):
            cv = BASE + line + "\n"
            app_id, cv_id = await _setup(client, cv=cv)
            got = await _stored(client, app_id, _review(cv_id, cv, lines=[ETL_ROW]))
            assert (got["confidence"], got["downgraded"]) == ("weak", ["line_uncovered"]), line
        from src.services.profile.storage import save_profile

        one = " | ".join([*RAW.split("\n"), "Calnex Solutions", "AI Solutions Engineer, R&D Jun 2025 – Sep 2025",
                          "Stevenage", "Northwind Analytics", "Data Engineer Jan 2019 – May 2025", "London"])
        save_profile(UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text=one)), fixture_user_id,
                     source_action="cv_upload")
        line = "Calnex Solutions | Data Engineer Jan 2019 – May 2025"
        assert (await _verdict(client, line, "Calnex Solutions"))[0] == "weak"


@pytest.mark.asyncio
async def test_a_claimed_strong_stored_weak_reply_names_codes_and_rows(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        line = "- Led a team of 12 engineers."
        cv = BASE + line + "\n"
        app_id, cv_id = await _setup(client, cv=cv)
        payload = _review(cv_id, cv, lines=[ETL_ROW, {"cv_line": line, "profile_quote": "Built agent evals",
                                                     "field_path": "cv_data.raw_text"}])
        resp = await _event(client, app_id, "cv_reviewed", payload)
        assert resp.status_code == 201, resp.text
        warned = [w for w in resp.json()["warnings"] if "STORED WEAK" in w]
        assert len(warned) == 1, resp.json()["warnings"]
        assert "claim_not_in_quote: lines 2" in warned[0] and "first_word_unknown: lines 2" in warned[0]
        assert "cv_checks_found: unsupported_numbers" in warned[0] and "never strong" in warned[0]
        assert "12 engineers" not in warned[0], "no CV text in the reply"
        # an uncovered line is named by its review.lines number
        resp = await _event(client, app_id, "cv_reviewed", _review(cv_id, cv, lines=[ETL_ROW]))
        assert "line_uncovered: lines 2" in " ".join(resp.json()["warnings"])
        # a strong store says nothing of the kind
        cv2 = BASE
        app2, cv2_id = await _setup(client, cv=cv2)
        resp = await _event(client, app2, "cv_reviewed", _review(cv2_id, cv2, lines=[ETL_ROW]))
        assert not [w for w in resp.json()["warnings"] if "STORED WEAK" in w]


async def _old_weak(app_id: int, user_id: str, cv_id: int, sha256: str) -> None:
    """A weak review stored before rules 2 (no ``rules`` key): a false weak the old rules gave."""
    from src.api import dependencies as api_deps
    from src.services.applications import spine

    db = await api_deps.get_db()
    await spine.append_event(
        db, user_id=user_id, application_id=app_id, event_type=cv_review.EVENT_TYPE, payload={
            "artifact_id": cv_id, "sha256": sha256, "claimed": "strong", "confidence": "weak",
            "downgraded": ["claim_not_in_quote", "quote_too_thin"], "lines": 2, "content_lines": 2, "covered": 2,
            "reviewer": "fresh helper", "by": "token:claude-code"},
        occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by="token:claude-code")


@pytest.mark.asyncio
async def test_old_rules_false_weaks_do_not_spend_the_retry_budget(authenticated_async_context, fixture_user_id):
    from src.services.applications import kit

    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        line = TRUE_LINES[0]
        texts = [BASE + line + "\n", BASE.replace("- Built", "* Built") + line + "\n",
                 BASE.replace("- Built", "** Built") + line + "\n"]
        app_id, cv_id = await _setup(client, cv=texts[0])
        ids = [cv_id] + [await _save_cv(client, app_id, t) for t in texts[1:]]
        for art, text in zip(ids, texts):
            await _old_weak(app_id, fixture_user_id, art, kit.sha256_text(text))
        v4 = BASE.replace("- Built", "• Built") + line + "\n"
        v4_id = await _save_cv(client, app_id, v4)
        row = {"cv_line": line, "profile_quote": "Calnex Solutions", "field_path": "cv_data.cv_positions"}
        got = await _stored(client, app_id, _review(v4_id, v4, lines=[ETL_ROW, row]))
        assert (got["confidence"], got["downgraded"], got["rules"]) == ("strong", [], cv_review.RULES)


@pytest.mark.asyncio
async def test_three_weak_reviews_under_these_rules_still_hit_retry_limit(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        bad = "- Led a team of 12 engineers."
        texts = [BASE + bad + "\n", BASE.replace("- Built", "* Built") + bad + "\n",
                 BASE.replace("- Built", "** Built") + bad + "\n"]
        app_id, cv_id = await _setup(client, cv=texts[0])
        row = {"cv_line": bad, "profile_quote": ETL, "field_path": "cv_data.raw_text"}
        for n, text in enumerate(texts):
            art = cv_id if n == 0 else await _save_cv(client, app_id, text)  # each version reviewed while newest
            got = await _stored(client, app_id, _review(art, text, lines=[ETL_ROW, row]))
            assert got["confidence"] == "weak" and "retry_limit" not in got["downgraded"]
        v4 = BASE.replace("- Built", "• Built")
        v4_id = await _save_cv(client, app_id, v4)
        fourth = await _stored(client, app_id, _review(v4_id, v4, lines=[ETL_ROW]))
        assert (fourth["confidence"], fourth["downgraded"]) == ("weak", ["retry_limit"])
