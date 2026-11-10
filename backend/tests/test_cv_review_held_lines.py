"""True-to-profile CVs still stored weak under rules 2 (production trace 2026-11-10, apps 113/345/351/353/389).

Five gaps, each a shape of the owner's own verbatim lines (synthetic names and contacts here):
1. a header line the profile HOLDS (``record_line_holds``) still had its row's quote judged by (c): "Co | Title |
   United Kingdom | dates" quoting itself is not one span of the profile, so ``quote_not_in_profile``;
2. a comma / dash header ("Co - Title, R&D Department, Jun 2025 - Sep 2025, Stevenage, UK") was never held: only a
   standalone ``|`` / ``•`` made a header, and a record said "United Kingdom" where the CV said "UK";
3. "PORTFOLIO PROJECTS" (the raw CV's own heading) was content: "portfolio" was no section word;
4. ``cv_entry_mismatch`` dropped the title written in the date piece ("Title, R&D Jun 2025 – Sep 2025") or beside
   the company ("Title, Co | dates | place") and compared the PLACE as the title;
5. loophole: dict KEYS were profile text, so "Co title X dates Y location Z" passed (c) and (d).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from src.services.applications import cv_checks, cv_review
from src.services.profile.models import AssistantSettings, CVData, UserProfile
from tests.test_cv_review import _save_cv, _setup, _stored

RAW = (
    "Ada Lovelace\nLondon, United Kingdom | +44 7700 900123 | ada@example.com\nAI Engineer | Agentic Systems\n"
    "SUMMARY\nAI engineer building agentic systems with LangGraph and MCP.\nEXPERIENCE\n"
    "Orbitly (orbitly.uk) | Founder & AI-Native Engineer Feb 2026 – Present\n"
    "Built the memory layer for an AI agent, storing profiles and history.\n"
    "Northwind Labs | AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage\n"
    "Engineered a RAG pipeline with Bedrock and ChromaDB, improving relevance by 40%.\n"
    "Tidewater | AI/ML Engineer Intern Oct 2024 – Jan 2025 | Remote\n"
    "Developed data preprocessing pipelines to streamline model training workflows.\n"
    "PORTFOLIO PROJECTS\nSupport Agent | LangGraph, MCP, Gmail, Slack\n"
    "SKILLS\nAgents & LLMs: LangGraph, LangChain, MCP, RAG\n"
)
POSITIONS = [
    {"company": "Orbitly (orbitly.uk)", "title": "Founder & AI-Native Engineer", "dates": "Feb 2026 – Present",
     "location": "United Kingdom", "bullets": ["Built the memory layer for an AI agent, storing profiles and history."]},
    {"company": "Northwind Labs", "title": "AI Solutions Engineer – R&D Department", "dates": "Jun 2025 – Sep 2025",
     "location": "Stevenage, United Kingdom",
     "bullets": ["Engineered a RAG pipeline with Bedrock and ChromaDB, improving relevance by 40%."]},
    {"company": "Tidewater", "title": "AI/ML Engineer Intern", "dates": "Oct 2024 – Jan 2025",
     "location": "United Kingdom (Remote)",
     "bullets": ["Developed data preprocessing pipelines to streamline model training workflows."]},
    {"company": "Brightline", "title": "Data Analyst", "dates": "Jan 2020 – Dec 2021", "location": "Berlin, Germany"},
]
CV_DATA: dict[str, Any] = {
    "name": "Ada Lovelace", "raw_text": RAW, "location": "London, United Kingdom",
    "links": ["github.com/ada", "linkedin.com/in/ada"],
    "skills": ["LangGraph", "LangChain", "MCP", "RAG"], "cv_positions": POSITIONS,
    "cv_projects": [{"name": "Support Agent", "stack": ["LangGraph", "MCP", "Gmail", "Slack"]}],
}
SUMMARY = "AI engineer building agentic systems with LangGraph and MCP."
B1 = "Built the memory layer for an AI agent, storing profiles and history."
B2 = "Engineered a RAG pipeline with Bedrock and ChromaDB, improving relevance by 40%."
B3 = "Developed data preprocessing pipelines to streamline model training workflows."


def _profile() -> UserProfile:
    return UserProfile(cv_data=CVData(**CV_DATA))


def _cv(name: str, headers: tuple[str, str, str], projects_heading: str = "PROJECTS") -> str:
    h1, h2, h3 = headers
    return (f"{name}\nLondon, United Kingdom | +44 7700 900123 | ada@example.com\n"
            "github.com/ada | linkedin.com/in/ada\nAI Engineer | Agentic Systems\nSUMMARY\n"
            f"{SUMMARY}\nEXPERIENCE\n{h1}\n- {B1}\n{h2}\n- {B2}\n{h3}\n- {B3}\n{projects_heading}\n"
            "Support Agent | LangGraph, MCP, Gmail, Slack\nSKILLS\nAgents & LLMs: LangGraph, LangChain, MCP, RAG\n")


# The five production CVs' SHAPES (app number: its header lines, each verbatim in shape).
OWNER_SHAPES = {
    113: _cv("Ada Lovelace", (
        "Orbitly (orbitly.uk) | Founder & AI-Native Engineer Feb 2026 – Present",
        "Northwind Labs | AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage",
        "Tidewater | AI/ML Engineer Intern Oct 2024 – Jan 2025 | Remote"), "PORTFOLIO PROJECTS"),
    351: _cv("Ada Lovelace", (
        "Orbitly (orbitly.uk) | Founder & AI-Native Engineer | United Kingdom | Feb 2026 - Present",
        "Northwind Labs | AI Solutions Engineer – R&D Department | Stevenage, United Kingdom | Jun 2025 - Sep 2025",
        "Tidewater | AI/ML Engineer Intern | United Kingdom (Remote) | Oct 2024 - Jan 2025")),
    345: _cv("ADA LOVELACE", (
        "Orbitly (orbitly.uk) | Founder & AI-Native Engineer | United Kingdom | Feb 2026 - Present",
        "Northwind Labs | AI Solutions Engineer – R&D Department | Stevenage, United Kingdom | Jun 2025 - Sep 2025",
        "Tidewater | AI/ML Engineer Intern | United Kingdom (Remote) | Oct 2024 - Jan 2025"), "PORTFOLIO PROJECTS"),
    353: _cv("Ada Lovelace", (
        "Orbitly (orbitly.uk) - Founder & AI-Native Engineer, Feb 2026 - Present, United Kingdom",
        "Northwind Labs - AI Solutions Engineer, R&D Department, Jun 2025 - Sep 2025, Stevenage, UK",
        "Tidewater - AI/ML Engineer Intern, Oct 2024 - Jan 2025, UK (Remote)")),
    389: _cv("Ada Lovelace", (
        "Founder & AI-Native Engineer, Orbitly (orbitly.uk) | Feb 2026 - Present | United Kingdom",
        "AI Solutions Engineer – R&D Department, Northwind Labs | Jun 2025 - Sep 2025 | Stevenage, United Kingdom",
        "AI/ML Engineer Intern, Tidewater | Oct 2024 - Jan 2025 | United Kingdom (Remote)")),
}


def _self_rows(text: str, profile: UserProfile) -> list[dict[str, str]]:
    """Every required line quoting itself, as a reviewer that copies the CV gives it."""
    return [{"cv_line": ln, "profile_quote": ln.strip().lstrip(cv_review._BULLETS + " \t"),
             "field_path": "cv_data.raw_text"} for ln in cv_review.content_lines(text, profile)]


def _seed(user_id: str, profile: UserProfile | None = None) -> None:
    from src.services.profile.storage import save_assistant_settings, save_profile

    save_profile(profile or _profile(), user_id, source_action="cv_upload")
    save_assistant_settings(user_id, AssistantSettings(apply_min_score=50, above_line="auto", below_line="auto"), "seed")


# ── Gap 2: comma / dash headers held by ONE record; places compared by the closed table ─────────


def test_a_comma_or_dash_header_is_held_by_one_record():
    records = cv_review.profile_records(_profile())
    for line in ("Orbitly (orbitly.uk) - Founder & AI-Native Engineer, Feb 2026 - Present, United Kingdom",
                 "Northwind Labs - AI Solutions Engineer, R&D Department, Jun 2025 - Sep 2025, Stevenage, UK",
                 "Tidewater - AI/ML Engineer Intern, Oct 2024 - Jan 2025, UK (Remote)",
                 # a comma inside the title: neighbouring pieces join into the raw line's "R&D Jun 2025 – Sep 2025"
                 "Northwind Labs - AI Solutions Engineer, R&D, Jun 2025 - Sep 2025, Stevenage",
                 "Brightline, Data Analyst, Jan 2020 - Dec 2021, Berlin, Germany"):
        assert cv_review.record_line_holds(line, records), line


def test_comma_header_attacks_stay_claims():
    records = cv_review.profile_records(_profile())
    for line in (
        "Northwind Labs - AI/ML Engineer Intern, Jun 2025 - Sep 2025, Stevenage",  # two jobs mixed
        "Tidewater - AI Solutions Engineer, R&D Department, Oct 2024 - Jan 2025, UK (Remote)",
        "Northwind Labs - Senior AI Solutions Engineer, R&D Department, Jun 2025 - Sep 2025, Stevenage",  # invented
        "Northwind Labs - AI Solutions Engineer, R&D Department, Jun 2025 - Sep 2025, Stevenage, UK, Kubernetes",
        "Northwind Labs - AI Solutions Engineer, R&D Department, Jun 2024 - Sep 2025, Stevenage",  # a date it lacks
        "Brightline, Data Analyst, Jan 2020 - Dec 2021, Berlin, UK",  # "UK" for a job the record places in Germany
        "Brightline, Data Analyst, Jan 2020 - Dec 2021, Berlin, US",
        "Northwind Labs, AI Solutions Engineer, Stevenage",  # no date range: a quote must hold it
        "Northwind Labs - Stevenage",  # two pieces only
    ):
        assert not cv_review.record_line_holds(line, records), line
    # "R&D Department" is held only because the record says "Department"
    short = [{**p, "title": "AI Solutions Engineer – R&D"} if p["company"] == "Northwind Labs" else p for p in POSITIONS]
    records = cv_review.profile_records(UserProfile(cv_data=CVData(**{**CV_DATA, "cv_positions": short})))
    assert not cv_review.record_line_holds(
        "Northwind Labs - AI Solutions Engineer, R&D Department, Jun 2025 - Sep 2025, Stevenage", records)


def test_a_comma_header_is_bounded():
    import time

    records = cv_review.profile_records(_profile())
    start = time.process_time()
    assert not cv_review.record_line_holds("Stevenage, Jun 2025 - Sep 2025, " * 500, records)
    assert not cv_review.record_line_holds(", ".join(["Stevenage", "Jun 2025 - Sep 2025"] * 7), records)
    assert time.process_time() - start < 1.0


def test_record_places_read_the_closed_abbreviation_table():
    records = cv_review.profile_records(_profile())
    assert cv_review.record_line_holds("Tidewater | AI/ML Engineer Intern | UK (Remote) | Oct 2024 - Jan 2025", records)
    assert cv_review.record_line_holds("Northwind Labs | Stevenage, UK | Jun 2025 - Sep 2025", records)
    assert not cv_review.record_line_holds("Brightline | Data Analyst | Berlin, UK", records)
    assert cv_checks.place_variant("Stevenage, United Kingdom (Remote)") == "Stevenage, UK (Remote)"
    assert cv_checks.place_variant("(UK) and us") == "(United Kingdom) and us"  # "us" the pronoun is no place
    assert cv_checks.place_variant("Berlin, Germany") == ""


# ── Gap 3: "PORTFOLIO PROJECTS" is a heading when the profile says the words ──────────────────


def test_portfolio_projects_is_a_heading_only_when_the_profile_says_it():
    text = "Ada Lovelace\nPORTFOLIO PROJECTS\nPORTFOLIO\nPORTFOLIO MANAGER\nPORTFOLIO: Kubernetes, Terraform\n"
    assert cv_review.content_lines(text, _profile()) == ["PORTFOLIO MANAGER", "PORTFOLIO: Kubernetes, Terraform"]
    plain = UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text="PROJECTS\nSupport Agent"))
    assert cv_review.content_lines(text, plain) == [
        "PORTFOLIO PROJECTS", "PORTFOLIO", "PORTFOLIO MANAGER", "PORTFOLIO: Kubernetes, Terraform"]


# ── Gap 4: the title beside the dates or the company is read as a title, never the place ──────


def test_entry_lines_with_the_title_beside_dates_or_company_match_the_profile():
    for app in (113, 351, 345, 353, 389):
        assert cv_checks.cv_entry_mismatch(OWNER_SHAPES[app], _profile()) is None, app


def test_a_wrong_title_is_still_found():
    def lines(*headers: str) -> str:
        return "Ada Lovelace\nEXPERIENCE\n" + "".join(f"{h}\n" for h in headers)

    for header in (
        "Northwind Labs | AI/ML Engineer Intern | Jun 2025 - Sep 2025 | Stevenage",  # moved to another employer
        "AI/ML Engineer Intern, Northwind Labs | Jun 2025 - Sep 2025 | Stevenage",
        "Northwind Labs | AI/ML Engineer Intern Jun 2025 – Sep 2025 | Stevenage",
        "Northwind Labs | Senior AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage",  # "Senior" added
        "Senior AI Solutions Engineer – R&D Department, Northwind Labs | Jun 2025 - Sep 2025 | Stevenage",
        "Senior Founder & AI-Native Engineer, Orbitly (orbitly.uk) | Feb 2026 - Present | United Kingdom",
    ):
        found = cv_checks.cv_entry_mismatch(lines(header), _profile())
        assert found is not None and "title" in found["detail"], header


# ── Gap 5: dict keys are never profile text ───────────────────────────────────────────────────


def test_dict_keys_are_not_profile_text_but_github_language_names_are():
    profile = _profile()
    profile.cv_data.github_languages = {"Rust": 120000, "Elixir": 5000}
    source = cv_checks.profile_source_text(profile)
    words = set(cv_review.evidence._words(source))
    assert not {"company", "title", "dates", "location", "bullets", "stack"} & words
    assert {"rust", "elixir"} <= words and "120000" not in words, "a language is its name, never its byte count"
    assert {"rust", "elixir"} <= cv_review.skill_book(profile).items
    norm = cv_review._norm(source)
    quote = ("Northwind Labs title AI Solutions Engineer – R&D Department dates Jun 2025 – Sep 2025 location "
             "Stevenage, United Kingdom")
    assert not cv_review.quote_in_profile(quote, norm, f' {" ".join(cv_review.evidence._words(norm))} ')


# ── Through the real door ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_five_owner_shapes_are_stored_strong_quoting_themselves(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        for app, text in OWNER_SHAPES.items():
            app_id, cv_id = await _setup(client, cv=text)
            got = await _stored(client, app_id, {
                "artifact_id": cv_id, "sha256": _sha(text), "confidence": "strong",
                "lines": _self_rows(text, _profile()), "reviewer": "fresh helper"})
            assert (got["confidence"], got["downgraded"], got["rules"]) == ("strong", [], cv_review.RULES), app


def _sha(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _verdict(client: Any, text: str, rows: list[dict[str, str]]) -> tuple[str, list[str]]:
    app_id, cv_id = await _setup(client, cv=text)
    got = await _stored(client, app_id, {"artifact_id": cv_id, "sha256": _sha(text), "confidence": "strong",
                                         "lines": rows, "reviewer": "fresh helper"})
    return got["confidence"], got["downgraded"]


HELD = "Orbitly (orbitly.uk) | Founder & AI-Native Engineer | United Kingdom | Feb 2026 - Present"
CLAIM = "- Led a team of 12 engineers at Orbitly."


@pytest.mark.asyncio
async def test_a_held_line_row_is_not_judged_by_its_quote(authenticated_async_context, fixture_user_id):
    """Gap 1: the held header's own text (not one span of the profile) is no quote_not_in_profile."""
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        text = f"Ada Lovelace\nEXPERIENCE\n{HELD}\n- {B1}\n"
        rows = [{"cv_line": HELD, "profile_quote": HELD, "field_path": "cv_data.cv_positions"},
                {"cv_line": f"- {B1}", "profile_quote": B1, "field_path": "cv_data.raw_text"}]
        assert await _verdict(client, text, rows) == ("strong", [])


