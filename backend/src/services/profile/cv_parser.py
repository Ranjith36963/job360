"""CV text extraction (PDF/DOCX) and deterministic, structural field reading.

Text extraction uses pdfplumber/python-docx (binary file reading). What we read
off that text is STRUCTURE only — the delimited "Skills" and "Summary" sections,
their list/parenthesis tokens, and font-size section boundaries. Zero hardcoded
patterns, zero domain-specific regex, zero keyword lists (hard rule #28).

Decision 28 (2026-09-21): Job360 has no brain of its own. The CV prompt and its
LLM pass (``llm_cv_fields_from_text``) lived here and are gone. We keep the raw
text; the user's own agent reads it (``get_profile``) and writes the structured
fields back (``update_profile``).
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from src.services.profile.models import CVData
from src.utils.loop_guard import cpu_bound

logger = logging.getLogger("job360.profile.cv_parser")

# ── File reading (infrastructure — not LLM) ─────────────────────


# A PDF whose text layer carries no space glyphs extracts as one long run:
# "SkilledinleveragingPython,SQL,C/C++". Found on the real corpus 2026-08-03 -
# one CV came out at 0.9% spaces where every other sat at 11-13%. pdfplumber's
# WORD-level extraction returns the same result, so this is the FILE, not our
# code: some exporters position glyphs without emitting space characters.
#
# We cannot fix the file, but silently shipping mangled skills is the worst
# option. Detect it, so the user can be told to re-export rather than left with
# a profile full of "Agilemethodologies".
_MIN_SPACE_RATIO = 0.05


def text_is_missing_spaces(raw_text: str) -> bool:
    """True when a PDF's text layer lost its word boundaries."""
    if not raw_text or len(raw_text) < 200:
        return False
    return (raw_text.count(" ") / len(raw_text)) < _MIN_SPACE_RATIO


def extract_text_from_pdf(file_path: str) -> str:
    """Extract text from a PDF file using pdfplumber."""
    try:
        import pdfplumber
    except ImportError:
        logger.error("pdfplumber not installed. Run: pip install pdfplumber")
        return ""

    from src.services.profile.layout import find_column_gutter  # noqa: PLC0415

    text_parts = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                # Read each COLUMN in turn on a two-column page. Reading such a
                # page straight through walks it line by line across the full
                # width, so a sidebar and the main column interleave and fuse —
                # on a real LinkedIn export a line came out as "Pandas
                # (Software) Mathematics Tutor", a skill welded to a job title,
                # and the profile got zero work history. LinkedIn's "Save to
                # PDF" is ALWAYS two-column, so this is every such upload.
                #
                # find_column_gutter returns None for a normal one-column CV,
                # which is the overwhelming majority, and then this is the exact
                # extract_text() call it has always been. Cropping is wrapped
                # because a malformed page can raise inside pdfplumber, and a
                # layout optimisation must never cost someone their upload.
                page_text = None
                try:
                    gutter = find_column_gutter(page.extract_words(), page.width)
                    if gutter:
                        halves = [
                            page.crop((0, 0, gutter, page.height)).extract_text(),
                            page.crop((gutter, 0, page.width, page.height)).extract_text(),
                        ]
                        page_text = "\n".join(h for h in halves if h)
                        logger.info(
                            "two-column page detected in %s - read the columns "
                            "separately (gutter at x=%.0f of %.0f)",
                            file_path, gutter, page.width,
                        )
                except Exception as e:  # noqa: BLE001 - fall back to flat text
                    logger.warning("column split failed for %s (%s); reading flat", file_path, e)
                    page_text = None
                if not page_text:
                    page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
    except Exception as e:
        logger.error("Failed to read PDF %s: %s", file_path, e)
        return ""
    text = "\n".join(text_parts)
    if text_is_missing_spaces(text):
        logger.warning(
            "PDF text layer has no word boundaries (%.1f%% spaces) for %s - "
            "extracted skills will be mangled; this file needs re-exporting",
            text.count(" ") / max(len(text), 1) * 100, file_path,
        )
    return text


