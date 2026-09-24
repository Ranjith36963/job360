"""Tests for LinkedIn PDF parser and GitHub profile enricher.

LinkedIn input format is a profile PDF (LinkedIn's "Save to PDF" export),
not a ZIP of CSVs.

Decision 28 (2026-09-21) removed the LLM pass from both parsers: Job360 has
no model of its own any more, so what these files test changed too.

  * LinkedIn: the parser is now ONE deterministic layer (pdfplumber + heading
    split). It reads the structural fields it can prove — the "Top Skills"
    sidebar plus inline "Technologies: ..." lines, summary, industry,
    headline, and the Contact block — and keeps the full extracted text on
    ``raw_text``. The prose sections (positions, education, certifications,
    honors, ...) come back EMPTY from a parse; the user's own agent reads
    ``raw_text`` and writes those sections back with ``update_profile``. So
    every test that used to mock an LLM to fabricate positions/education/
    certifications is gone — there is nothing left to mock, and asserting an
    LLM-shaped value would be asserting a fact the parser can no longer
    produce. What remains here proves the deterministic extraction (section
    split, header fields, skills, inline tech lines, the Contact block) and
    the ``enrich_cv_from_linkedin`` merge contract (fill-if-present, never
    clears a shelf).
  * GitHub: ``fetch_github_profile`` and ``_infer_skills`` were already
    deterministic (rule #28 — no hardcoded language/topic->skill map) and
    are unchanged by decision 28; their tests are unchanged too.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.profile.linkedin_parser import (
    _dewrap_columns,
    _empty_linkedin_data,
    _extract_header_fields,
    _extract_skills,
    _looks_like_linkedin,
    _split_sections,
    enrich_cv_from_linkedin,
    is_linkedin_pdf,
    parse_linkedin_pdf,
    parse_linkedin_pdf_async,
)
from src.services.profile.models import CVData

# ---------------------------------------------------------------------------
# Two-column de-interleaving (LinkedIn "Save to PDF" sidebar fix)
# ---------------------------------------------------------------------------

class TestDewrapColumns:
    def test_deinterleaves_two_columns(self):
        """A clear left sidebar + right main column → left text fully precedes
        right text, so 'Top Skills' content stays under its heading."""
        words = []
        for i, t in enumerate(
            ["Top", "Skills", "LangGraph", "Systems", "Design", "Multi-agent", "Certifications"]
        ):
            words.append({"text": t, "x0": 50, "x1": 140, "top": 100 + i * 20})
        for i, t in enumerate(["Summary", "I", "build", "AI", "systems", "that", "scale"]):
            words.append({"text": t, "x0": 320, "x1": 430, "top": 100 + i * 20})
        out = _dewrap_columns(words, 600)
        assert out is not None
        assert out.index("LangGraph") < out.index("Summary")

    def test_single_column_returns_none(self):
        """Dense single-column text has no clear vertical gutter → None (caller
        falls back to flat extraction, so normal CVs are unaffected)."""
        words = []
        for r in range(8):
            for x0 in (50, 160, 270, 380, 490):
                words.append({"text": "w", "x0": x0, "x1": x0 + 95, "top": 100 + r * 20})
        assert _dewrap_columns(words, 600) is None

    def test_empty_words_returns_none(self):
        assert _dewrap_columns([], 600) is None


class TestInlineTechSkills:
    def test_extracts_technologies_lines_with_wrap(self):
        """LinkedIn experience 'Technologies: ...' lines (incl. the wrapped
        continuation) are extracted deterministically."""
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        text = (
            "Some achievement prose here.\n"
            "Technologies: Docker • AWS Bedrock • Redis • Linux\n"
            "• Python • RAG Pipelines • Generative AI\n"
            "Next Company\n"
        )
        sk = set(_extract_inline_tech_skills(text))
        assert {"Docker", "AWS Bedrock", "Redis", "Linux", "Python",
                "RAG Pipelines", "Generative AI"}.issubset(sk)
        assert "Some achievement prose here." not in sk
        assert "Next Company" not in sk

    def test_no_tech_line_returns_empty(self):
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        assert _extract_inline_tech_skills("Just prose.\nNo tech line.\n") == []

    def test_extracts_continuously_learning_line(self):
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        text = "Continuously learning: Prompt engineering • Vector databases • RLHF • AI evaluation frameworks\n"
        sk = set(_extract_inline_tech_skills(text))
        assert {"Prompt engineering", "Vector databases", "RLHF", "AI evaluation frameworks"}.issubset(sk)

    def test_mid_item_wrap_heals_vector_databases(self):
        """The PDF wraps the list INSIDE an item ("... • Vector" / "Databases
        • Python"). The owner's stored skills held a bare "Vector" from this."""
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        text = (
            "Technologies: Docker • AWS Bedrock • Vector\n"
            "Databases • Python\n"
            "Next Company\n"
        )
        sk = _extract_inline_tech_skills(text)
        assert sk == ["Docker", "AWS Bedrock", "Vector Databases", "Python"]
        assert "Vector" not in sk
        assert "Databases" not in sk

    def test_mid_item_wrap_heals_data_preprocessing(self):
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        text = (
            "Technologies: Python • Pandas • Data\n"
            "Preprocessing • Scikit-learn\n"
        )
        sk = _extract_inline_tech_skills(text)
        assert sk == ["Python", "Pandas", "Data Preprocessing", "Scikit-learn"]
        assert "Data" not in sk
        assert "Preprocessing" not in sk

    def test_unwrapped_list_unchanged(self):
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        text = "Technologies: Docker • AWS Bedrock • Redis\nLed a team of five.\n"
        assert _extract_inline_tech_skills(text) == ["Docker", "AWS Bedrock", "Redis"]

    def test_heading_on_next_line_not_joined(self):
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        text = "Technologies: Docker • Vector\nEducation\nMSc • Computer Science\n"
        assert _extract_inline_tech_skills(text) == ["Docker", "Vector"]

    # The owner's REAL export (prod read 2026-09-24): the two Technologies runs
    # are copied verbatim, including where the PDF cut them. Both cut items are
    # the LAST item, so no bullet follows — only the line width can tell. The
    # prose around them is neutral filler of the same widths (the real export's
    # line widths were p95 75 / max 80); no personal or contact data.
    _OWNER_DOC = (
        "- Designed a retrieval pipeline that answered internal support questions\n"
        "  for the platform team, cutting the time spent on repeated tickets by\n"
        "  a clear margin and freeing engineers for product work across quarters\n"
        "- Built an evaluation harness that scored every model change against a\n"
        "  fixed question set before release, so regressions were caught early\n"
        "capabilities into product roadmap, accelerating feature development cycle by\n"
        "25%\n"
        "Technologies: Docker • AWS Bedrock • AWS S3 • Chroma DB • Redis • Linux\n"
        "• Python • RAG Pipelines • Generative AI • Multi-Agent Systems • Vector\n"
        "Databases\n"
        "Second Company\n"
        "AI / ML intern\n"
        "October 2024 - January 2025 (4 months)\n"
        "- Trained a video classification model on a labelled clip set and wrote\n"
        "  the data loaders, augmentation steps and the evaluation reporting code\n"
        "- Implemented LangChain framework for orchestrating complex AI workflows\n"
        "and prompt engineering optimization\n"
        "Technologies: Python • TensorFlow • PyTorch • LangChain • OpenAI API •\n"
        "Gemini API • Large Language Models • NLP • Video Understanding • Data\n"
        "Preprocessing\n"
        "Education\n"
        "Some University\n"
    )

    def test_owner_real_export_heals_last_item_wraps(self):
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        sk = _extract_inline_tech_skills(self._OWNER_DOC)
        assert "Vector Databases" in sk
        assert "Data Preprocessing" in sk
        for stub in ("Vector", "Databases", "Data", "Preprocessing"):
            assert stub not in sk
        # The line after the healed fragment is never pulled in.
        assert not any("Second Company" in s or "Education" in s for s in sk)
        assert sk[-1] == "Data Preprocessing"
        assert sk[:11] == [
            "Docker", "AWS Bedrock", "AWS S3", "Chroma DB", "Redis", "Linux",
            "Python", "RAG Pipelines", "Generative AI", "Multi-Agent Systems",
            "Vector Databases",
        ]

    def test_owner_export_through_deterministic_fields(self):
        """Same text through the public entry point the upload path uses."""
        from src.services.profile.linkedin_parser import deterministic_linkedin_fields

        # Headings + page footer so the text is recognised as a LinkedIn export.
        doc = (
            "Top Skills\nMachine Learning\nSummary\nEngineer.\nExperience\n"
            + self._OWNER_DOC
            + "Page 1 of 2\n"
        )
        skills = deterministic_linkedin_fields(doc)["skills"]
        assert "Vector Databases" in skills and "Data Preprocessing" in skills
        assert "Vector" not in skills and "Data" not in skills

    def test_width_rule_off_when_ratio_unreachable(self, monkeypatch):
        """The ratio is a live setting: above 1.0 no line is 'full width', so
        the last-item rule never fires (stubs come back, proving it's the rule)."""
        from src.core import settings
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        monkeypatch.setattr(settings, "LINKEDIN_WRAP_WIDTH_RATIO", 1.5)
        sk = _extract_inline_tech_skills(self._OWNER_DOC)
        assert "Vector" in sk and "Vector Databases" not in sk

    def test_short_tech_line_not_joined_in_full_document(self):
        """A tech line well short of the page width was NOT cut: the next
        line (a company name) stays out, even in a full-width document."""
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        doc = self._OWNER_DOC.replace(
            "Technologies: Docker • AWS Bedrock • AWS S3 • Chroma DB • Redis • Linux\n"
            "• Python • RAG Pipelines • Generative AI • Multi-Agent Systems • Vector\n"
            "Databases\n",
            "Technologies: Docker • Redis • Linux\n",
        )
        sk = _extract_inline_tech_skills(doc)
        assert "Linux" in sk
        assert not any("Second Company" in s for s in sk)

    def test_full_width_line_then_prose_not_joined(self):
        """A full-width tech line followed by a full-width prose line: the
        prose is not a short fragment, so it is not joined."""
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        doc = self._OWNER_DOC.replace(
            "Databases\n",
            "Databases were migrated to a managed service with nightly snapshot jobs\n",
        )
        sk = _extract_inline_tech_skills(doc)
        assert "Vector" in sk
        assert not any("migrated" in s for s in sk)

    def test_full_width_line_then_heading_not_joined(self):
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        doc = self._OWNER_DOC.replace("Preprocessing\nEducation\n", "Education\n")
        sk = _extract_inline_tech_skills(doc)
        assert "Data" in sk
        assert "Data Education" not in sk

    def test_blank_line_not_joined(self):
        from src.services.profile.linkedin_parser import _extract_inline_tech_skills

        text = "Technologies: Docker • Vector\n\nDatabases • Python\n"
        sk = _extract_inline_tech_skills(text)
        assert sk == ["Docker", "Vector"]
        assert "Vector Databases" not in sk
from src.services.profile.github_enricher import (
    _infer_skills,
    enrich_cv_from_github,
    fetch_github_profile,
)

# ---------------------------------------------------------------------------
# Helpers — build LinkedIn-shaped PDFs in memory
# ---------------------------------------------------------------------------

def _make_linkedin_pdf(
    tmp_path: Path,
    name: str = "John Doe",
    headline: str = "ML Engineer, Technology",
    location: str = "London, United Kingdom",
    url: str = "linkedin.com/in/johndoe",
    summary: str = "Experienced ML engineer",
    experience: list[str] | None = None,
    education: list[str] | None = None,
    skills: list[str] | None = None,
    certifications: list[str] | None = None,
    include_footer: bool = True,
    filename: str = "linkedin.pdf",
) -> Path:
    """Render a PDF that mimics LinkedIn's 'Save to PDF' layout."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)

    def _line(text: str) -> None:
        pdf.cell(0, 6, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    if include_footer:
        _line(url)
    _line(name)
    _line(headline)
    _line(location)
    _line("")

    if summary:
        _line("Summary")
        for para_line in summary.splitlines():
            _line(para_line)
        _line("")

    if experience:
        _line("Experience")
        for exp_line in experience:
            _line(exp_line)
        _line("")

    if education:
        _line("Education")
        for edu_line in education:
            _line(edu_line)
        _line("")

    if skills:
        _line("Skills")
        for skill in skills:
            _line(skill)
        _line("")

    if certifications:
        _line("Certifications")
        for cert in certifications:
            _line(cert)
        _line("")

    if include_footer:
        _line("Page 1 of 1")

    path = tmp_path / filename
    pdf.output(str(path))
    return path


def _make_plain_cv_pdf(tmp_path: Path) -> Path:
    """Render a PDF that does NOT look like LinkedIn (no URL, no footer, no headings)."""
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in [
        "Jane Smith",
        "Senior Software Engineer",
        "",
        "A software engineer with 10 years of experience in distributed systems.",
        "",
        "Google, 2015-2020: Built large-scale infrastructure.",
        "University of Cambridge, BSc Computer Science.",
    ]:
        pdf.cell(0, 6, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    path = tmp_path / "plain_cv.pdf"
    pdf.output(str(path))
    return path


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

class TestLinkedInPdfDetection:
    def test_full_linkedin_pdf_detected(self, tmp_path):
        path = _make_linkedin_pdf(
            tmp_path,
            experience=["Engineer at Google", "Jan 2020 - Dec 2022"],
            education=["MIT", "MSc Computer Science", "2016 - 2018"],
            skills=["Python", "SQL"],
        )
        assert is_linkedin_pdf(str(path)) is True

    def test_plain_cv_not_detected(self, tmp_path):
        path = _make_plain_cv_pdf(tmp_path)
        assert is_linkedin_pdf(str(path)) is False

    def test_empty_text_not_detected(self):
        assert _looks_like_linkedin("") is False

    def test_requires_two_markers(self):
        # Only a URL — not enough.
        assert _looks_like_linkedin("linkedin.com/in/johndoe") is False
        # URL + footer (2 markers, no headings) passes.
        assert _looks_like_linkedin("linkedin.com/in/johndoe\nPage 1 of 2") is True
        # Three headings alone (no URL, no footer) → only 1 marker.
        assert _looks_like_linkedin("Summary\nExperience\nEducation") is False

    def test_corrupt_file_not_detected(self, tmp_path):
        path = tmp_path / "bad.pdf"
        path.write_bytes(b"not a real pdf")
        assert is_linkedin_pdf(str(path)) is False


# ---------------------------------------------------------------------------
# Section split & deterministic extraction
# ---------------------------------------------------------------------------

class TestSectionSplit:
    def test_split_recognises_known_headings(self):
        text = "John Doe\nML Engineer\n\nSummary\nI build models.\n\nExperience\nGoogle 2020-2022\n\nSkills\nPython\nSQL\n"
        sections = _split_sections(text)
        assert "summary" in sections
        assert sections["summary"].strip() == "I build models."
        assert "experience" in sections
        assert "skills" in sections
        assert "Python" in sections["skills"]

    def test_split_preserves_header_block(self):
        text = "John Doe\nSenior Engineer\n\nSummary\nHello"
        sections = _split_sections(text)
        header = sections["header"]
        assert "John Doe" in header
        assert "Summary" not in header  # heading itself isn't in the header block

    def test_split_is_case_insensitive(self):
        text = "SUMMARY\nMy summary.\nexperience\nRole details."
        sections = _split_sections(text)
        assert "summary" in sections and "experience" in sections


class TestDeterministicExtraction:
    def test_header_name_and_headline(self):
        fields = _extract_header_fields(
            "linkedin.com/in/johndoe\nJohn Doe\nML Engineer, Technology\nLondon\nPage 1 of 2"
        )
        assert fields["name"] == "John Doe"
        assert fields["headline"] == "ML Engineer, Technology"
        assert fields["industry"] == "Technology"

    def test_header_without_industry_suffix(self):
        fields = _extract_header_fields("Jane Doe\nEngineer")
        assert fields["name"] == "Jane Doe"
        assert fields["headline"] == "Engineer"
        assert fields["industry"] == ""

    def test_skills_one_per_line(self):
        skills = _extract_skills("Python\nSQL\nDocker\n")
        assert skills == ["Python", "SQL", "Docker"]

    def test_skills_dedup_case_insensitive(self):
        skills = _extract_skills("Python\npython\nSQL\n")
        assert skills == ["Python", "SQL"]

    def test_skills_strip_endorsement_counts(self):
        skills = _extract_skills("Python (24)\nSQL (8)\n")
        assert skills == ["Python", "SQL"]


# ---------------------------------------------------------------------------
# End-to-end parse_linkedin_pdf — deterministic (decision 28: no LLM pass)
# ---------------------------------------------------------------------------

class TestParseLinkedInPdfEndToEnd:
    @pytest.mark.asyncio
    async def test_full_parse(self, tmp_path):
        """A LinkedIn export with every section present still comes back with
        ONLY the structural fields filled. Positions/education/certifications
        are prose sections the parser deliberately leaves alone since decision
        28 — they arrive empty, and the full text is on ``raw_text`` for the
        user's agent to read and write back with ``update_profile``."""
        path = _make_linkedin_pdf(
            tmp_path,
            summary="Experienced ML engineer",
            experience=["Software Engineer at Google", "Jan 2020 - Dec 2022", "Built ML pipelines"],
            education=["MIT", "MSc Computer Science", "2016 - 2018"],
            skills=["Python", "SQL", "Machine Learning", "Docker"],
            certifications=["AWS Solutions Architect - Amazon - 2021"],
        )
        data = await parse_linkedin_pdf_async(str(path))
        assert data["summary"] == "Experienced ML engineer"
        assert data["headline"] == "ML Engineer, Technology"
        assert data["industry"] == "Technology"
        assert data["skills"] == ["Python", "SQL", "Machine Learning", "Docker"]
        # Prose sections: deliberately empty. No LLM pass reads them any more.
        assert data["positions"] == []
        assert data["education"] == []
        assert data["certifications"] == []
        # The full text is kept so the user's agent can read the prose itself.
        assert "raw_text" in data and "Software Engineer at Google" in data["raw_text"]

    def test_sync_wrapper_returns_same_shape(self, tmp_path):
        path = _make_linkedin_pdf(
            tmp_path,
            summary="x",
            experience=["E"], education=["M"],
            skills=["Python"], certifications=["Cert"],
        )
        data = parse_linkedin_pdf(str(path))
        # Asserted against the SCHEMA OF RECORD, not a hand-typed key list.
        #
        # This assertion used to spell out twelve keys. That made it a fourth
        # hand-maintained copy of the LinkedIn schema, alongside
        # _empty_linkedin_data, llm_linkedin_fields and merge_linkedin_fields —
        # and when eight sections were added on 2026-08-09, the copies drifted:
        # the merger silently dropped the new keys, and this test PASSED,
        # because it was asserting the same stale shape the merger produced.
        # A test that pins a hand-typed duplicate of the thing under test can
        # only ever confirm that both copies are wrong in the same way.
        # (``llm_linkedin_fields`` is gone since decision 28, but the lesson —
        # derive the key set from the schema, never retype it — still holds.)
        assert set(data.keys()) == set(_empty_linkedin_data().keys())
        assert data["skills"] == ["Python"]

    @pytest.mark.asyncio
    async def test_skills_only_still_works(self, tmp_path):
        """If only the Skills section exists, the parse still succeeds — there
        is no second pass to wait on or fail."""
        path = _make_linkedin_pdf(
            tmp_path,
            summary="",
            experience=None, education=None,
            skills=["Python", "Rust"],
            certifications=None,
        )
        data = await parse_linkedin_pdf_async(str(path))
        assert data["skills"] == ["Python", "Rust"]
        assert data["positions"] == []
        assert data["education"] == []


# ---------------------------------------------------------------------------
# Two-pass: LinkedIn raw text storage (re-run extraction without re-upload)
# ---------------------------------------------------------------------------

class TestLinkedInRawTextStorage:
    @pytest.mark.asyncio
    async def test_parse_returns_raw_text(self, tmp_path):
        """parse_linkedin_pdf_async exposes the extracted text under 'raw_text'
        so the user's agent can read the prose sections (and a caller can
        re-run structural extraction) without asking for the PDF again."""
        path = _make_linkedin_pdf(
            tmp_path,
            summary="",
            experience=None, education=None,
            skills=["Python", "Rust"], certifications=None,
        )
        data = await parse_linkedin_pdf_async(str(path))
        assert "raw_text" in data
        assert "Python" in data["raw_text"]

    def test_enrich_stores_linkedin_raw_text(self):
        cv = CVData()
        data = {"skills": ["Python"], "positions": [], "raw_text": "RAW LINKEDIN TEXT"}
        out = enrich_cv_from_linkedin(cv, data)
        assert out.linkedin_raw_text == "RAW LINKEDIN TEXT"

    def test_enrich_missing_raw_text_leaves_empty(self):
        """Older callers that don't pass raw_text don't crash; field stays ''."""
        cv = CVData()
        out = enrich_cv_from_linkedin(cv, {"skills": [], "positions": []})
        assert out.linkedin_raw_text == ""


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestLinkedInPdfErrors:
    def test_corrupt_pdf_returns_empty(self, tmp_path):
        path = tmp_path / "bad.pdf"
        path.write_bytes(b"this is not a pdf")
        data = parse_linkedin_pdf(str(path))
        assert data["positions"] == []
        assert data["skills"] == []
        assert data["summary"] == ""

    def test_missing_file_returns_empty(self):
        data = parse_linkedin_pdf("/path/does/not/exist.pdf")
        assert data["positions"] == []
        assert data["skills"] == []

    def test_non_linkedin_pdf_returns_empty(self, tmp_path):
        path = _make_plain_cv_pdf(tmp_path)
        data = parse_linkedin_pdf(str(path))
        assert data["positions"] == []
        assert data["skills"] == []
        assert data["summary"] == ""


# ---------------------------------------------------------------------------
# Enrichment — contract with downstream is unchanged
# ---------------------------------------------------------------------------

class TestEnrichCVFromLinkedIn:
    def test_merges_skills(self):
        cv = CVData(skills=["Python", "Java"])
        linkedin_data = {"skills": ["Python", "SQL", "Docker"], "positions": [], "education": [], "certifications": []}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert "SQL" in cv.linkedin_skills
        assert "Docker" in cv.linkedin_skills

    def test_merges_job_titles(self):
        cv = CVData(job_titles=["Software Engineer"])
        linkedin_data = {
            "skills": [],
            "positions": [
                {"title": "Software Engineer", "company": "Google"},
                {"title": "Senior Engineer", "company": "Meta"},
            ],
            "education": [], "certifications": [],
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert "Senior Engineer" in cv.job_titles
        assert cv.job_titles.count("Software Engineer") == 1

    def test_merges_education(self):
        cv = CVData()
        linkedin_data = {
            "skills": [], "positions": [],
            "education": [{"school": "MIT", "degree": "MSc CS"}],
            "certifications": [],
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert any("MIT" in e for e in cv.education)

    def test_merges_certifications(self):
        cv = CVData()
        linkedin_data = {
            "skills": [], "positions": [], "education": [],
            "certifications": [{"name": "AWS SA"}],
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert "AWS SA" in cv.certifications

    def test_fills_empty_summary(self):
        cv = CVData()
        linkedin_data = {"skills": [], "positions": [], "education": [], "certifications": [], "summary": "I am an engineer"}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert cv.summary == "I am an engineer"

    def test_does_not_overwrite_existing_summary(self):
        cv = CVData(summary="My existing summary")
        linkedin_data = {"skills": [], "positions": [], "education": [], "certifications": [], "summary": "LinkedIn summary"}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert cv.summary == "My existing summary"

    def test_stores_industry(self):
        cv = CVData()
        linkedin_data = {"skills": [], "positions": [], "education": [], "certifications": [], "industry": "Technology"}
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert cv.linkedin_industry == "Technology"

    def test_double_enrich_no_dupes(self):
        cv = CVData(skills=["Python"])
        linkedin_data = {
            "skills": ["SQL", "Docker"],
            "positions": [{"title": "Engineer", "company": "Google"}],
            "education": [], "certifications": [],
            "industry": "Tech",
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert len(cv.linkedin_skills) == 2
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        assert len(cv.linkedin_skills) == 2


# ---------------------------------------------------------------------------
# GitHub Enricher — unchanged
# ---------------------------------------------------------------------------

class TestInferSkills:
    # Rule #28: _infer_skills returns the RAW GitHub language/topic strings
    # (no hardcoded mapping). Languages keep their casing; topics get a cosmetic
    # hyphen->space cleanup. The user's own agent canonicalises meaning downstream
    # (decision 28 — Job360 has no model of its own).
    def test_languages_raw_ranked_by_bytes(self):
        languages = {"Python": 50000, "JavaScript": 30000, "HCL": 10000}
        skills = _infer_skills(languages, set())
        assert skills[0] == "Python"           # most bytes first
        assert "JavaScript" in skills
        assert "HCL" in skills                 # raw, not mapped to "Terraform"

    def test_topics_raw_with_hyphen_cleanup(self):
        topics = {"react", "docker", "machine-learning"}
        skills = _infer_skills({}, topics)
        assert "react" in skills
        assert "docker" in skills
        assert "machine learning" in skills    # hyphen -> space, no mapping

    def test_deduplicates_across_lang_and_topic(self):
        # "docker" appears as both a language-ish entry and a topic → once only.
        skills = _infer_skills({"Python": 50000, "docker": 5000}, {"docker"})
        assert sum(1 for s in skills if s.lower() == "docker") == 1

    def test_empty_inputs(self):
        assert _infer_skills({}, set()) == []

    def test_unknown_language_kept_raw(self):
        # No allowlist any more — every signal the API returns is surfaced.
        assert _infer_skills({"COBOL": 1000}, set()) == ["COBOL"]

    def test_unknown_topic_kept_raw(self):
        assert _infer_skills({}, {"some-random-topic"}) == ["some random topic"]


class TestFetchGitHubProfile:
    @pytest.mark.asyncio
    async def test_fetch_repos_and_languages(self):
        mock_repos = [
            {"name": "ml-project", "language": "Python", "description": "ML pipeline",
             "stargazers_count": 10, "topics": ["machine-learning", "pytorch"], "fork": False},
            {"name": "web-app", "language": "TypeScript", "description": "React app",
             "stargazers_count": 5, "topics": ["react", "nextjs"], "fork": False},
        ]
        mock_languages_ml = {"Python": 50000, "Jupyter Notebook": 10000}
        mock_languages_web = {"TypeScript": 30000, "CSS": 5000}

        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 200
            if "repos?per_page" in url:
                resp.json = AsyncMock(return_value=mock_repos)
            elif "ml-project/languages" in url:
                resp.json = AsyncMock(return_value=mock_languages_ml)
            elif "web-app/languages" in url:
                resp.json = AsyncMock(return_value=mock_languages_web)
            else:
                resp.json = AsyncMock(return_value={})
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))
        session.post = MagicMock(side_effect=lambda *a, **kw: _async_context(_pinned_404()))

        result = await fetch_github_profile("testuser", session=session)
        assert len(result["repositories"]) == 2
        assert result["languages"]["Python"] == 50000
        assert "machine-learning" in result["topics"]
        assert "Python" in result["skills_inferred"]

    @pytest.mark.asyncio
    async def test_skips_forks(self):
        mock_repos = [
            {"name": "forked-repo", "language": "Python", "description": "", "stargazers_count": 0, "topics": [], "fork": True},
            {"name": "own-repo", "language": "Go", "description": "", "stargazers_count": 1, "topics": [], "fork": False},
        ]

        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 200
            if "repos?per_page" in url:
                resp.json = AsyncMock(return_value=mock_repos)
            else:
                resp.json = AsyncMock(return_value={"Go": 20000})
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))
        session.post = MagicMock(side_effect=lambda *a, **kw: _async_context(_pinned_404()))

        result = await fetch_github_profile("testuser", session=session)
        assert len(result["repositories"]) == 1
        assert result["repositories"][0]["name"] == "own-repo"

    @pytest.mark.asyncio
    async def test_handles_api_error(self):
        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 404
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))
        session.post = MagicMock(side_effect=lambda *a, **kw: _async_context(_pinned_404()))

        result = await fetch_github_profile("nonexistent", session=session)
        assert result["repositories"] == []
        assert result["skills_inferred"] == []

    @pytest.mark.asyncio
    async def test_handles_rate_limit(self):
        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 403
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))
        session.post = MagicMock(side_effect=lambda *a, **kw: _async_context(_pinned_404()))

        result = await fetch_github_profile("testuser", session=session)
        assert result["repositories"] == []