@pytest.mark.asyncio
async def test_the_held_row_skip_never_passes_another_line(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        text = f"Ada Lovelace\nEXPERIENCE\n{HELD}\n{CLAIM}\n"
        held_row = {"cv_line": HELD, "profile_quote": "Led a team of 12 engineers", "field_path": "x"}
        # the unheld claim has no row of its own: the held row covers only its own line
        assert await _verdict(client, text, [held_row]) == ("weak", ["line_uncovered", "cv_checks_found"])
        # the claim's row quoting the held header: judged, and weak
        got = await _verdict(client, text, [held_row, {"cv_line": CLAIM, "profile_quote": HELD, "field_path": "x"}])
        assert got[0] == "weak" and {"quote_not_in_profile", "claim_not_in_quote"} <= set(got[1]), got
        # a held-shaped row naming a line the CV lacks is still line_not_in_cv, and its quote is still judged
        other = "Tidewater | AI/ML Engineer Intern | United Kingdom (Remote) | Oct 2024 - Jan 2025"
        text = f"Ada Lovelace\nEXPERIENCE\n{HELD}\n"
        got = await _verdict(client, text, [held_row, {"cv_line": other, "profile_quote": other, "field_path": "x"}])
        assert got == ("weak", ["line_not_in_cv", "quote_not_in_profile"]), got


@pytest.mark.asyncio
async def test_new_shape_attacks_stay_weak(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        for line, quote in (
            ("Northwind Labs - AI/ML Engineer Intern, Jun 2025 - Sep 2025, Stevenage", "Northwind Labs"),
            ("Northwind Labs - Senior AI Solutions Engineer, R&D Department, Jun 2025 - Sep 2025, Stevenage",
             "AI Solutions Engineer – R&D Department"),
            ("Brightline, Data Analyst, Jan 2020 - Dec 2021, Berlin, UK", "Brightline"),
            ("PORTFOLIO MANAGER", "PORTFOLIO PROJECTS"),
            ("Northwind Labs - AI Solutions Engineer, R&D Department, Jun 2025 - Sep 2025, Stevenage, UK",
             "Northwind Labs title AI Solutions Engineer – R&D Department dates Jun 2025 – Sep 2025 location "
             "Stevenage, United Kingdom"),  # the TRUE comma header: held now, so its (dict-key) quote is not judged
        ):
            text = f"Ada Lovelace\nEXPERIENCE\n{HELD}\n- {B2}\n{line}\n"  # the current role, as every CV has it
            rows = [{"cv_line": HELD, "profile_quote": HELD, "field_path": "x"},
                    {"cv_line": f"- {B2}", "profile_quote": B2, "field_path": "x"},
                    {"cv_line": line, "profile_quote": quote, "field_path": "x"}]
            got = await _verdict(client, text, rows)
            if line.endswith("Stevenage, UK"):  # the TRUE comma header is held now: the key quote is not needed
                assert got == ("strong", []), (line, got)
                continue
            assert got[0] == "weak" and set(got[1]) - {"cv_checks_found"}, (line, got)  # a quote rule caught it
        # the dict-key quote on a summary line no record holds: quote_not_in_profile now
        line = "- Northwind Labs AI Solutions Engineer R&D Department Jun 2025 Sep 2025 Stevenage United Kingdom"
        quote = ("Northwind Labs title AI Solutions Engineer – R&D Department dates Jun 2025 – Sep 2025 location "
                 "Stevenage, United Kingdom")
        text = f"Ada Lovelace\nEXPERIENCE\n{HELD}\n- {B2}\n{line}\n"
        got = await _verdict(client, text, [{"cv_line": HELD, "profile_quote": HELD, "field_path": "x"},
                                            {"cv_line": f"- {B2}", "profile_quote": B2, "field_path": "x"},
                                            {"cv_line": line, "profile_quote": quote, "field_path": "x"}])
        assert got[0] == "weak" and "quote_not_in_profile" in got[1], got


@pytest.mark.asyncio
async def test_rules_two_weak_reviews_no_longer_spend_the_budget(authenticated_async_context, fixture_user_id):
    """RULES is 3: the reviews these five gaps stored weak under rules 2 stop counting (one fresh budget)."""
    from src.api import dependencies as api_deps
    from src.services.applications import kit, spine

    assert cv_review.RULES == 3
    async with authenticated_async_context() as client:
        _seed(fixture_user_id)
        texts = [OWNER_SHAPES[351].replace("- Built", p) for p in ("- Built", "* Built", "** Built")]
        app_id, cv_id = await _setup(client, cv=texts[0])
        ids = [cv_id] + [await _save_cv(client, app_id, t) for t in texts[1:]]
        db = await api_deps.get_db()
        for art, text in zip(ids, texts):
            await spine.append_event(db, user_id=fixture_user_id, application_id=app_id,
                                     event_type=cv_review.EVENT_TYPE, payload={
                                         "artifact_id": art, "sha256": kit.sha256_text(text), "claimed": "strong",
                                         "confidence": "weak", "downgraded": ["quote_not_in_profile"], "lines": 30,
                                         "content_lines": 30, "covered": 30, "reviewer": "fresh helper",
                                         "by": "token:claude-code", "rules": 2},
                                     occurred_at=datetime.now(timezone.utc).isoformat(), recorded_by="token:claude-code")
        v4 = OWNER_SHAPES[351].replace("- Built", "• Built")
        v4_id = await _save_cv(client, app_id, v4)
        got = await _stored(client, app_id, {"artifact_id": v4_id, "sha256": _sha(v4), "confidence": "strong",
                                             "lines": _self_rows(v4, _profile()), "reviewer": "fresh helper"})
        assert (got["confidence"], got["downgraded"], got["rules"]) == ("strong", [], 3)


# ── Checker, 2026-11-10 on 6ae101af7: two bypasses, both stored STRONG ────────────────────────
# A held line's row skips (c) and (d) ENTIRELY, so record_line_holds / list_line_holds ARE the gate for such a line:
# what a record may hold has to be a header's facts, never a sentence.

BYP_POSITIONS = [
    {"company": "Calnex Solutions", "title": "AI Solutions Engineer, R&D", "dates": "Jun 2025 – Sep 2025",
     "location": "Stevenage", "bullets": ["Built agent evals for test equipment, UK."]},  # a bullet naming a place
    {"company": "Orbitly (orbitly.uk)", "title": "Founder & AI-Native Engineer", "dates": "Feb 2026 – Present",
     "location": "Berlin, Germany"},
    {"company": "Northwind Analytics", "title": "Data Engineer", "dates": "Jan 2019 – May 2025", "location": "London",
     "bullets": ["Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.",
                 "Automated weekly reporting, saving 10 hours a week.", "Led a team of 5, mentoring juniors."]},
]
BYP_RAW = ("Ada Lovelace\nEXPERIENCE\nCalnex Solutions | AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage\n"
           "Built agent evals for test equipment, UK.\n"
           "Built ETL pipelines in Python and Snowflake, cutting warehouse costs 30%.\n"
           "Led a team of 5, Jan 2019 - May 2025, cutting costs.\n")  # a raw bullet with dates is no header
CURRENT = "Orbitly (orbitly.uk) | Founder & AI-Native Engineer | Feb 2026 - Present"
# Bypass 1: clauses of one job's bullets (or of two bullets) recombined into a "header"
CLAUSE_ATTACKS = (
    "Built ETL pipelines in Python and Snowflake, Jan 2019 - May 2025, saving 10 hours a week.",
    "Led a team of 5, Jan 2019 - May 2025, cutting warehouse costs 30%.",
    "Northwind Analytics - Data Engineer, Jan 2019 - May 2025, mentoring juniors",
    "Built ETL pipelines in Python and Snowflake | saving 10 hours a week.",
    "Calnex Solutions | AI Solutions Engineer, R&D | UK",  # "UK" only a bullet says
    "Led a team of 5 | Jan 2019 - May 2025 | cutting costs",  # clauses of a raw bullet line
)
# Bypass 2: one quote spanning two fields / two records (Calnex's company and dates, Orbitly's title)
SPAN_QUOTE = ("Calnex Solutions AI Solutions Engineer, R&D Jun 2025 – Sep 2025 Stevenage Built agent evals for test "
              "equipment, UK. Orbitly (orbitly.uk) Founder & AI-Native Engineer")
SPAN_ATTACKS = ("Calnex Solutions - Founder & AI-Native Engineer, Jun 2025 - Sep 2025, Stevenage",
                "Calnex Solutions | Founder Jun 2025 - Sep 2025")


def _byp_profile() -> UserProfile:
    return UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text=BYP_RAW, cv_positions=BYP_POSITIONS))