def extract_sections_from_pdf(file_path: str) -> dict[str, str] | None:
    """Batch 1.7 — layout-aware PDF section extraction.

    Pulls word-level metadata (``fontname``, ``size``, ``top``, ``x0``)
    from each page and hands it to ``layout.segment_sections_from_words``
    for font-size clustering. Returns ``None`` (not empty-dict) when the
    PDF can't be opened — that lets callers fall back to the flat
    ``extract_text_from_pdf`` path without ambiguity.
    """
    try:
        import pdfplumber
    except ImportError:
        return None

    from src.services.profile.layout import segment_sections_from_words

    all_words: list[dict[str, Any]] = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                try:
                    page_words = page.extract_words(extra_attrs=["fontname", "size"])
                except Exception as e:  # noqa: BLE001
                    logger.debug("extract_words failed on page %d of %s: %s", page_idx, file_path, e)
                    continue
                for w in page_words:
                    w["page"] = page_idx
                all_words.extend(page_words)
    except Exception as e:
        logger.warning("Failed to read PDF for layout extraction %s: %s", file_path, e)
        return None

    if not all_words:
        return None
    return segment_sections_from_words(all_words)


def extract_text_from_docx(file_path: str) -> str:
    """Extract text from a DOCX file using python-docx."""
    try:
        import docx
    except ImportError:
        logger.error("python-docx not installed. Run: pip install python-docx")
        return ""

    try:
        doc = docx.Document(file_path)
        return "\n".join(para.text for para in doc.paragraphs if para.text.strip())
    except Exception as e:
        logger.error("Failed to read DOCX %s: %s", file_path, e)
        return ""


