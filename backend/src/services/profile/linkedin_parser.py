"""Parse a LinkedIn 'Save to PDF' profile export to structured career data.

Replaces the older LinkedIn Data Export (ZIP of CSVs) flow. Produces the
exact same output dict schema so downstream code (``enrich_cv_from_linkedin``,
``keyword_generator.generate_search_config``) is unchanged.

Strategy (two-layer):
  1. Deterministic pdfplumber text extraction + heading-based section split.
     Covers ``headline``, ``summary``, ``skills``, ``industry``.
  2. LLM extraction for prose-heavy sections (``Experience``, ``Education``,
     ``Certifications``) where dates and bullets need structured parsing.

All failure modes return the empty-data dict (never raises).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from src.services.profile._llm_utils import coerce_str, coerce_str_list
from src.services.profile.models import CVData

logger = logging.getLogger("job360.profile.linkedin")


# ── Section vocabulary ───────────────────────────────────────────

# Exact-match (case-insensitive) standalone heading lines that LinkedIn's
# "Save to PDF" uses. Order is not significant for split, but present here
# so detection and split share one source of truth.
_SECTION_HEADINGS = (
    "Contact",
    "Summary",
    "Experience",
    "Education",
    "Skills",
    "Top Skills",
    "Certifications",
    "Licenses & Certifications",
    "Languages",
    "Honors-Awards",
    "Honors & Awards",
    "Publications",
    "Volunteer Experience",
    "Projects",
    "Recommendations",
    "Interests",
    "Courses",
    "Organizations",
    "Patents",
    "Test Scores",
)

# Case-insensitive lookup.
_HEADING_SET = {h.lower() for h in _SECTION_HEADINGS}

_LINKEDIN_URL_RE = re.compile(r"linkedin\.com/in/[\w\-]+", re.IGNORECASE)
_PAGE_FOOTER_RE = re.compile(r"Page\s+\d+\s+of\s+\d+", re.IGNORECASE)


# ── Text extraction (thin wrapper over pdfplumber) ────────────────

def _extract_text(file_path: str) -> str:
    """Read all pages of a PDF into one newline-joined string. Empty on failure."""
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed. Run: pip install pdfplumber")
        return ""
    try:
        with pdfplumber.open(file_path) as pdf:
            parts = [p.extract_text() or "" for p in pdf.pages]
        return "\n".join(parts)
    except Exception as e:
        logger.warning("Failed to read LinkedIn PDF %s: %s", file_path, e)
        return ""


# ── LinkedIn-PDF detection ────────────────────────────────────────

def is_linkedin_pdf(file_path: str) -> bool:
    """Return True iff the file looks like a LinkedIn 'Save to PDF' export.

    Heuristic: at least 2 of 3 markers present — linkedin.com/in/<slug> URL,
    three or more known section headings, or a 'Page N of M' footer.
    """
    text = _extract_text(file_path)
    return _looks_like_linkedin(text)


def _looks_like_linkedin(text: str) -> bool:
    if not text:
        return False
    markers = 0
    if _LINKEDIN_URL_RE.search(text):
        markers += 1
    heading_hits = 0
    for line in text.splitlines():
        if line.strip().lower() in _HEADING_SET:
            heading_hits += 1
            if heading_hits >= 3:
                break
    if heading_hits >= 3:
        markers += 1
    if _PAGE_FOOTER_RE.search(text):
        markers += 1
    return markers >= 2


# ── Section split ─────────────────────────────────────────────────

def _split_sections(text: str) -> dict[str, str]:
    """Split extracted text into {heading_lower: body}. Pre-heading text lives under 'header'."""
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if _PAGE_FOOTER_RE.search(stripped):
            continue
        key = stripped.lower()
        if stripped and key in _HEADING_SET:
            current = key
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(raw_line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


# ── Deterministic field extraction ────────────────────────────────

def _extract_header_fields(header_text: str) -> dict[str, str]:
    """Pull name and headline from the pre-first-section block.

    Convention: first non-empty line is the name, next non-empty line is
    the headline. Industry is best-effort — the trailing comma-segment of
    the headline if present (e.g. 'ML Engineer, Technology').
    """
    lines = [ln.strip() for ln in header_text.splitlines() if ln.strip()]
    # Drop lines that are clearly footers or URLs from the header region.
    lines = [ln for ln in lines if not _PAGE_FOOTER_RE.search(ln) and not _LINKEDIN_URL_RE.search(ln)]
    name = lines[0] if lines else ""
    headline = lines[1] if len(lines) > 1 else ""
    industry = ""
    if "," in headline:
        industry = headline.rsplit(",", 1)[-1].strip()
    return {"name": name, "headline": headline, "industry": industry}


def _extract_skills(skills_text: str) -> list[str]:
    """LinkedIn lists one skill per line under 'Skills' / 'Top Skills'."""
    seen: set[str] = set()
    out: list[str] = []
    for line in skills_text.splitlines():
        item = line.strip()
        if not item:
            continue
        # Skip endorsement counts like '(12)' that sometimes tag along
        item = re.sub(r"\s*\(\d+\)\s*$", "", item).strip()
        key = item.lower()
        if item and key not in seen:
            out.append(item)
            seen.add(key)
    return out


# ── LLM extraction for prose sections ─────────────────────────────

_LINKEDIN_SYSTEM = (
    "You are an expert LinkedIn profile parser. You read raw text from one "
    "section of a LinkedIn 'Save to PDF' export and return a strictly-typed "
    "JSON object. You do not invent data — if a field is absent in the text, "
    "leave it as an empty string. You return JSON only."
)

_EXPERIENCE_PROMPT = """Extract every position/role from the LinkedIn Experience section text below.
Return JSON: {{"positions": [{{"title": str, "company": str, "start": str, "end": str, "description": str}}, ...]}}

