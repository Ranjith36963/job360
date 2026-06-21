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

# Minimum clear vertical gutter (px) that marks a real two-column layout.
_COLUMN_GUTTER_MIN = 24


def _words_to_lines(words: list[dict]) -> str:
    """Rebuild text from words: group by ``top`` (3px tolerance), sort lines
    top→bottom and words left→right within a line."""
    from collections import defaultdict

    rows: dict = defaultdict(list)
    for w in words:
        rows[round(float(w.get("top", 0)) / 3.0)].append(w)
    out: list[str] = []
    for key in sorted(rows):
        ws = sorted(rows[key], key=lambda w: float(w.get("x0", 0)))
        out.append(" ".join(str(w.get("text", "")) for w in ws))
    return "\n".join(out)


def _dewrap_columns(words: list[dict], page_width: float) -> str | None:
    """De-interleave a two-column page so each column reads top-to-bottom.

    LinkedIn's "Save to PDF" puts a sidebar (Contact / Top Skills /
    Certifications) beside the main column. pdfplumber's ``extract_text``
    reads them in visual-line order, interleaving the two — which orphans the
    "Top Skills" items under the wrong heading. This finds a clear vertical
    gutter and emits the left column fully, then the right column.

    Returns ``None`` when there is no genuine two-column structure (no wide
    empty gutter, or one side is sparse) — the caller then uses flat text, so
    single-column CVs/LinkedIn exports are unaffected.
    """
    if not words or page_width <= 0:
        return None
    lo, hi = int(page_width * 0.18), int(page_width * 0.58)
    if hi <= lo:
        return None
    # Mark every x covered by a word within the candidate gutter region.
    covered = bytearray(hi - lo + 1)
    for w in words:
        x0 = int(float(w.get("x0", 0)))
        x1 = int(float(w.get("x1", x0)))
        for x in range(max(lo, x0), min(hi, x1) + 1):
            covered[x - lo] = 1
    # Longest run of uncovered x in [lo, hi] = the gutter.
    best_w = best_a = best_b = 0
    run_start = None
    for i in range(len(covered) + 1):
        if i < len(covered) and covered[i] == 0:
            if run_start is None:
                run_start = i
        elif run_start is not None:
            if i - run_start > best_w:
                best_w, best_a, best_b = i - run_start, run_start + lo, i + lo
            run_start = None
    if best_w < _COLUMN_GUTTER_MIN:
        return None
    left = [w for w in words if float(w.get("x1", 0)) <= best_a]
    right = [w for w in words if float(w.get("x0", 0)) >= best_b]
    if len(left) < 6 or len(right) < 6:
        return None
    return _words_to_lines(left) + "\n" + _words_to_lines(right)


def _extract_text(file_path: str) -> str:
    """Read all pages of a PDF into one newline-joined string. Empty on failure.

    Two-column pages are de-interleaved (``_dewrap_columns``) so a sidebar
    reads as a contiguous block; single-column pages fall back to flat
    ``extract_text`` unchanged.
    """
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed. Run: pip install pdfplumber")
        return ""
    try:
        with pdfplumber.open(file_path) as pdf:
            parts: list[str] = []
            for page in pdf.pages:
                col: str | None = None
                try:
                    words = page.extract_words()
                    col = _dewrap_columns(words, float(page.width or 0))
                except Exception:  # noqa: BLE001 — fall back to flat text
                    col = None
                parts.append(col if col is not None else (page.extract_text() or ""))
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


_TECH_LINE = re.compile(
    r"^\s*(?:technologies|tech stack|tools|skills|continuously learning)\s*:\s*(.*)$",
    re.IGNORECASE,
)
_TECH_SPLIT = re.compile(r"[•·|,]|\s-\s")