def test_a_record_holds_only_header_facts_never_bullet_clauses():
    records = cv_review.profile_records(_byp_profile())
    for line in CLAUSE_ATTACKS:
        assert not cv_review.record_line_holds(line, records), line
    for line in (CURRENT, "Calnex Solutions | AI Solutions Engineer, R&D | Jun 2025 - Sep 2025 | Stevenage",
                 "Northwind Analytics - Data Engineer, Jan 2019 - May 2025, London",
                 "Calnex Solutions - AI Solutions Engineer, R&D, Jun 2025 - Sep 2025, Stevenage"):  # the raw header
        assert cv_review.record_line_holds(line, records), line


def test_a_quote_lies_inside_one_field_or_one_line():
    profile = _byp_profile()
    source, words = cv_review.quote_sources(profile)
    assert not cv_review.quote_in_profile(SPAN_QUOTE, source, words)
    assert not cv_review.quote_in_profile("Stevenage Built agent evals", source, words)  # two fields
    assert not cv_review.quote_in_profile("Calnex Solutions \x00 Founder", source, words)  # the separator is no text
    for one in ("Led a team of 5, mentoring juniors.", "Founder & AI-Native Engineer", "Built agent evals for test",
                "Calnex Solutions | AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage"):
        assert cv_review.quote_in_profile(one, source, words), one