Rules:
- One object per role, in the order written.
- "start"/"end" verbatim as written (e.g. "Jan 2020", "Present"). Empty string if missing.
- "description" = concatenated bullet points / paragraph for that role. Empty string if missing.
- Strip role duration annotations like "(3 yrs 2 mos)".

TEXT:
---
{text}
---"""

_EDUCATION_PROMPT = """Extract every education entry from the LinkedIn Education section text below.
Return JSON: {{"education": [{{"school": str, "degree": str, "start": str, "end": str, "notes": str}}, ...]}}

Rules:
- "school" = institution name. "degree" = qualification (e.g. "MSc Computer Science").
- "start"/"end" verbatim (e.g. "2016", "2018"). Empty if missing.
- "notes" = activities/coursework/dissertation, empty if none.

TEXT:
---
{text}
---"""

_CERTIFICATIONS_PROMPT = """Extract every certification from the LinkedIn certifications section text below.
Return JSON: {{"certifications": [{{"name": str, "authority": str, "start": str, "end": str}}, ...]}}

Rules:
- "name" = certification name. "authority" = issuing body (e.g. "Amazon Web Services").
- "start" = issued date, "end" = expiry/renewal date. Empty if missing.

TEXT:
---
{text}
---"""


# ── Batch 1.5 — expanded LinkedIn sections ────────────────────────

_LANGUAGES_PROMPT = """Extract every human language from the LinkedIn Languages section text below.
Return JSON: {{"languages": [{{"language": str, "proficiency": str}}, ...]}}

Rules:
- "language" = the language name (e.g. "English", "Mandarin Chinese", "Spanish").
- "proficiency" = the proficiency level as written (e.g. "Native or bilingual", "Professional working", "Elementary"). Empty string if missing.

TEXT:
---
{text}
---"""

_PROJECTS_PROMPT = """Extract every portfolio/personal project from the LinkedIn Projects section text below.
Return JSON: {{"projects": [{{"title": str, "description": str, "start": str, "end": str, "url": str}}, ...]}}

Rules:
- "title" = project name.
- "description" = the prose body (bullets concatenated). Empty if none.
- "start"/"end" verbatim as written (e.g. "Mar 2022", "Present"). Empty if missing.
- "url" = associated link if present in the text; empty otherwise.

TEXT:
---
{text}
---"""

_VOLUNTEER_PROMPT = """Extract every volunteer role from the LinkedIn Volunteer Experience section text below.
Return JSON: {{"volunteer": [{{"role": str, "organisation": str, "cause": str, "start": str, "end": str, "description": str}}, ...]}}

