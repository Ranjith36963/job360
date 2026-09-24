"""Parse a LinkedIn 'Save to PDF' profile export to structured career data.

Replaces the older LinkedIn Data Export (ZIP of CSVs) flow. Produces the
exact same output dict schema so downstream code (``enrich_cv_from_linkedin``,
``keyword_generator.generate_search_config``) is unchanged.

Strategy (one layer, deterministic — decision 28, 2026-09-21):
  pdfplumber text extraction + heading-based section split. Covers
  ``headline``, ``summary``, ``skills``, ``industry`` and the Contact block,
  and keeps the FULL text on ``raw_text``.

The prose-heavy sections (Experience, Education, Certifications, honors,
publications…) used to go through a second, LLM pass here. They don't any more:
Job360 has no model of its own. The user's agent reads ``raw_text`` off
``get_profile`` and writes those sections back with ``update_profile``.

All failure modes return the empty-data dict (never raises).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from src.services.profile.models import CVData
from src.utils.loop_guard import cpu_bound

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


@cpu_bound
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


def _is_mid_item_wrap(nxt: str) -> bool:
    """True when ``nxt`` is the rest of a tech list the PDF wrapped mid-item.

    Called only while inside a "Technologies:" run, after the previous line
    did NOT end on a bullet. LinkedIn's PDF wraps by width, so a list can
    break inside an item: "... • Vector" then "Databases • Python". The
    signal is purely STRUCTURAL (rule #28 — no word lists): the next line is
    non-blank, is not a section heading or a new tech line, is not
    bullet-led (that case is already absorbed), and itself carries a list
    bullet — so it is list text, not the next prose line or company name.
    A final item wrapped with no later bullet ("... • Vector" / "Databases")
    carries no bullet to go on; ``_is_width_wrap_tail`` decides that case
    from the line width instead.
    """
    if not nxt or nxt.lower() in _HEADING_SET or _TECH_LINE.match(nxt):
        return False
    if nxt[:1] in {"•", "·", "-"}:
        return False
    return "•" in nxt or "·" in nxt


# Fewer non-blank lines than this and a "document line width" is noise, so
# the width rule stays off (a short pasted snippet has no real line width).
_WRAP_WIDTH_MIN_LINES = 8


def _document_line_width(lines: list[str]) -> int | None:
    """The PDF's own full line width, measured from the text itself: the 95th
    percentile of non-blank line lengths (the max can be an outlier line).
    ``None`` when there are too few lines to measure."""
    lengths = sorted(len(ln.rstrip()) for ln in lines if ln.strip())
    if len(lengths) < _WRAP_WIDTH_MIN_LINES:
        return None
    return lengths[min(len(lengths) - 1, int(len(lengths) * 0.95))]


def _is_width_wrap_tail(last: str, nxt: str, width: int | None) -> bool:
    """True when the tech run's last line was CUT by the PDF mid-item and
    ``nxt`` is the rest of that final item ("... • Vector" / "Databases").

    Structural only (rule #28): ``last`` ends with no bullet or separator and
    runs close to the document's full line width
    (``settings.LINKEDIN_WRAP_WIDTH_RATIO``), AND the next word could not
    have fitted on it. ``nxt`` must be a short fragment: non-blank, not a
    heading, not a new tech line, not bullet-led, carrying no bullet (that is
    the mid-item rule's case) and not itself a full-width line of prose.
    """
    from src.core import settings  # noqa: PLC0415 — read live, per call

    if width is None:
        return False
    last = last.rstrip()
    if not last or last.endswith(("•", "·", "|", ",", "-")):
        return False
    threshold = settings.LINKEDIN_WRAP_WIDTH_RATIO * width
    if len(last) < threshold:
        return False
    if not nxt or nxt.lower() in _HEADING_SET or _TECH_LINE.match(nxt):
        return False
    if nxt[:1] in {"•", "·", "-"} or "•" in nxt or "·" in nxt:
        return False
    if len(nxt) >= threshold:
        return False
    first_word = nxt.split()[0]
    return len(last) + 1 + len(first_word) > width


def _extract_inline_tech_skills(text: str) -> list[str]:
    """Deterministically pull skills from inline 'Technologies: A • B • C' lines
    in the experience body (incl. a wrapped continuation line starting '•').

    LinkedIn lists the tech stack per role on these lines; the section-based
    ``_extract_skills`` only reads the "Top Skills" sidebar, so without this the
    deterministic pass misses Docker/AWS Bedrock/RAG/etc. that are stated outright.
    """
    lines = text.splitlines()
    width = _document_line_width(lines)
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
        # by the previous line ending on a dangling bullet ("OpenAI API •"),
        # by the next line starting with a bullet ("• Python • ..."), or by the
        # PDF breaking the list MID-ITEM ("... • Vector" / "Databases • Python")
        # — see ``_is_mid_item_wrap``. Buffered lines are joined with a space,
        # so a mid-item wrap heals to "Vector Databases" before the split.
        while j < len(lines):
            prev_dangles = buf[-1].rstrip().endswith(("•", "·"))
            nxt = lines[j].strip()
            if prev_dangles or nxt[:1] in {"•", "·", "-"} or _is_mid_item_wrap(nxt):
                buf.append(lines[j])
                j += 1
            else:
                break
        # The run's LAST item cut by the page edge ("... • Vector" then
        # "Databases"): no bullet follows, so only the line width can tell.
        # Joins one fragment and ends the run — never chains further.
        if j < len(lines) and _is_width_wrap_tail(lines[j - 1], lines[j].strip(), width):
            buf.append(lines[j])
            j += 1
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


# NOTE (decision 28, 2026-09-21) — the LinkedIn LLM layer lived here and is
# gone: one system prompt, sixteen section prompts (experience, education,
# certifications, languages, projects, volunteer, courses, honors,
# publications, patents, organizations, test scores, recommendations,
# interests, headline, prose-skills), ``llm_infer_linkedin_skills``,
# ``llm_linkedin_fields``, ``_llm_json`` and the eight ``_coerce_*`` shapers
# that existed only to tidy LLM JSON. Job360 keeps the export's raw text
# (``cv.linkedin_raw_text``) and the structure it can prove; the user's own
# agent reads that text and writes the sections back with ``update_profile``.


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
        # Raw text kept so the user's agent can read the prose (empty here).
        "raw_text": "",
    }


# ── Public async/sync parse API ───────────────────────────────────

async def parse_linkedin_pdf_async(file_path: str) -> dict[str, Any]:
    """Parse a LinkedIn 'Save to PDF' export into the canonical dict schema.

    Returns an empty-data dict on failure (missing pdfplumber, corrupt PDF,
    non-LinkedIn PDF) — never raises.
    """
    # Synchronous pdfplumber work — must not run on the event loop (the
    # LinkedIn upload route already threads its own call; this one did not).
    text = await asyncio.to_thread(_extract_text, file_path)
    if not text or not _looks_like_linkedin(text):
        if text:
            logger.info("PDF at %s does not look like a LinkedIn export; skipping", file_path)
        return _empty_linkedin_data()

    return await parse_linkedin_from_text(text)


def deterministic_linkedin_fields(text: str) -> dict[str, Any]:
    """Read a LinkedIn export — STRUCTURE only. This is the whole parse.

    Splits sections, reads the header (name/headline/industry), the "Top Skills"
    sidebar + inline "Technologies: A • B • C" lines, the Contact block and the
    summary. Section bodies that need semantic parsing (experience, education,
    certifications, honors…) are LEFT ALONE and their text is kept in
    ``raw_text``: since decision 28 they belong to the user's own agent, which
    reads the text off ``get_profile`` and writes those sections back with
    ``update_profile``. No prose skill-term scan (CLAUDE.md rule #28).
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


def merge_linkedin_fields(
    det: dict[str, Any], extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Shape a parsed LinkedIn export into the ONE canonical dict.

    ``det`` is what ``deterministic_linkedin_fields`` read (skills / summary /
    header / contact / raw_text). ``extra`` is an optional second dict of the
    same shape whose section lists are taken as-is — it was the LLM pass's
    output until decision 28 removed that pass, and it stays as the seam for a
    caller that already holds structured sections. Both callers
    (``parse_linkedin_from_text`` and the extraction orchestrator) go through
    here so the merge lives in exactly one place.

    BUILT FROM ``_empty_linkedin_data()``, NOT FROM A HAND-LISTED SET OF KEYS.
    That is the whole point of this shape. This function used to return a
    literal with twelve keys, a THIRD hand-maintained copy of the LinkedIn
    schema. When eight new sections shipped on 2026-08-09 the other copies were
    updated and this one was not, so those eight keys were dropped here — and
    because ``enrich_cv_from_linkedin`` then did
    ``cv.linkedin_honors = data.get("honors", [])``, the shelves were not merely
    left unfilled, they were ASSIGNED EMPTY on every extraction.

    Deriving the key set from the schema means a section added tomorrow flows
    through automatically.

    Ownership: ``det`` (structure) overrides only where it actually parsed
    something; skills are unioned so neither side can clobber the other.
    """
    det = det or {}
    extra = extra or {}

    out = _empty_linkedin_data()
    for key, value in extra.items():
        if key in out:
            out[key] = value
    for key, value in det.items():
        if key in out and value:
            out[key] = value

    skills = list(det.get("skills", []))
    seen = {s.lower() for s in skills}
    for s in extra.get("skills", []):
        if s.lower() not in seen:
            skills.append(s)
            seen.add(s.lower())
    out["skills"] = skills
    out["raw_text"] = det.get("raw_text", "") or extra.get("raw_text", "")
    return out


async def parse_linkedin_from_text(text: str) -> dict[str, Any]:
    """Parse already-extracted LinkedIn text into the canonical dict schema.

    Deterministic all the way through since decision 28 — ``async`` only
    because every caller awaits it and the upload route is async. Returns the
    empty-data dict when the text doesn't look like a LinkedIn export. The full
    text is carried on ``raw_text`` so the user's agent can read the prose
    sections this parser deliberately leaves alone.
    """
    if not text or not _looks_like_linkedin(text):
        return _empty_linkedin_data()

    merged = merge_linkedin_fields(deterministic_linkedin_fields(text))
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

def enrich_cv_from_linkedin(cv: CVData, linkedin_data: dict[str, Any]) -> CVData:
    """Merge LinkedIn data into existing CVData, deduplicating.

    NEVER CLEARS A SHELF. Every assignment below is guarded on the incoming
    value being non-empty, so a parse that did not read a section leaves what
    is already stored alone.

    That guard used to be a ``llm_ran`` flag: the LLM pass owned the prose
    sections, so an empty value was ambiguous — "this profile has no honors" or
    "the cost cache skipped the paid call". Assigning on the second case wiped
    real data (measured 2026-08-08: upload LinkedIn, touch anything else, five
    sections gone permanently). Decision 28 removed the pass and settled the
    ownership instead: the prose sections belong to the USER'S AGENT, which
    writes them with ``update_profile``. Job360's own parse can only ever add
    what it read structurally, never take away what the agent put there.
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
        # A caller may hand this section in as EITHER a list of objects
        # ({"name": ...}) or a bare list of strings — both are reasonable
        # readings of "certifications", and an agent writing through
        # update_profile can send either. The object-only assumption raised
        # AttributeError and aborted the WHOLE LinkedIn merge, so one
        # loosely-shaped section could silently cost a user every LinkedIn
        # field. Accept both shapes.
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

    # Store LinkedIn-specific fields. EVERY ONE IS FILL-IF-PRESENT, never
    # assign-the-empty — see the docstring. The structural half (skills,
    # industry, headline, summary, contact, raw_text) is what this parser
    # actually reads; the prose sections below arrive only when a caller hands
    # them in, and an absent key must leave the agent's own entry standing.
    if new_linkedin_skills:
        cv.linkedin_skills = new_linkedin_skills
    cv.linkedin_industry = linkedin_data.get("industry", "") or cv.linkedin_industry
    # Keep the raw text (when the parser supplied it) — it is what the user's
    # agent reads to fill the prose sections. Only overwrite on a non-empty
    # value so a partial re-enrich never wipes a stored transcript.
    if linkedin_data.get("raw_text"):
        cv.linkedin_raw_text = linkedin_data["raw_text"]

    # Contact is structural (emails/phones/URLs are patterns), so a re-parse
    # always refreshes it — on a non-empty parse only, so a failed read never
    # blanks it.
    if linkedin_data.get("contact"):
        cv.linkedin_contact = dict(linkedin_data["contact"])

    for key in (
        "positions", "languages", "projects", "volunteer", "courses",
        "honors", "publications", "patents", "organizations",
        "test_scores", "recommendations", "interests",
    ):
        value = linkedin_data.get(key)
        if value:
            setattr(cv, f"linkedin_{key}", value)

    return cv
