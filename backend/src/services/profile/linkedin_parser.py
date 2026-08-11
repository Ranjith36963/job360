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

from src.services.profile._llm_utils import coerce_str
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


def _words_to_lines(words: list[dict[str, Any]]) -> str:
    """Rebuild text from words: group by ``top`` (3px tolerance), sort lines
    top→bottom and words left→right within a line."""
    from collections import defaultdict

    rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for w in words:
        rows[round(float(w.get("top", 0)) / 3.0)].append(w)
    out: list[str] = []
    for key in sorted(rows):
        ws = sorted(rows[key], key=lambda w: float(w.get("x0", 0)))
        out.append(" ".join(str(w.get("text", "")) for w in ws))
    return "\n".join(out)


def _dewrap_columns(words: list[dict[str, Any]], page_width: float) -> str | None:
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


# NOTE: deliberately NOT named _EMAIL_RE. One already exists further down this
# module with a CAPTURE GROUP, and being defined later it would shadow this one
# — `findall` would then return only the group (the local part), silently
# storing "ada" instead of "ada@example.com". Caught by running the parser over
# a real export rather than a fixture.
_CONTACT_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_URL_RE = re.compile(r"(?:https?://)?(?:www\.)?[\w-]+\.[\w.-]+(?:/[\w./?%&=~-]*)?")


def _is_linkedin_host(url: str) -> bool:
    """True when ``url``'s HOST is linkedin.com (or a subdomain of it).

    Compares the parsed hostname, never a substring. ``"linkedin.com" in url``
    is wrong in both directions and CodeQL flags it (py/incomplete-url-
    substring-sanitization, high):
      * ``linkedin.com.attacker.io``  would be TREATED as LinkedIn, and
      * ``notlinkedin.com``           would be wrongly dropped from a person's
        own website list.

    A scheme is added before parsing because these come out of a PDF as bare
    hosts ("www.linkedin.com/in/ada"), which ``urlparse`` would otherwise read
    as a path with no host at all.
    """
    from urllib.parse import urlparse  # noqa: PLC0415 — stdlib, used once here

    candidate = url if "://" in url else f"http://{url}"
    try:
        host = (urlparse(candidate).hostname or "").lower()
    except ValueError:
        return False
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def _extract_contact_fields(contact_text: str) -> dict[str, Any]:
    """Structure the LinkedIn "Contact" block instead of discarding it.

    That section was SPLIT (so it bounded its neighbours) and then read by
    nobody — the same "parsed and thrown away" shape as GitHub's identity block.
    It holds the person's email, phone, profile URL and personal sites.

    Deterministic on purpose (rule #28 territory): emails, phone numbers and
    URLs are PATTERNS, not semantics, so this needs no LLM and cannot
    hallucinate. Anything ambiguous is simply left out.

    ``websites`` excludes the linkedin.com URL itself — that is already
    ``linkedin_url``, and repeating it as a "personal site" would be wrong.
    """
    text = contact_text or ""
    if not text.strip():
        return {}

    emails = _CONTACT_EMAIL_RE.findall(text)
    li_match = _LINKEDIN_URL_RE.search(text.replace("\n", ""))

    phones: list[str] = []
    for cand in _PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", cand)
        # A real number, not a year or a stray page reference.
        if 9 <= len(digits) <= 15:
            phones.append(cand.strip())

    websites: list[str] = []
    for line in text.splitlines():
        if _CONTACT_EMAIL_RE.search(line):
            continue  # an address is not a website
        for url in _URL_RE.findall(line):
            u = url.strip().rstrip(".,;)")
            if "." not in u or _is_linkedin_host(u):
                continue
            if u.lower() in {w.lower() for w in websites}:
                continue
            websites.append(u)

    out: dict[str, Any] = {}
    if emails:
        out["email"] = emails[0]
    if phones:
        out["phone"] = phones[0]
    if li_match:
        out["linkedin_url"] = li_match.group(0)
    if websites:
        out["websites"] = websites
    return out


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