class TestEnrichCVFromGitHub:
    def test_merges_skills(self):
        cv = CVData(skills=["Python", "Java"])
        github_data = {
            "skills_inferred": ["Python", "TypeScript", "Docker"],
            "languages": {"Python": 50000, "TypeScript": 30000},
            "topics": ["docker"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert "TypeScript" in cv.github_skills_inferred
        assert "Docker" in cv.github_skills_inferred
        assert "Python" not in cv.github_skills_inferred

    def test_stores_languages_and_topics(self):
        cv = CVData()
        github_data = {
            "skills_inferred": ["Go"],
            "languages": {"Go": 20000},
            "topics": ["microservices"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert cv.github_languages == {"Go": 20000}
        assert "microservices" in cv.github_topics


# ---------------------------------------------------------------------------
# Storage with new fields
# ---------------------------------------------------------------------------

# TestStorageWithNewFields was deleted in Batch 3.5.2 — storage moved
# from data/user_profile.json to the user_profiles DB table, so the
# PROFILE_PATH monkey-patch it used no longer exists. Equivalent
# per-user round-trip + schema-drift + unknown-key coverage lives in
# tests/test_profile_storage.py (Batch 3.5.2). See
# docs/product/plans/batch-3.5.2-plan.md Deliverable B for the migration
# rationale.


# ---------------------------------------------------------------------------
# GitHub error handling + combined enrichment
# ---------------------------------------------------------------------------

class TestGitHubErrors:
    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        async def mock_get(url, **kwargs):
            raise asyncio.TimeoutError("Timed out")
        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))
        session.post = MagicMock(side_effect=lambda *a, **kw: _async_context(_pinned_404()))
        result = await fetch_github_profile("testuser", session=session)
        assert result["repositories"] == []
        assert result["skills_inferred"] == []

    @pytest.mark.asyncio
    async def test_partial_language_fetch_failure(self):
        mock_repos = [
            {"name": "repo1", "language": "Python", "description": "", "stargazers_count": 1, "topics": [], "fork": False},
            {"name": "repo2", "language": "Go", "description": "", "stargazers_count": 1, "topics": [], "fork": False},
        ]

        async def mock_get(url, **kwargs):
            resp = AsyncMock()
            resp.status = 200
            if "repos?per_page" in url:
                resp.json = AsyncMock(return_value=mock_repos)
            elif "repo1/languages" in url:
                resp.json = AsyncMock(return_value={"Python": 50000})
            elif "repo2/languages" in url:
                resp.status = 500
                resp.json = AsyncMock(return_value=None)
                return resp
            return resp

        session = AsyncMock()
        session.get = MagicMock(side_effect=lambda url, **kw: _async_context(mock_get(url, **kw)))
        session.post = MagicMock(side_effect=lambda *a, **kw: _async_context(_pinned_404()))
        result = await fetch_github_profile("testuser", session=session)
        assert len(result["repositories"]) == 2
        assert "Python" in result["skills_inferred"]

    def test_double_enrich_github_no_dupes(self):
        cv = CVData(skills=["Python"])
        github_data = {
            "skills_inferred": ["TypeScript", "Docker"],
            "languages": {"TypeScript": 30000},
            "topics": ["docker"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert len(cv.github_skills_inferred) == 2
        cv = enrich_cv_from_github(cv, github_data)
        assert len(cv.github_skills_inferred) == 2


class TestCombinedEnrichment:
    def test_linkedin_then_github_no_data_loss(self):
        cv = CVData(skills=["Python"])
        linkedin_data = {
            "skills": ["SQL"], "positions": [{"title": "Engineer", "company": "Co"}],
            "education": [{"school": "MIT", "degree": "MSc"}],
            "certifications": [{"name": "AWS"}],
            "industry": "Tech", "summary": "Hi",
        }
        cv = enrich_cv_from_linkedin(cv, linkedin_data)
        github_data = {
            "skills_inferred": ["TypeScript", "Docker"],
            "languages": {"TypeScript": 30000},
            "topics": ["docker"],
        }
        cv = enrich_cv_from_github(cv, github_data)
        assert cv.linkedin_skills == ["SQL"]
        assert cv.linkedin_industry == "Tech"
        assert "TypeScript" in cv.github_skills_inferred
        assert "Docker" in cv.github_skills_inferred
        assert "Engineer" in cv.job_titles

# ---------------------------------------------------------------------------
# Async context manager helper for mocking aiohttp
# ---------------------------------------------------------------------------

class _async_context:
    """Wrap a coroutine as an async context manager for aiohttp mocking."""
    def __init__(self, coro):
        self._coro = coro

    async def __aenter__(self):
        return await self._coro

    async def __aexit__(self, *args):
        pass


async def _pinned_404():
    """A 404 response for the GraphQL pinned-repos POST, so ``_fetch_pinned``
    no-ops cleanly on the mock session (no dangling coroutine warning)."""
    resp = AsyncMock()
    resp.status = 404
    return resp