@cpu_bound
def extract_text(file_path: str) -> str:
    """Extract text from PDF or DOCX based on file extension."""
    path = Path(file_path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(file_path)
    elif ext == ".docx":
        return extract_text_from_docx(file_path)
    elif ext == ".doc":
        logger.warning("Legacy .doc format not supported. Please convert to .docx: %s", file_path)
        return ""
    else:
        logger.warning("Unsupported file type: %s", ext)
        return ""


# ── CV deterministic pass (Pass 1) — no LLM, plain text heuristics ──

_DET_SKILL_HEADINGS = {
    "skills", "technical skills", "core skills", "key skills",
    "competencies", "technical competencies", "areas of expertise",
}
_DET_SUMMARY_HEADINGS = {
    "summary", "profile", "professional summary", "about", "about me",
    "objective", "personal statement",
}
# Any heading that ends a section body. Broad on purpose so a skills block
# stops at the next section even when that section isn't one we extract.
_DET_OTHER_HEADINGS = {
    "experience", "work experience", "employment", "education", "projects",
    "certifications", "licenses & certifications", "achievements", "awards",
    "publications", "references", "interests", "languages", "contact",
    "volunteer experience", "courses",
}
_DET_ALL_HEADINGS = _DET_SKILL_HEADINGS | _DET_SUMMARY_HEADINGS | _DET_OTHER_HEADINGS

# Structural STEMS that mark a skills/tools section heading. These are
# section-LABEL word-stems (e.g. "Core Technical Skills", "TOOLS & TECHNOLOGIES",
# "Tech Stack"), NOT a skill vocabulary — so a CV using any heading containing
# one is recognised without hardcoding skill names (CLAUDE.md rule #28).
_DET_SKILL_HEADING_STEMS = (
    "skill", "tool", "technolog", "tech stack", "competenc", "expertise", "proficien",
)


def _is_skill_heading(key: str) -> bool:
    """A short heading line whose label contains a skills/tools section stem.

    SHAPE CHECK ADDED 2026-08-03. The stem test alone matched any line
    CONTAINING a stem, which on the real 7-CV corpus fired on ordinary body
    text and hijacked the section:

        "JSSATE, (Visvesvaraya Technological University) Bangalore, India"
        "Skilledinleveraging Python, SQL, C/C++ and AWS to deliver..."

    Because the collector starts at the FIRST match, one such false positive
    means the real skills block is never reached. That is how an embedded
    engineer with a full "Technical Skills" section extracted ZERO skills.

    A real heading is short and label-shaped: a handful of words, no sentence
    punctuation. Structural, not a vocabulary - rule #28 holds.
    """
    if not key:
        return False
    if not any(stem in key for stem in _DET_SKILL_HEADING_STEMS):
        return False
    # A heading is a LABEL, not a sentence. Real ones: "Technical Skills",
    # "CORE COMPETENCIES", "TOOLS & TECHNOLOGIES".
    if len(key) > 45 or len(key.split()) > 6:
        return False
    # Commas and full stops belong to prose, not to a section label.
    return not any(ch in key for ch in ",.;")


# Last-word markers of a section heading. A short heading whose FINAL word is one
# of these ends the preceding section — so "PROFESSIONAL EXPERIENCE", "WORK
# HISTORY", "TECHNICAL PROJECTS" all stop a skills block even though they aren't
# in the exact heading set. Structural section labels, NOT a skill vocabulary
# (rule #28).
_DET_SECTION_LAST_WORDS = {
    "experience", "employment", "education", "projects", "project",
    "certifications", "certification", "achievements", "awards", "publications",
    "references", "interests", "languages", "contact", "history",
    "qualifications", "training", "courses", "summary", "profile", "objective",
    "volunteering", "accomplishments",
}


def _is_section_heading(key: str) -> bool:
    """True if ``key`` is any recognised section heading — used to stop a captured
    body at the next section. Matches the fixed set, a skills-section stem, OR a
    short heading whose LAST word is a section marker ('professional experience',
    'work history') so non-exact headings still end the block."""
    if not key:
        return False
    if key in _DET_ALL_HEADINGS or _is_skill_heading(key):
        return True
    words = key.split()
    return 1 <= len(words) <= 4 and words[-1] in _DET_SECTION_LAST_WORDS

# Delimiters that separate skills on one line. NOTE: "/" is intentionally NOT a
# delimiter — it binds compound skills ("CI/CD", "AI/ML", "TCP/IP") that must
# stay whole; splitting on it shattered them into meaningless halves.
_DET_SKILL_DELIMS = set(",•·|;")
# Pull out a trailing "(a, b, c)" group: "Python (Pandas, NumPy)" → outer + inner.
_DET_PAREN = re.compile(r"^(.*?)\s*\(([^)]*)\)\s*$")


def _det_split_line(line: str) -> list[str]:
    """Split a skills line on delimiters, but NOT on commas inside parentheses.

    "Python (Pandas, NumPy) • Docker" → ["Python (Pandas, NumPy)", "Docker"]
    so the parenthetical survives for ``_det_expand_token`` to expand.
    """
    toks: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in line:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif depth == 0 and ch in _DET_SKILL_DELIMS:
            toks.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    toks.append("".join(cur))
    return [t for t in toks if t.strip()]


def _det_expand_token(token: str) -> list[str]:
    """Expand a skill token that wraps tools in parentheses.

    "OCR (Tesseract)" → ["OCR", "Tesseract"]
    "Python (Pandas, NumPy, Matplotlib)" → ["Python", "Pandas", "NumPy", "Matplotlib"]
    "Docker" → ["Docker"]
    The outer term and each comma-separated inner term become separate skills,
    so CV lines like "AWS (Bedrock, SageMaker, S3)" surface every tool.
    """
    tok = token.strip()
    if not tok:
        return []
    m = _DET_PAREN.match(tok)
    if not m:
        return [tok]
    out: list[str] = []
    outer = m.group(1).strip()
    if outer:
        out.append(outer)
    for inner in m.group(2).split(","):
        inner = inner.strip()
        if inner:
            out.append(inner)
    return out or [tok]


def _det_heading_key(line: str) -> str:
    """Normalise a line for heading comparison: lowercase, strip, drop a
    trailing colon. Returns '' for lines that aren't heading-shaped."""
    s = line.strip()
    # Prose sentences end with a period — headings don't. Guards against a
    # wrapped summary line ("...within a UK technology organisation.") being
    # mistaken for a skills heading just because it contains a stem ("technolog").
    if s.endswith("."):
        return ""
    t = s.rstrip(":").strip().lower()
    # Real headings are short. Guards against a long sentence being treated
    # as a heading.
    if len(t) > 30:
        return ""
    return t


def _det_collect_section(
    lines: list[str], heading_set: set[str], *, stem_skills: bool = False
) -> list[str]:
    """Return the body lines under the first matching heading, stopping at the
    next recognised section heading. Empty list when absent.

    With ``stem_skills=True`` the start heading is matched STRUCTURALLY — any
    short heading line whose label contains a skills/tools stem (``_is_skill_
    heading``) — so non-standard headings like "Core Technical Skills" or
    "TOOLS & TECHNOLOGIES" are captured, not just the exact ``heading_set``.
    """
    # Collect EVERY matching section, not just the first. A CV may legitimately
    # split its skills across two blocks ("Technical Skills" and "Tools"), and a
    # single early false positive used to swallow the whole result. Taking all
    # of them makes one bad match survivable instead of fatal.
    out: list[str] = []
    capturing = False
    for line in lines:
        key = _det_heading_key(line)
        is_start = key in heading_set or (stem_skills and _is_skill_heading(key))
        if not capturing:
            if is_start:
                capturing = True
            continue
        # A new skills heading continues collecting; any other section ends it.
        if is_start:
            continue
        if _is_section_heading(key):
            capturing = False
            continue
        if line.strip():
            out.append(line.strip())
    return out


# NOTE (CLAUDE.md rule #28): the hardcoded prose skill-term + common-tool
# vocabularies that used to live here were removed. This pass does NOT carry
# skill knowledge; semantic prose→skill recognition belongs to the user's own
# agent (decision 28), never to a keyword list here.


_DET_WRAP_MIN_LEN = 40  # a line shorter than this didn't hit the page margin,
#                         so the next line is a NEW item, not a wrap continuation.


def _det_label_prefix_len(line: str) -> int:
    """Length of a leading 'Category Label:' on a skills line, else 0. Structural
    (a short title-like run before a colon), not a keyword list — rule #28."""
    i = line.find(":")
    if i == -1:
        return 0
    label = line[:i].strip()
    # A label is short and few words ("Cloud & MLOps:", "AI Automation Tools:").
    if label and len(label) <= 30 and len(label.split()) <= 5:
        return i + 1
    return 0


def _det_merge_wrapped_lines(skill_lines: list[str]) -> list[str]:
    """Rebuild logical skill lines from physically-wrapped PDF lines.

    A PDF wraps a long skills line at the page margin, splitting one skill across
    two physical lines ("… • Audio" / "Processing • …") — so "Audio Processing"
    must rejoin. But a genuinely NEW item (a line starting with a bullet, a line
    with its own "Category:" label, or any line following a SHORT line that could
    not have wrapped) must stay separate. Only true margin-wraps are merged.
    """
    logical: list[str] = []
    for raw in skill_lines:
        s = raw.strip()
        if not s:
            continue
        starts_bullet = s[0] in "•·"
        has_label = _det_label_prefix_len(s) > 0
        prev_could_wrap = bool(logical) and len(logical[-1]) >= _DET_WRAP_MIN_LEN
        is_continuation = (
            bool(logical) and not starts_bullet and not has_label and prev_could_wrap
        )
        if is_continuation:
            logical[-1] = f"{logical[-1]} {s}"
        else:
            logical.append(s)
    return logical


# Words that only ever appear in prose, never inside a real skill name. This is
# a GRAMMAR list, not a skill list — it says nothing about any domain, so it
# does not violate CLAUDE.md rule #28 (no hardcoded skill/keyword vocabularies).
# It exists because a CV that states skills in sentences gets its sentences
# split on commas into fake "skills" like "analytics and APIs" or
# "Production Python for data engineering". Measured on the real 7-CV corpus:
# this was the single biggest source of over-extraction.
_DET_PROSE_MARKERS = (" for ", " with ", " to ", " of the ", " in the ", " using ",
                      " across ", " through ", " including ", " such as ")


def _det_is_prose(token: str) -> bool:
    """True when a token reads as a sentence fragment rather than a skill name.

    Three structural signals, none of them domain-specific:
      * it ends in a full stop  -> it was a sentence
      * it contains a prepositional phrase ("X for Y", "X using Y")
      * it starts with a lowercase verb-ish word AND is multi-word
        ("integrating various sensors" vs "asyncio")
    """
    t = token.strip()
    if not t:
        return True
    if t.endswith("."):
        return True
    # A full stop INSIDE a token means two sentences were glued together and
    # then comma-split, producing hybrids like "grounded LLM answers. Containers"
    # — found in the real corpus. Not a skill; the sentence boundary was missed.
    # Guard against real dotted names (Node.js, .NET, asp.net) by requiring the
    # stop to be followed by a space and a capital, i.e. actual sentence shape.
    # Require a real WORD before the stop (3+ letters), so abbreviations like
    # "U.S. GAAP" and "M.Sc. Statistics" survive - those are genuine skills in
    # finance and academia, and an over-eager guard silently deletes them.
    if re.search(r"[A-Za-z]{3}\.\s+[A-Z]", t):
        return True
    low = f" {t.lower()} "
    if any(m in low for m in _DET_PROSE_MARKERS):
        return True
    # "integrating various sensors and actuators" — a gerund opening a
    # multi-word phrase is describing an activity, not naming a skill.
    words = t.split()
    if len(words) >= 3 and words[0].islower() and words[0].endswith("ing"):
        return True
    return False


def _acronym_words(skill: str) -> list[str]:
    """Split a skill into the words an acronym would be built from.

    Hyphens and slashes join words INSIDE a term, so a plain ``.split()`` sees
    "Retrieval-Augmented Generation" as two words and builds "rg" — missing the
    "RAG" sitting right next to it in the same list. Observed in a real profile,
    which showed both "RAG" and "Retrieval-Augmented Generation" as separate
    skills. Structural only (punctuation), no vocabulary — rule #28 safe.
    """
    return [w for w in re.split(r"[\s\-/]+", skill or "") if w]


def _strip_possessive(skill: str) -> str:
    """Turn "LLM'S" into "LLM".

    LinkedIn prose yields possessives and stylised plurals of acronyms, and they
    reach the profile verbatim — a real extraction showed "LLM'S" as a skill. It
    matches no job posting and reads as junk to the person whose profile it is.
    Punctuation-only, so it is profession-agnostic.
    """
    return re.sub(r"['’]s$", "", (skill or "").strip(), flags=re.I)


def _det_collapse_acronyms(skills: list[str]) -> list[str]:
    """Drop an expansion when its own acronym is already present (or vice versa).

    "RAG" + "Retrieval Augmented Generation" is ONE capability listed twice —
    the parenthesis expander produces both. Keeping both inflates the skill
    count without adding capability, which is exactly what makes a profile look
    broad and match everything weakly.

    Structural: we build the initials of each multi-word token and look for an
    existing short token that matches. No vocabulary involved, so this works
    for any profession.
    """
    if len(skills) < 2:
        return skills
    short = {s.lower(): s for s in skills if len(_acronym_words(s)) == 1}
    out: list[str] = []
    dropped: set[str] = set()
    for s in skills:
        words = _acronym_words(s)
        if len(words) >= 2:
            initials = "".join(w[0] for w in words if w and w[0].isalpha()).lower()
            if len(initials) >= 2 and initials in short:
                # Keep the acronym (shorter, and what job ads actually use);
                # drop the long form.
                dropped.add(s.lower())
                continue
        out.append(s)
    return [s for s in out if s.lower() not in dropped]


def deterministic_cv_fields(raw_text: str) -> dict[str, Any]:
    """Read the base CV fields off the text — STRUCTURE only.

    Conservative by design: only the clearly-delimited "Skills" and
    "Summary" sections are read. Returns ``{"skills": [...], "summary": str}``.
    Since decision 28 this is the WHOLE of Job360's own reading of a CV —
    everything semantic (roles, dates, achievements, the skills stated only in
    prose) belongs to the user's agent, which reads ``raw_text`` and writes the
    fields back with ``update_profile``. Re-runs from the stored ``raw_text``,
    so no field ever depends on still having the original file.
    """
    if not raw_text or not raw_text.strip():
        return {"skills": [], "summary": ""}
    lines = raw_text.splitlines()

    skill_lines = _det_collect_section(lines, _DET_SKILL_HEADINGS, stem_skills=True)
    units: list[str] = []
    for logical in _det_merge_wrapped_lines(skill_lines):
        units.extend(_det_split_line(logical))
    skills: list[str] = []
    seen: set[str] = set()
    for token in units:
        # Drop a leading "Category: " label so "Cloud & MLOps: AWS (...)"
        # yields the real skills, not the category name.
        if ":" in token:
            token = token.rsplit(":", 1)[-1]
        for tok in _det_expand_token(token):
            tok = tok.strip()
            # Structural noise guard: real skills are short. A token with
            # >5 words or >45 chars is prose (common on CVs that state skills
            # in sentences, not lists), not a skill — drop it. This keeps
            # precision when stem-matched headings pull in prose bodies.
            if not tok or len(tok) > 45 or len(tok.split()) > 5:
                continue
            if _det_is_prose(tok):
                continue
            if tok.lower() not in seen:
                skills.append(tok)
                seen.add(tok.lower())

    # Collapse acronym/expansion pairs. A skills line reading
    # "RAG (Retrieval Augmented Generation)" expands to BOTH terms, so the same
    # capability is counted twice — and a CV listing many such pairs inflates
    # the count without adding a single new capability. Measured on the real
    # corpus (User_info/, 7 CVs): this was a meaningful share of the two
    # over-extracted profiles. Structural, not a vocabulary: we compare a
    # token's initials to another token, so it works for any domain and needs
    # no keyword list (CLAUDE.md rule #28).
    skills = _det_collapse_acronyms(skills)

    # NOTE: no hardcoded skill-keyword scanning here (CLAUDE.md rule #28).
    # This pass reads STRUCTURE only (the Skills section + its list/parenthesis
    # tokens). Semantic skills stated in prose are the user's AGENT's job — it
    # reads the same raw_text off get_profile and writes them with
    # update_profile (decision 28).
    summary = " ".join(_det_collect_section(lines, _DET_SUMMARY_HEADINGS)).strip()
    return {"skills": skills, "summary": summary}


async def parse_cv_async(file_path: str) -> CVData:
    """Read a CV file into a ``CVData`` — raw text plus the deterministic fields.

    Decision 28 (2026-09-21): this used to hand the text to an LLM provider
    chain and return whatever the model said the CV contained. Job360 no longer
    has a model of its own. It extracts the text (PDF/DOCX), reads the
    STRUCTURE it can prove — the delimited Skills and Summary sections — and
    stops there. The user's own agent reads ``raw_text`` through ``get_profile``
    and writes the structured fields back through ``update_profile``.

    Raises ``RuntimeError`` when no text can be extracted at all (corrupt file,
    scanned image, unsupported format) — that is a real upload failure and the
    caller must surface it.
    """
    # pdfplumber/python-docx are synchronous and CPU-bound; a 2-page PDF was
    # measured stalling the loop 2,399 ms (tests/test_upload_does_not_block_loop.py).
    # The upload ROUTE already used to_thread — this async parser did not.
    raw_text = await asyncio.to_thread(extract_text, file_path)
    if not raw_text:
        raise RuntimeError(
            f"Failed to extract text from {file_path}. "
            "File may be corrupted, empty, or in an unsupported format. "
            "Only PDF and DOCX files are supported."
        )
    return cv_data_from_text(raw_text)


def cv_data_from_text(raw_text: str) -> CVData:
    """Build a ``CVData`` from already-extracted CV text — deterministic only.

    The whole of Job360's own reading of a CV, in one place: keep the text,
    plus the skills and summary the structural pass can prove. Every other
    shelf stays empty until the user's agent fills it.
    """
    det = deterministic_cv_fields(raw_text)
    skills, cv_skills_esco = _maybe_normalise_skills_via_esco(det.get("skills", []))
    return CVData(
        raw_text=raw_text,
        skills=skills,
        cv_skills_esco=cv_skills_esco,
        summary=det.get("summary", ""),
    )


def parse_cv(file_path: str) -> CVData:
    """Synchronous wrapper for parse_cv_async (used by the CLI)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Already in an async context — create a new thread to avoid nested event loop
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(lambda: asyncio.run(parse_cv_async(file_path))).result()
    else:
        return asyncio.run(parse_cv_async(file_path))


def _maybe_normalise_skills_via_esco(
    skills: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Step-1.5 S1.5-D — ESCO-normalise raw skill strings when the
    ``ESCO_SKILL_NORMALISATION_ENABLED`` flag is on AND the ESCO index
    artefacts are on disk. Returns ``(canonical_skills, {label: esco_uri})``.

    Skills with no confident match (cosine < 0.55) pass through unchanged
    and contribute no entry to the URI map. The flag-off / no-data path
    is the identity transform → graceful no-op (CLAUDE.md rule #18).

    The normaliser singleton lazy-loads sentence-transformers + the
    embedding matrix on first call, then caches both for the process
    lifetime — calling per-skill in a loop is intentional and cheap.
    """
    from src.core.settings import (  # noqa: PLC0415 — lazy
        ESCO_SKILL_NORMALISATION_ENABLED,
    )

    if not ESCO_SKILL_NORMALISATION_ENABLED:
        return skills, {}
    try:
        from src.services.profile.skill_normalizer import (  # noqa: PLC0415
            is_available,
            normalize_skill,
        )
    except Exception:
        return skills, {}
    if not is_available():
        return skills, {}

    canonical: list[str] = []
    esco_map: dict[str, str] = {}
    for raw in skills:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            match = normalize_skill(raw)
        except Exception:
            match = None
        if match is not None and match.label:
            canonical.append(match.label)
            esco_map[match.label] = match.uri
        else:
            canonical.append(raw)
    return canonical, esco_map