@pytest.mark.asyncio
async def test_both_bypasses_are_stored_weak(authenticated_async_context, fixture_user_id):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id, _byp_profile())
        base = f"Ada Lovelace\nEXPERIENCE\n{CURRENT}\n"
        cur = {"cv_line": CURRENT, "profile_quote": "Founder & AI-Native Engineer", "field_path": "x"}
        for line in CLAUSE_ATTACKS:
            got = await _verdict(client, base + line + "\n", [
                cur, {"cv_line": line, "profile_quote": "Northwind Analytics", "field_path": "x"}])
            assert got[0] == "weak" and set(got[1]) - {"cv_checks_found"}, (line, got)
        for line in SPAN_ATTACKS:
            got = await _verdict(client, base + line + "\n", [
                cur, {"cv_line": line, "profile_quote": SPAN_QUOTE, "field_path": "x"}])
            assert got[0] == "weak" and "quote_not_in_profile" in got[1], (line, got)
        # the true current-role header is still strong with no quote that holds it
        assert await _verdict(client, base, [{**cur, "profile_quote": "anything at all"}]) == ("strong", [])


# ── Wrapped raw bullets (coordinator decision, 2026-11-10, after three join bypasses): NO joining ──────────
# A quote lies in ONE field value or ONE physical raw line. A length or case heuristic cannot tell a PDF wrap from a
# new bullet (checker: a 76-char Calnex bullet + a lowercase Northwind bullet stored strong), so a sentence the PDF
# wrapped is quoted from the STRUCTURED bullet (cv_positions / cv_projects), which is one whole string.