Rules:
- "role" = the volunteer position title.
- "organisation" = the organisation/charity name.
- "cause" = the stated cause if present (e.g. "Education", "Environment"). Empty if missing.
- "start"/"end" verbatim. Empty if missing.
- "description" = concatenated bullets/paragraph. Empty if missing.

TEXT:
---
{text}
---"""

_COURSES_PROMPT = """Extract every course from the LinkedIn Courses section text below.
Return JSON: {{"courses": [{{"title": str, "institution": str, "date": str}}, ...]}}

Rules:
- "title" = course name as written.
- "institution" = the awarding body if present (e.g. "Coursera", "MIT OpenCourseWare"). Empty if missing.
- "date" = date/term written. Empty if missing.

TEXT:
---
{text}
---"""


_LINKEDIN_SKILLS_PROMPT = """Below is the raw text of a LinkedIn profile (exported "Save to PDF").
The two-column layout means the "Top Skills" sidebar is often interleaved with
other text, so read the WHOLE thing.

List every concrete professional SKILL the person claims — their "Top Skills",
technologies in their summary/experience, and anything in a "Continuously
learning" or similar line.

Return JSON: {{"skills": ["Skill One", "Skill Two", ...]}}

Rules:
- Only skills the text supports. Do not invent.
- Individual items, not categories.
- Skip bare contact info, company names, and job titles.

LINKEDIN TEXT:
---
{text}
---"""


async def llm_infer_linkedin_skills(raw_text: str) -> list[str]:
    """Two-pass LLM enhance for LinkedIn skills.

    LinkedIn's "Save to PDF" is two-column, so pdfplumber interleaves the
    "Top Skills" sidebar with the main column and the deterministic heading
    split loses it. This reads the FULL raw text with an LLM, recovering the
    Top Skills plus skills mentioned in prose (e.g. "Vector databases • RLHF").

    Returns ``[]`` (never raises) on blank input or provider failure. Blank
    input never calls the LLM (cost guard).
    """
    if not raw_text or not raw_text.strip():
        return []
    try:
        from src.services.profile.llm_provider import llm_extract  # noqa: PLC0415
        result = await llm_extract(
            _LINKEDIN_SKILLS_PROMPT.format(text=raw_text), system=_LINKEDIN_SYSTEM
        )
    except Exception as e:  # noqa: BLE001 — never crash the pass
        logger.warning("LinkedIn LLM skill inference failed: %s", e)
        return []

    raw = result.get("skills") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in raw:
        if isinstance(s, str) and s.strip() and s.strip().lower() not in seen:
            out.append(s.strip())
            seen.add(s.strip().lower())
    return out


async def _llm_json(prompt: str) -> dict[str, Any]:
    """Call the shared LLM provider; return {} on any failure."""
    if not prompt.strip():
        return {}
    try:
        from src.services.profile.llm_provider import llm_extract
        return await llm_extract(prompt, system=_LINKEDIN_SYSTEM)
    except Exception as e:
        logger.warning("LinkedIn LLM extraction failed: %s", e)
        return {}


def _coerce_positions(raw: Any) -> list[dict]:
    """Shape a list-of-dicts LLM result into the canonical positions schema."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = coerce_str(item.get("title"))
        if not title:
            continue
        out.append({
            "title": title.strip(),
            "company": coerce_str(item.get("company")).strip(),
            "start": coerce_str(item.get("start")).strip(),
            "end": coerce_str(item.get("end")).strip(),
            "description": coerce_str(item.get("description")).strip(),
        })
    return out