def _extract_inline_tech_skills(text: str) -> list[str]:
    """Deterministically pull skills from inline 'Technologies: A • B • C' lines
    in the experience body (incl. a wrapped continuation line starting '•').

    LinkedIn lists the tech stack per role on these lines; the section-based
    ``_extract_skills`` only reads the "Top Skills" sidebar, so without this the
    deterministic pass misses Docker/AWS Bedrock/RAG/etc. that are stated outright.
    """
    lines = text.splitlines()
    out: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        m = _TECH_LINE.match(lines[i])
        if not m:
            i += 1
            continue
        buf = [m.group(1)]
        j = i + 1
        # Keep absorbing wrapped continuation lines. A wrap is signalled either
        # by the previous line ending on a dangling bullet ("OpenAI API •") or
        # by the next line starting with a bullet ("• Python • ...").
        while j < len(lines):
            prev_dangles = buf[-1].rstrip().endswith(("•", "·"))
            nxt = lines[j].strip()
            if prev_dangles or nxt[:1] in {"•", "·", "-"}:
                buf.append(lines[j])
                j += 1
            else:
                break
        for tok in _TECH_SPLIT.split(" ".join(buf)):
            t = tok.strip().lstrip("•·-").strip()
            if t and 1 < len(t) <= 40 and t.lower() not in seen:
                out.append(t)
                seen.add(t.lower())
        i = j
    return out


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
technologies in their summary/experience, anything in a "Continuously learning"
or similar line, AND skills named inside summary sentences or "WHAT I DO"-style
bullets (e.g. "Multimodal AI", "LLM fine-tuning", "prompt engineering", "FAISS",
"vector databases", "cloud deployment").

Return JSON: {{"skills": ["Skill One", "Skill Two", ...]}}

Rules:
- Only skills the text supports. Do not invent.
- Individual items, not categories. Pull each tool out of a parenthesis list,
  e.g. "vector databases (ChromaDB, FAISS)" → "Vector Databases", "ChromaDB", "FAISS".
- Do NOT fabricate "<word> Processing" skills from a modality list like
  "text, image, speech, audio processing" — emit "Multimodal AI" and
  "Audio Processing" only if those exact terms appear.
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


def deterministic_linkedin_fields(text: str) -> dict:
    """Pass 1 for LinkedIn — STRUCTURE only, NO LLM.

    Splits sections, reads the header (name/headline/industry), the "Top Skills"
    sidebar + inline "Technologies: A • B • C" lines, and the summary. Section
    bodies that need semantic parsing (experience/education/…) are LEFT to the
    LLM pass (``llm_linkedin_fields``). No prose skill-term scan (CLAUDE.md
    rule #28). Mirrors the CV deterministic pass so the LinkedIn lane has a real,
    independent deterministic half — the deterministic and LLM passes never feed
    each other; both read the same raw text and merge afterwards.
    """
    if not text or not _looks_like_linkedin(text):
        return {"skills": [], "summary": "", "industry": "", "headline": "", "raw_text": text or ""}

    sections = _split_sections(text)
    header = _extract_header_fields(sections.get("header", ""))
    summary = sections.get("summary", "").strip()
    skills = _extract_skills(
        sections.get("skills", "") or sections.get("top skills", "")
    )
    # Also harvest the inline "Technologies: A • B • C" lines stated per role —
    # deterministic, and recovers the tech stack the Top-Skills sidebar omits.
    seen_sk = {s.lower() for s in skills}
    for s in _extract_inline_tech_skills(text):
        if s.lower() not in seen_sk:
            skills.append(s)
            seen_sk.add(s.lower())
    return {
        "skills": skills,
        "summary": summary,
        "industry": header.get("industry", ""),
        "headline": header.get("headline", ""),
        # Keep the extracted text so the passes can re-run on a later profile
        # change without the user re-uploading the PDF.
        "raw_text": text,
    }