WRAP_RAW = (
    "Ada Lovelace\nEXPERIENCE\nNorthwind Labs | AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage\n"
    "• Engineered a RAG pipeline with Bedrock, improving relevance by 40%; added Redis caching\n"
    "that cut response time from 3.2s to 0.8s.\n"
    "• Added a Critic layer that caught 5 of 6 risky replies sent without\n"
    "approval; 148 tests, CI and tracing.\n"
    "• Shipped the billing service.\nusing Stripe webhooks in 2024.\n"
    "• Worked at Tidewater as AI/ML Engineer Intern, from\noct 2024 - jan 2025, remote\n"
    "wrote parsers for Orbitly\nled team of 12 at Northwind\n"
    "• Wrote parsers for the Calnex test equipment evaluation platform and tooling\nled team of 12 at Orbitly\n"
)
RAG = ("Engineered a RAG pipeline with Bedrock, improving relevance by 40%; added Redis caching that cut response time "
       "from 3.2s to 0.8s.")
CRITIC = "A Critic layer caught 5 of 6 risky replies sent without approval; 148 tests, CI and tracing."
JOIN_ATTACKS = (
    RAG,  # a true sentence, but only as two raw lines: quote the structured bullet instead
    "Added a Critic layer that caught 5 of 6 risky replies sent without approval; 148 tests, CI and tracing.",
    "Shipped the billing service. using Stripe webhooks in 2024.",
    "wrote parsers for Orbitly led team of 12",  # checker on 263dd39be
    "Wrote parsers for the Calnex test equipment evaluation platform and tooling led team of 12",  # on 1fbff6b3d
    "Wrote parsers for the Calnex test equipment evaluation platform and tooling led team",
    "approval; 148 tests, CI and tracing. Shipped the billing service.",
)