def _coerce_education(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        school = coerce_str(item.get("school")).strip()
        if not school:
            continue
        out.append({
            "school": school,
            "degree": coerce_str(item.get("degree")).strip(),
            "start": coerce_str(item.get("start")).strip(),
            "end": coerce_str(item.get("end")).strip(),
            "notes": coerce_str(item.get("notes")).strip(),
        })
    return out


def _coerce_certifications(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = coerce_str(item.get("name")).strip()
        if not name:
            continue
        out.append({
            "name": name,
            "authority": coerce_str(item.get("authority")).strip(),
            "start": coerce_str(item.get("start")).strip(),
            "end": coerce_str(item.get("end")).strip(),
        })
    return out


# Batch 1.5 coercers — one per new section ───────────────────────────

def _coerce_languages(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        lang = coerce_str(item.get("language")).strip()
        if not lang:
            continue
        out.append({
            "language": lang,
            "proficiency": coerce_str(item.get("proficiency")).strip(),
        })
    return out


def _coerce_projects(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = coerce_str(item.get("title")).strip()
        if not title:
            continue
        out.append({
            "title": title,
            "description": coerce_str(item.get("description")).strip(),
            "start": coerce_str(item.get("start")).strip(),
            "end": coerce_str(item.get("end")).strip(),
            "url": coerce_str(item.get("url")).strip(),
        })
    return out


def _coerce_volunteer(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = coerce_str(item.get("role")).strip()
        org = coerce_str(item.get("organisation")).strip() or coerce_str(item.get("organization")).strip()
        if not role and not org:
            continue
        out.append({
            "role": role,
            "organisation": org,
            "cause": coerce_str(item.get("cause")).strip(),
            "start": coerce_str(item.get("start")).strip(),
            "end": coerce_str(item.get("end")).strip(),
            "description": coerce_str(item.get("description")).strip(),
        })
    return out


def _coerce_courses(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        title = coerce_str(item.get("title")).strip()
        if not title:
            continue
        out.append({
            "title": title,
            "institution": coerce_str(item.get("institution")).strip(),
            "date": coerce_str(item.get("date")).strip(),
        })
    return out


def _empty_linkedin_data() -> dict:
    return {
        "positions": [],
        "skills": [],
        "education": [],
        "certifications": [],
        "summary": "",
        "industry": "",
        "headline": "",
        # Batch 1.5 — expanded sections
        "languages": [],
        "projects": [],
        "volunteer": [],
        "courses": [],
        # Two-pass — raw text kept for offline LLM re-runs (empty here).
        "raw_text": "",
    }


# ── Public async/sync parse API ───────────────────────────────────

async def parse_linkedin_pdf_async(file_path: str) -> dict:
    """Parse a LinkedIn 'Save to PDF' export into the canonical dict schema.

    Returns an empty-data dict on failure (missing pdfplumber, corrupt PDF,
    non-LinkedIn PDF, LLM unavailable) — never raises.
    """
    text = _extract_text(file_path)
    if not text or not _looks_like_linkedin(text):
        if text:
            logger.info("PDF at %s does not look like a LinkedIn export; skipping", file_path)
        return _empty_linkedin_data()

    return await parse_linkedin_from_text(text)


async def parse_linkedin_from_text(text: str) -> dict:
    """Parse already-extracted LinkedIn text into the canonical dict schema.

    Factored out of ``parse_linkedin_pdf_async`` so the two-pass orchestrator
    can re-run the LinkedIn LLM pass from the stored ``cv.linkedin_raw_text``
    on a later profile change — no re-upload needed. Returns the empty-data
    dict when the text doesn't look like a LinkedIn export.
    """
    if not text or not _looks_like_linkedin(text):
        return _empty_linkedin_data()

    sections = _split_sections(text)
    header = _extract_header_fields(sections.get("header", ""))
    summary = sections.get("summary", "").strip()
    skills = _extract_skills(
        sections.get("skills", "") or sections.get("top skills", "")
    )
    experience_text = sections.get("experience", "")
    education_text = sections.get("education", "")
    certs_text = (
        sections.get("certifications", "")
        or sections.get("licenses & certifications", "")
    )
    # Batch 1.5 — four additional sections.
    languages_text = sections.get("languages", "")
    projects_text = sections.get("projects", "")
    volunteer_text = sections.get("volunteer experience", "")
    courses_text = sections.get("courses", "")

    # Seven LLM calls in parallel — only the ones with text.
    async def _maybe(prompt_template: str, text: str, key: str):
        if not text.strip():
            return {key: []}
        return await _llm_json(prompt_template.format(text=text))

    exp_task = _maybe(_EXPERIENCE_PROMPT, experience_text, "positions")
    edu_task = _maybe(_EDUCATION_PROMPT, education_text, "education")
    cert_task = _maybe(_CERTIFICATIONS_PROMPT, certs_text, "certifications")
    lang_task = _maybe(_LANGUAGES_PROMPT, languages_text, "languages")
    proj_task = _maybe(_PROJECTS_PROMPT, projects_text, "projects")
    vol_task = _maybe(_VOLUNTEER_PROMPT, volunteer_text, "volunteer")
    course_task = _maybe(_COURSES_PROMPT, courses_text, "courses")

    (
        exp_raw, edu_raw, cert_raw,
        lang_raw, proj_raw, vol_raw, course_raw,
    ) = await asyncio.gather(
        exp_task, edu_task, cert_task,
        lang_task, proj_task, vol_task, course_task,
    )

    def _get(r: Any, key: str) -> Any:
        return r.get(key) if isinstance(r, dict) else None

    return {
        "positions": _coerce_positions(_get(exp_raw, "positions")),
        "skills": skills,
        "education": _coerce_education(_get(edu_raw, "education")),
        "certifications": _coerce_certifications(_get(cert_raw, "certifications")),
        "summary": summary,
        "industry": header.get("industry", ""),
        "headline": header.get("headline", ""),
        # Batch 1.5 — expanded sections
        "languages": _coerce_languages(_get(lang_raw, "languages")),
        "projects": _coerce_projects(_get(proj_raw, "projects")),
        "volunteer": _coerce_volunteer(_get(vol_raw, "volunteer")),
        "courses": _coerce_courses(_get(course_raw, "courses")),
        # Two-pass — keep the extracted text so the LLM pass can re-run on a
        # later profile change without the user re-uploading the PDF.
        "raw_text": text,
    }


def parse_linkedin_pdf(file_path: str) -> dict:
    """Synchronous wrapper for ``parse_linkedin_pdf_async`` (used by CLI + route)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(lambda: asyncio.run(parse_linkedin_pdf_async(file_path))).result()
    return asyncio.run(parse_linkedin_pdf_async(file_path))


# ── Merge into CVData (UNCHANGED — contract with downstream) ─────

def enrich_cv_from_linkedin(cv: CVData, linkedin_data: dict) -> CVData:
    """Merge LinkedIn data into existing CVData, deduplicating."""
    # Skills
    seen_skills = {s.lower() for s in cv.skills}
    new_linkedin_skills = []
    for s in linkedin_data.get("skills", []):
        if s.lower() not in seen_skills:
            new_linkedin_skills.append(s)
            seen_skills.add(s.lower())

    # Job titles from positions
    seen_titles = {t.lower() for t in cv.job_titles}
    for pos in linkedin_data.get("positions", []):
        title = pos.get("title", "")
        if title and title.lower() not in seen_titles:
            cv.job_titles.append(title)
            seen_titles.add(title.lower())

    # Education
    existing_edu = {e.lower() for e in cv.education}
    for edu in linkedin_data.get("education", []):
        entry = f"{edu.get('degree', '')} - {edu.get('school', '')}".strip(" -")
        if entry and entry.lower() not in existing_edu:
            cv.education.append(entry)
            existing_edu.add(entry.lower())

    # Certifications
    existing_certs = {c.lower() for c in cv.certifications}
    for cert in linkedin_data.get("certifications", []):
        name = cert.get("name", "")
        if name and name.lower() not in existing_certs:
            cv.certifications.append(name)
            existing_certs.add(name.lower())

    # Summary — only fill if empty
    if not cv.summary and linkedin_data.get("summary"):
        cv.summary = linkedin_data["summary"]

    # Store LinkedIn-specific fields
    cv.linkedin_positions = linkedin_data.get("positions", [])
    cv.linkedin_skills = new_linkedin_skills
    cv.linkedin_industry = linkedin_data.get("industry", "")
    # Two-pass — keep the raw text (if the parser supplied it) so the LLM
    # pass can re-run offline. Only overwrite when a non-empty value arrives,
    # so a partial re-enrich never wipes a previously-stored transcript.
    if linkedin_data.get("raw_text"):
        cv.linkedin_raw_text = linkedin_data["raw_text"]

    # Batch 1.5 — expanded sections. Overwrite rather than merge: LinkedIn
    # is the canonical source for these, and re-parsing a profile should
    # reflect the new state rather than accumulate stale entries.
    cv.linkedin_languages = linkedin_data.get("languages", [])
    cv.linkedin_projects = linkedin_data.get("projects", [])
    cv.linkedin_volunteer = linkedin_data.get("volunteer", [])
    cv.linkedin_courses = linkedin_data.get("courses", [])

    return cv