# ReDoS-safe (CodeQL py/polynomial-redos, same class as channels.py). This runs
# via finditer over UPLOADED CV / LinkedIn text — attacker-supplied file content,
# a bigger and less trusted surface than the channels.py case.
#
# The original ``@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}`` let ``.`` match inside the
# domain class AND as the literal TLD separator → O(n) split points → quadratic
# (measured 1.8 s on an 8 k payload). Note a "one dot excluded" rewrite is NOT
# enough: ``(?:\.[label])*\.[A-Za-z]{2,}`` is still ambiguous because the star
# group can also consume the final ``.tld`` — that variant still took 1.8 s.
#
# The trailing ``\.[A-Za-z]{2,}`` TLD assertion is therefore dropped entirely:
# each ``.label`` is now consumed exactly once (linear, 0.001 s — 1800× faster),
# and nothing is lost because the caller below only reads ``m.group(1)`` (the
# local part) and already filters on ``len(local) >= 5``.
_EMAIL_RE = re.compile(r"([A-Za-z0-9._%+\-]+)@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_LINKEDIN_SLUG_RE = re.compile(r"linkedin\.com/in/([A-Za-z0-9\-]+)", re.IGNORECASE)


def _identity_tokens(text: str) -> set[str]:
    """Compacted letters of the person's OWN contact identifiers — email
    local-part(s) and the LinkedIn URL slug. Used to recognise the name when the
    2-column PDF de-wrap bleeds the identity block (name/headline/location) into
    the Top-Skills sidebar. STRUCTURAL: matches the user's own contact info, never
    a skill vocabulary (CLAUDE.md rule #28)."""
    tokens: set[str] = set()
    for m in _EMAIL_RE.finditer(text):  # emails sit on their own line, no wrap
        local = re.sub(r"[^a-z]", "", m.group(1).lower())
        if len(local) >= 5:
            tokens.add(local)
    healed = text.replace("\n", "")  # heal a slug wrapped across two lines
    for m in _LINKEDIN_SLUG_RE.finditer(healed):
        slug = re.sub(r"[^a-z]", "", m.group(1).lower())
        if len(slug) >= 5:
            tokens.add(slug)
    return tokens


def _drop_trailing_identity_block(skills: list[str], id_tokens: set[str]) -> list[str]:
    """If a skill line's compacted letters EXACTLY equal one of the person's own
    contact identifiers, it's their name that bled in — and the de-wrap always
    appends the identity block (name → headline → location) AFTER the real skills,
    so truncate from the name line to the end. Exact-token match (not substring)
    + multi-word + position>0 keeps this from ever eating a real skill."""
    if not id_tokens or not skills:
        return skills
    for idx, s in enumerate(skills):
        words = re.findall(r"[A-Za-z]+", s)
        compact = "".join(words).lower()
        if idx > 0 and len(words) >= 2 and len(compact) >= 5 and compact in id_tokens:
            return skills[:idx]
    return skills


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

_LANGUAGES_PROMPT = (
    "Extract every human language from the LinkedIn Languages section text below.\n"
    'Return JSON: {{"languages": [{{"language": str, "proficiency": str}}, ...]}}\n'
    "\n"
    "Rules:\n"
    '- "language" = the language name (e.g. "English", "Mandarin Chinese", "Spanish").\n'
    '- "proficiency" = the proficiency level as written (e.g. "Native or bilingual", '
    '"Professional working", "Elementary"). Empty string if missing.\n'
    "\n"
    "TEXT:\n"
    "---\n"
    "{text}\n"
    "---"
)

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

_VOLUNTEER_PROMPT = (
    "Extract every volunteer role from the LinkedIn Volunteer Experience section text below.\n"
    'Return JSON: {{"volunteer": [{{"role": str, "organisation": str, "cause": str, '
    '"start": str, "end": str, "description": str}}, ...]}}\n'
    "\n"
    "Rules:\n"
    '- "role" = the volunteer position title.\n'
    '- "organisation" = the organisation/charity name.\n'
    '- "cause" = the stated cause if present (e.g. "Education", "Environment"). Empty if missing.\n'
    '- "start"/"end" verbatim. Empty if missing.\n'
    '- "description" = concatenated bullets/paragraph. Empty if missing.\n'
    "\n"
    "TEXT:\n"
    "---\n"
    "{text}\n"
    "---"
)

# ── The seven sections the splitter recognised and nobody read ──────────────
# ``_SECTION_HEADINGS`` lists 20 headings; only 11 had an extractor. The rest
# were split purely so they acted as boundaries, then dropped. Each below is
# real evidence on the profiles that carry it — see CVData for why each earns a
# shelf. Same shape as the prompts above: one section of text in, typed rows
# out, empty list when the section is absent.

_HONORS_PROMPT = """Extract every award from the LinkedIn Honors & Awards section text below.
Return JSON: {{"honors": [{{"title": str, "issuer": str, "date": str, "description": str}}, ...]}}

Rules:
- "title" = the award name as written.
- "issuer" = the awarding body if present. Empty if missing.
- "date" verbatim as written. Empty if missing.
- "description" = any prose body. Empty if missing.

TEXT:
---
{text}
---"""

_PUBLICATIONS_PROMPT = """Extract every publication from the LinkedIn Publications section text below.
Return JSON: {{"publications": [{{"title": str, "publisher": str, "date": str, "url": str, "description": str}}, ...]}}

Rules:
- "title" = the paper/article title.
- "publisher" = journal, conference or outlet if present. Empty if missing.
- "date" verbatim. Empty if missing.
- "url" = link if present in the text; empty otherwise.
- "description" = abstract/summary prose. Empty if missing.

TEXT:
---
{text}
---"""

_PATENTS_PROMPT = """Extract every patent from the LinkedIn Patents section text below.
Return JSON: {{"patents": [{{"title": str, "number": str, "status": str, "date": str}}, ...]}}

Rules:
- "title" = the patent title.
- "number" = patent/application number if present. Empty if missing.
- "status" = e.g. "Issued", "Pending". Empty if not stated.
- "date" verbatim. Empty if missing.

TEXT:
---
{text}
---"""

_ORGANIZATIONS_PROMPT = """Extract every organisation from the LinkedIn Organizations section text below.
Return JSON: {{"organizations": [{{"name": str, "role": str, "start": str, "end": str}}, ...]}}

Rules:
- "name" = the organisation or professional body (e.g. "BCS", "IEEE").
- "role" = the stated position/membership if present. Empty if missing.
- "start"/"end" verbatim. Empty if missing.

TEXT:
---
{text}
---"""

_TEST_SCORES_PROMPT = """Extract every test score from the LinkedIn Test Scores section text below.
Return JSON: {{"test_scores": [{{"name": str, "score": str, "date": str}}, ...]}}

Rules:
- "name" = the test (e.g. "IELTS", "GRE", "TOEFL").
- "score" = the score exactly as written, including any band/section detail.
- "date" verbatim. Empty if missing.

TEXT:
---
{text}
---"""

_RECOMMENDATIONS_PROMPT = """Extract every recommendation from the LinkedIn Recommendations section text below.
Return JSON: {{"recommendations": [{{"author": str, "relationship": str, "text": str}}, ...]}}

Rules:
- "author" = who wrote it, if named. Empty if missing.
- "relationship" = how they know this person (e.g. "managed directly"). Empty if missing.
- "text" = the recommendation prose, verbatim, trimmed to 600 characters.
- These are OTHER PEOPLE's words about this person — do not paraphrase or
  summarise them, and never invent one.

TEXT:
---
{text}
---"""

_HEADLINE_PROMPT = """Below is the raw text of a LinkedIn profile PDF export.

Find the person's HEADLINE — the one-line professional tagline LinkedIn shows
directly under their name. Return it verbatim, joining any lines the PDF wrapped
mid-phrase back into a single line.

WHY THIS NEEDS YOU AND NOT A PARSER. The export is TWO COLUMNS. The left rail
(Contact, Top Skills, Certifications) is flattened first, so the name and
headline do not appear at the top of the text at all — they land in the middle,
after the last left-column section. A structural reader looking for a header
block finds nothing, which is why this field was empty on every two-column
export.

RULES:
1. The headline is the line or lines immediately AFTER the person's full name
   and BEFORE their location or the Summary section.
2. It is a self-description, often with "|" or "•" separators. It is NOT a
   certification, a job title with an employer, or a section heading.
3. Return the location line separately if one directly follows the headline.
4. If you genuinely cannot find a headline, return empty strings. Never invent
   one and never assemble one from their job titles.

Return ONLY JSON: {{"headline": "<verbatim, unwrapped>", "location": "<or empty>"}}

TEXT:
{text}
"""

_INTERESTS_PROMPT = """Extract every followed company, group or influencer from the LinkedIn Interests section text below.
Return JSON: {{"interests": ["Name One", "Name Two", ...]}}

Rules:
- One entry per line/name as written. Names only, no commentary.
- Return [] if the section holds nothing nameable.

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


def _coerce_positions(raw: Any) -> list[dict[str, str]]:
    """Shape a list-of-dicts LLM result into the canonical positions schema."""
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
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


def _coerce_education(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
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


def _coerce_certifications(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
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

def _coerce_languages(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
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


def _coerce_projects(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
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


def _coerce_volunteer(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
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


def _coerce_rows(raw: Any, fields: tuple[str, ...]) -> list[dict[str, str]]:
    """Coerce an LLM row list to typed dicts with exactly ``fields``.

    One shared cleaner for the seven sections added 2026-08-09 (honors,
    publications, patents, organizations, test scores, recommendations). Their
    shapes differ only in FIELD NAMES, so a coercer each would be six copies of
    the same defensive loop — and six places for a future fix to be applied five
    times.

    A row is kept only when its FIRST field (the identifying one: title / name /
    author) has content, mirroring the existing per-section coercers: a row with
    no identity is LLM noise, not an entry.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = {f: coerce_str(item.get(f)).strip() for f in fields}
        if not row[fields[0]]:
            continue
        out.append(row)
    return out


def _coerce_courses(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
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


def _empty_linkedin_data() -> dict[str, Any]:
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
        # 2026-08-09 — the seven sections that were split and then discarded.
        "honors": [],
        "publications": [],
        "patents": [],
        "organizations": [],
        "test_scores": [],
        "recommendations": [],
        "interests": [],
        "contact": {},
        # Two-pass — raw text kept for offline LLM re-runs (empty here).
        "raw_text": "",
    }


# ── Public async/sync parse API ───────────────────────────────────

async def parse_linkedin_pdf_async(file_path: str) -> dict[str, Any]:
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


def deterministic_linkedin_fields(text: str) -> dict[str, Any]:
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
    # REAL layout: the header section is often EMPTY and the de-wrap appends the
    # identity block (name/headline/location) to the END of the Top-Skills body.
    # Recognise the name via the user's own email/URL and truncate the trailing
    # block (structural, not a keyword denylist: rule #28 safe).
    skills = _drop_trailing_identity_block(skills, _identity_tokens(text))
    # LinkedIn's 2-column "Save to PDF" export interleaves the left "Top Skills"
    # sidebar with the right-column header, so de-wrap can bleed the person's own
    # name / headline / location into the skills body. Those are already captured
    # structurally as header fields — drop any "skill" that is verbatim a header
    # line (structural identity filter, NOT a keyword denylist: rule #28 safe).
    header_lines = {
        ln.strip().lower()
        for ln in sections.get("header", "").splitlines()
        if ln.strip()
    }
    if skills:
        skills = [s for s in skills if s.strip().lower() not in header_lines]
    # Also harvest the inline "Technologies: A • B • C" lines stated per role —
    # deterministic, and recovers the tech stack the Top-Skills sidebar omits.
    seen_sk = {s.lower() for s in skills}
    for s in _extract_inline_tech_skills(text):
        if s.lower() not in seen_sk and s.strip().lower() not in header_lines:
            skills.append(s)
            seen_sk.add(s.lower())
    return {
        "skills": skills,
        "summary": summary,
        "industry": header.get("industry", ""),
        "headline": header.get("headline", ""),
        # The Contact block, structured rather than discarded. Deterministic:
        # emails/phones/URLs are patterns, so this costs no LLM call and
        # cannot hallucinate.
        "contact": _extract_contact_fields(sections.get("contact", "")),
        # Keep the extracted text so the passes can re-run on a later profile
        # change without the user re-uploading the PDF.
        "raw_text": text,
    }


async def llm_linkedin_fields(text: str) -> dict[str, list[Any]]:
    """Pass 2 for LinkedIn — LLM ONLY.

    Runs the seven per-section LLM extractions (experience/education/…) PLUS the
    prose-skills pass (``llm_infer_linkedin_skills``) over the same raw text the
    deterministic pass read. Returns the LLM-owned fields; the orchestrator (and
    ``parse_linkedin_from_text``) merge this with the deterministic dict.
    """
    empty: dict[str, list[Any]] = {
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
    # 2026-08-09 — the seven sections that were split and then discarded.
    honors_text = (
        sections.get("honors & awards", "") or sections.get("honors-awards", "")
    )
    publications_text = sections.get("publications", "")
    patents_text = sections.get("patents", "")
    organizations_text = sections.get("organizations", "")
    test_scores_text = sections.get("test scores", "")
    recommendations_text = sections.get("recommendations", "")
    interests_text = sections.get("interests", "")

    # Seven section LLM calls + the prose-skills pass, in parallel — only the
    # ones with text actually hit a provider (``_maybe`` short-circuits blanks).
    async def _maybe(prompt_template: str, text: str, key: str) -> dict[str, Any]:
        if not text.strip():
            return {key: []}
        return await _llm_json(prompt_template.format(text=text))

    (
        exp_raw, edu_raw, cert_raw,
        lang_raw, proj_raw, vol_raw, course_raw, prose_skills,
        honors_raw, pubs_raw, patents_raw, orgs_raw,
        scores_raw, recs_raw, interests_raw, headline_raw,
    ) = await asyncio.gather(
        _maybe(_EXPERIENCE_PROMPT, experience_text, "positions"),
        _maybe(_EDUCATION_PROMPT, education_text, "education"),
        _maybe(_CERTIFICATIONS_PROMPT, certs_text, "certifications"),
        _maybe(_LANGUAGES_PROMPT, languages_text, "languages"),
        _maybe(_PROJECTS_PROMPT, projects_text, "projects"),
        _maybe(_VOLUNTEER_PROMPT, volunteer_text, "volunteer"),
        _maybe(_COURSES_PROMPT, courses_text, "courses"),
        llm_infer_linkedin_skills(text),
        # ``_maybe`` short-circuits an absent section without touching a
        # provider, so a profile with none of these costs exactly nothing —
        # which is why adding seven calls does not add seven calls.
        _maybe(_HONORS_PROMPT, honors_text, "honors"),
        _maybe(_PUBLICATIONS_PROMPT, publications_text, "publications"),
        _maybe(_PATENTS_PROMPT, patents_text, "patents"),
        _maybe(_ORGANIZATIONS_PROMPT, organizations_text, "organizations"),
        _maybe(_TEST_SCORES_PROMPT, test_scores_text, "test_scores"),
        _maybe(_RECOMMENDATIONS_PROMPT, recommendations_text, "recommendations"),
        _maybe(_INTERESTS_PROMPT, interests_text, "interests"),
        # Reads the FULL text, not a section: in a 2-column export the
        # header has no section to read — that is the bug being fixed.
        _maybe(_HEADLINE_PROMPT, text, "headline"),
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
        # The seven previously-discarded sections. Coerced through one shared
        # row cleaner rather than seven near-identical ones — the shapes differ
        # only in their field names, and a per-section coercer for each would be
        # six copies of the same defensive loop.
        "honors": _coerce_rows(
            _get(honors_raw, "honors"), ("title", "issuer", "date", "description")
        ),
        "publications": _coerce_rows(
            _get(pubs_raw, "publications"),
            ("title", "publisher", "date", "url", "description"),
        ),
        "patents": _coerce_rows(
            _get(patents_raw, "patents"), ("title", "number", "status", "date")
        ),
        "organizations": _coerce_rows(
            _get(orgs_raw, "organizations"), ("name", "role", "start", "end")
        ),
        "test_scores": _coerce_rows(
            _get(scores_raw, "test_scores"), ("name", "score", "date")
        ),
        "recommendations": _coerce_rows(
            _get(recs_raw, "recommendations"), ("author", "relationship", "text")
        ),
        "interests": [
            s.strip()
            for s in (_get(interests_raw, "interests") or [])
            if isinstance(s, str) and s.strip()
        ],
        "headline": (_get(headline_raw, "headline") or "").strip()
        if isinstance(_get(headline_raw, "headline"), str)
        else "",
    }


def merge_linkedin_fields(det: dict[str, Any], llm: dict[str, Any]) -> dict[str, Any]:
    """Merge the two independent LinkedIn passes into ONE canonical dict.

    ``det`` (structure: skills/summary/header/contact) + ``llm`` (LLM:
    positions/education/…/prose-skills) → one dict ready for
    ``enrich_cv_from_linkedin``. Used by both ``parse_linkedin_from_text`` and
    the two-pass orchestrator so the merge logic lives in exactly one place.

    BUILT FROM ``_empty_linkedin_data()``, NOT FROM A HAND-LISTED SET OF KEYS.
    That is the whole point of this shape. This function used to return a
    literal with twelve keys, which made it a THIRD hand-maintained copy of the
    LinkedIn schema alongside ``_empty_linkedin_data`` and
    ``llm_linkedin_fields``. When eight new sections shipped on 2026-08-09 the
    other two copies were updated and this one was not, so those eight keys were
    dropped here — and because ``enrich_cv_from_linkedin`` then does
    ``cv.linkedin_honors = data.get("honors", [])``, the shelves were not merely
    left unfilled, they were ASSIGNED EMPTY on every extraction.

    Deriving the key set from the schema means a section added tomorrow flows
    through automatically. There is now one place to add a LinkedIn section, not
    three that must be kept in step by memory.

    Ownership: the LLM pass owns the section lists (assigned wholesale, empties
    included, so a genuine re-parse can still clear a section the person
    deleted); the deterministic pass owns the structural header and contact
    block and overrides only where it actually parsed something; skills are
    unioned so neither pass can clobber the other.
    """
    det = det or {}
    llm = llm or {}

    out = _empty_linkedin_data()
    for key, value in llm.items():
        if key in out:
            out[key] = value
    for key, value in det.items():
        if key in out and value:
            out[key] = value

    skills = list(det.get("skills", []))
    seen = {s.lower() for s in skills}
    for s in llm.get("skills", []):
        if s.lower() not in seen:
            skills.append(s)
            seen.add(s.lower())
    out["skills"] = skills
    out["raw_text"] = det.get("raw_text", "") or llm.get("raw_text", "")
    return out


async def parse_linkedin_from_text(text: str) -> dict[str, Any]:
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


def parse_linkedin_pdf(file_path: str) -> dict[str, Any]:
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

def enrich_cv_from_linkedin(
    cv: CVData, linkedin_data: dict[str, Any], *, llm_ran: bool = True
) -> CVData:
    """Merge LinkedIn data into existing CVData, deduplicating.

    ``llm_ran`` tells this function whether the LinkedIn LLM pass actually
    executed for this call. Defaults True so every existing caller keeps
    its current behaviour; only the two-pass orchestrator, which knows the
    cost cache skipped the pass, passes False. See the section comment
    below for why an empty value is ambiguous without it.
    """
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
        # An LLM returns this section as EITHER a list of objects
        # ({"name": ...}) or a bare list of strings, depending on how it read
        # the page — both are reasonable readings of "certifications". The
        # object-only assumption raised AttributeError and aborted the WHOLE
        # LinkedIn merge, so one loosely-shaped section could silently cost a
        # user every LinkedIn field. Accept both shapes.
        if isinstance(cert, str):
            name = cert.strip()
        elif isinstance(cert, dict):
            name = str(cert.get("name") or cert.get("title") or "").strip()
        else:
            continue
        if name and name.lower() not in existing_certs:
            cv.certifications.append(name)
            existing_certs.add(name.lower())

    # The About section always gets its own shelf. Keeping the fill-if-empty
    # copy into ``cv.summary`` preserves the old behaviour for profiles with no
    # CV summary, but the copy is no longer the ONLY home — before this, anyone
    # whose CV had a summary lost their LinkedIn About completely, and it is the
    # most self-authored prose either document contains.
    if linkedin_data.get("summary"):
        cv.linkedin_summary = linkedin_data["summary"]
        if not cv.summary:
            cv.summary = linkedin_data["summary"]

    # Same rule for the headline: LinkedIn's is stored in its own shelf, and
    # only fills the CV-owned ``headline`` when the CV had none. They are
    # genuinely different claims — a CV headline is a role label, a LinkedIn
    # one often states the stack and current availability.
    if linkedin_data.get("headline"):
        cv.linkedin_headline = linkedin_data["headline"]
        if not cv.headline:
            cv.headline = linkedin_data["headline"]

    # Store LinkedIn-specific fields
    # These five sections are LLM-ONLY output — the deterministic pass
    # explicitly leaves them alone. So an empty value here means one of two
    # very different things, and the caller is the only one who knows which:
    #   * the LLM ran and this profile genuinely has no such section, or
    #   * the LLM pass was SKIPPED by the cost cache (unchanged raw text)
    # Overwriting on the second case destroyed real data. Measured 2026-08-08:
    # upload LinkedIn (positions/languages/projects/volunteer/courses all
    # populate), then touch ANYTHING else on the profile — the LLM pass is
    # correctly skipped, the merge still ran, and all five were wiped to []
    # permanently. Nothing short of uploading a DIFFERENT PDF restored them.
    #
    # `llm_ran` lets the canonical-source intent survive (a real re-parse still
    # replaces, so removing a section from LinkedIn removes it here) without
    # the cache-hit path being able to erase anything.
    if llm_ran:
        cv.linkedin_positions = linkedin_data.get("positions", [])
    cv.linkedin_skills = new_linkedin_skills
    cv.linkedin_industry = linkedin_data.get("industry", "") or cv.linkedin_industry
    # Two-pass — keep the raw text (if the parser supplied it) so the LLM
    # pass can re-run offline. Only overwrite when a non-empty value arrives,
    # so a partial re-enrich never wipes a previously-stored transcript.
    if linkedin_data.get("raw_text"):
        cv.linkedin_raw_text = linkedin_data["raw_text"]

    # Contact is deterministic, so it is stored OUTSIDE the ``llm_ran`` gate:
    # a re-parse that skipped the paid passes must still refresh it. Only
    # overwrite on a non-empty parse so a failed read never blanks it.
    if linkedin_data.get("contact"):
        cv.linkedin_contact = dict(linkedin_data["contact"])

    # Batch 1.5 — expanded sections. Overwrite rather than merge: LinkedIn
    # is the canonical source for these, and re-parsing a profile should
    # reflect the new state rather than accumulate stale entries.
    if llm_ran:
        cv.linkedin_languages = linkedin_data.get("languages", [])
        cv.linkedin_projects = linkedin_data.get("projects", [])
        cv.linkedin_volunteer = linkedin_data.get("volunteer", [])
        cv.linkedin_courses = linkedin_data.get("courses", [])
        # Same overwrite rule: LinkedIn is canonical for its own sections, so a
        # re-parse reflects the CURRENT profile instead of accumulating entries
        # the person has since deleted.
        cv.linkedin_honors = linkedin_data.get("honors", [])
        cv.linkedin_publications = linkedin_data.get("publications", [])
        cv.linkedin_patents = linkedin_data.get("patents", [])
        cv.linkedin_organizations = linkedin_data.get("organizations", [])
        cv.linkedin_test_scores = linkedin_data.get("test_scores", [])
        cv.linkedin_recommendations = linkedin_data.get("recommendations", [])
        cv.linkedin_interests = linkedin_data.get("interests", [])

    return cv