def _wrap_profile() -> UserProfile:
    return UserProfile(cv_data=CVData(
        name="Ada Lovelace", raw_text=WRAP_RAW,
        cv_positions=[{"company": "Northwind Labs", "title": "AI Solutions Engineer, R&D", "dates": "Jun 2025 – Sep 2025",
                       "location": "Stevenage", "bullets": [RAG]}],
        cv_projects=[{"name": "Support Agent", "description": f"Support agent that drafts replies. {CRITIC}",
                      "technologies": ["LangGraph", "MCP"]}]))


def test_a_quote_never_joins_two_raw_lines():
    source, words = cv_review.quote_sources(UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text=WRAP_RAW)))
    for quote in JOIN_ATTACKS:
        assert not cv_review.quote_in_profile(quote, source, words), quote
    for one in ("approval; 148 tests, CI and tracing.", "led team of 12 at Northwind",
                "Wrote parsers for the Calnex test equipment evaluation platform and tooling"):  # one physical line
        assert cv_review.quote_in_profile(one, source, words), one


def test_each_structured_bullet_and_description_is_quotable_whole():
    source, words = cv_review.quote_sources(_wrap_profile())
    for whole in (RAG, f"Support agent that drafts replies. {CRITIC}", CRITIC):
        assert cv_review.quote_in_profile(whole, source, words), whole
    assert not cv_review.quote_in_profile(f"{RAG} Support agent that drafts replies.", source, words)  # two fields