async def llm_linkedin_fields(text: str) -> dict:
    """Pass 2 for LinkedIn — LLM ONLY.

    Runs the seven per-section LLM extractions (experience/education/…) PLUS the
    prose-skills pass (``llm_infer_linkedin_skills``) over the same raw text the
    deterministic pass read. Returns the LLM-owned fields; the orchestrator (and
    ``parse_linkedin_from_text``) merge this with the deterministic dict.
    """
    empty = {
        "positions": [], "education": [], "certifications": [],
        "languages": [], "projects": [], "volunteer": [], "courses": [], "skills": [],
    }
    if not text or not _looks_like_linkedin(text):
        return empty

    sections = _split_sections(text)
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

    # Seven section LLM calls + the prose-skills pass, in parallel — only the
    # ones with text actually hit a provider (``_maybe`` short-circuits blanks).
    async def _maybe(prompt_template: str, text: str, key: str):
        if not text.strip():
            return {key: []}
        return await _llm_json(prompt_template.format(text=text))

    (
        exp_raw, edu_raw, cert_raw,
        lang_raw, proj_raw, vol_raw, course_raw, prose_skills,
    ) = await asyncio.gather(
        _maybe(_EXPERIENCE_PROMPT, experience_text, "positions"),
        _maybe(_EDUCATION_PROMPT, education_text, "education"),
        _maybe(_CERTIFICATIONS_PROMPT, certs_text, "certifications"),
        _maybe(_LANGUAGES_PROMPT, languages_text, "languages"),
        _maybe(_PROJECTS_PROMPT, projects_text, "projects"),
        _maybe(_VOLUNTEER_PROMPT, volunteer_text, "volunteer"),
        _maybe(_COURSES_PROMPT, courses_text, "courses"),
        llm_infer_linkedin_skills(text),
    )

    def _get(r: Any, key: str) -> Any:
        return r.get(key) if isinstance(r, dict) else None

    return {
        "positions": _coerce_positions(_get(exp_raw, "positions")),
        "education": _coerce_education(_get(edu_raw, "education")),
        "certifications": _coerce_certifications(_get(cert_raw, "certifications")),
        "languages": _coerce_languages(_get(lang_raw, "languages")),
        "projects": _coerce_projects(_get(proj_raw, "projects")),
        "volunteer": _coerce_volunteer(_get(vol_raw, "volunteer")),
        "courses": _coerce_courses(_get(course_raw, "courses")),
        "skills": list(prose_skills) if isinstance(prose_skills, list) else [],
    }


def merge_linkedin_fields(det: dict, llm: dict) -> dict:
    """Merge the two independent LinkedIn passes into ONE canonical dict.

    ``det`` (structure: skills/summary/header) + ``llm`` (LLM: positions/
    education/…/prose-skills) → one dict ready for ``enrich_cv_from_linkedin``.
    Skills are unioned (deterministic Top-Skills/inline first, then LLM prose),
    so neither pass can clobber the other. Used by both ``parse_linkedin_from_text``
    and the two-pass orchestrator so the merge logic lives in exactly one place.
    """
    det = det or {}
    llm = llm or {}
    skills = list(det.get("skills", []))
    seen = {s.lower() for s in skills}
    for s in llm.get("skills", []):
        if s.lower() not in seen:
            skills.append(s)
            seen.add(s.lower())
    return {
        "positions": llm.get("positions", []),
        "skills": skills,
        "education": llm.get("education", []),
        "certifications": llm.get("certifications", []),
        "summary": det.get("summary", ""),
        "industry": det.get("industry", ""),
        "headline": det.get("headline", ""),
        "languages": llm.get("languages", []),
        "projects": llm.get("projects", []),
        "volunteer": llm.get("volunteer", []),
        "courses": llm.get("courses", []),
        "raw_text": det.get("raw_text", "") or llm.get("raw_text", ""),
    }


async def parse_linkedin_from_text(text: str) -> dict:
    """Parse already-extracted LinkedIn text into the canonical dict schema.

    Thin merge of the two independent passes — ``deterministic_linkedin_fields``
    (structure) and ``llm_linkedin_fields`` (LLM). Factored this way so the
    two-pass orchestrator can call each half separately on a later profile change
    from the stored ``cv.linkedin_raw_text`` (no re-upload). Returns the
    empty-data dict when the text doesn't look like a LinkedIn export.
    """
    if not text or not _looks_like_linkedin(text):
        return _empty_linkedin_data()

    det = deterministic_linkedin_fields(text)
    llm = await llm_linkedin_fields(text)
    merged = merge_linkedin_fields(det, llm)
    merged["raw_text"] = text
    return merged


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