def test_a_raw_line_pair_is_never_a_header_record():
    records = cv_review.profile_records(_wrap_profile())
    assert not cv_review.record_line_holds(
        "Worked at Tidewater as AI/ML Engineer Intern, from oct 2024 - jan 2025, remote", records)
    assert not cv_review.record_line_holds("Tidewater | AI/ML Engineer Intern | oct 2024 - jan 2025 | remote", records)


@pytest.mark.asyncio
async def test_joined_raw_lines_are_weak_and_structured_bullets_are_strong(
    authenticated_async_context, fixture_user_id,
):
    async with authenticated_async_context() as client:
        _seed(fixture_user_id, _wrap_profile())
        header = "Northwind Labs | AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage"
        head = {"cv_line": header, "profile_quote": header, "field_path": "cv_data.raw_text"}
        text = f"Ada Lovelace\nEXPERIENCE\n{header}\n- {RAG}\n- {CRITIC}\n"
        rows = [head, {"cv_line": f"- {RAG}", "profile_quote": RAG, "field_path": "cv_data.cv_positions[0].bullets[0]"},
                {"cv_line": f"- {CRITIC}", "profile_quote": CRITIC, "field_path": "cv_data.cv_projects[0].description"}]
        assert await _verdict(client, text, rows) == ("strong", [])
        for line in JOIN_ATTACKS[1:]:
            cv = f"Ada Lovelace\nEXPERIENCE\n{header}\n- {line}\n"
            got = await _verdict(client, cv, [head, {"cv_line": f"- {line}", "profile_quote": line,
                                                     "field_path": "cv_data.raw_text"}])
            assert got[0] == "weak" and "quote_not_in_profile" in got[1], (line, got)


def test_quote_sources_is_linear():
    import time

    raw = "lowercase line of a very long text that goes on and on, again and again\n" * 20000
    start = time.process_time()
    source, _ = cv_review.quote_sources(UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text=raw)))
    assert time.process_time() - start < 2.0 and source.count(cv_review._SEGMENT) == 19999  # one segment per line


def test_a_sentence_in_a_record_field_gives_no_clause_pieces():
    """Checker on 263dd39be: a title that is a sentence was cut into clauses and recombined."""
    zed = {"company": "Zed Corp", "title": "Led a team of 12 engineers, saving 5 hours a week",
           "dates": "Jan 2022 – Jan 2023", "location": "Leeds"}
    long = {"company": "Yew Labs", "title": "Engineer " + "who automated weekly reporting for the finance team " * 3,
            "dates": "Jan 2020 – Jan 2021"}
    records = cv_review.profile_records(UserProfile(cv_data=CVData(name="Ada Lovelace", cv_positions=[zed, long])))
    for line in ("Zed Corp, saving 5 hours a week, Jan 2022 - Jan 2023",
                 "Zed Corp, Led a team of 12 engineers, Jan 2022 - Jan 2023",
                 "Zed Corp | saving 5 hours a week | Leeds",
                 "Yew Labs | Engineer | Jan 2020 - Jan 2021",  # a value over the cap is no piece at all
                 "Yew Labs, who automated weekly reporting for the finance team, Jan 2020 - Jan 2021"):
        assert not cv_review.record_line_holds(line, records), line
    # the whole value, verbatim and in order, is still the record's own title
    assert cv_review.record_line_holds(
        "Zed Corp | Led a team of 12 engineers, saving 5 hours a week | Jan 2022 - Jan 2023", records)
    # the owner's real header fields still split: "R&D" alone, "Stevenage, UK"
    records = cv_review.profile_records(_profile())
    assert cv_review.record_line_holds("Stevenage, UK | Northwind Labs | R&D Department", records)
    assert cv_review.record_line_holds(
        "Northwind Labs - AI Solutions Engineer, R&D Department, Jun 2025 - Sep 2025, Stevenage, UK", records)
