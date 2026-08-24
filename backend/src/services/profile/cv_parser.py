"""CV text extraction (PDF/DOCX) and LLM-powered analysis.

Text extraction uses pdfplumber/python-docx (binary file reading).
All understanding, extraction, and classification is done by LLM.
Zero hardcoded patterns, zero domain-specific regex, zero keyword lists.
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

# ── LLM prompt for CV analysis ──────────────────────────────────

_CV_SYSTEM = """You are an expert CV/resume analyst. You extract ALL professional information from CVs across ANY domain — technology, medical, legal, construction, finance, education, or any other field.

Your job is to extract EVERYTHING a recruiter or job matching engine would need. Miss nothing. Every skill, every achievement, every role, every metric, every certification matters.

You return structured JSON. Nothing else."""

_CV_PROMPT = """Analyze this CV/resume text and extract ALL professional information. Be exhaustive — extract every single skill, technology, tool, methodology, certification, achievement, and qualification mentioned anywhere in the document.

Return a JSON object with exactly these fields:

{{
  "name": "Full name of the candidate",
  "headline": "Their stated role/title from the CV header (e.g., 'AI/ML Engineer | Generative AI Specialist' or 'Cardiology Consultant')",
  "location": "Their location (e.g., 'United Kingdom', 'London')",
  "summary": "Their professional summary paragraph, verbatim from the CV",
  "skills": [
    "Every skill, technology, tool, framework, methodology, domain expertise mentioned ANYWHERE in the CV. Include compound terms like 'AWS Bedrock', 'Docker deployment', 'HIPAA compliance', 'Contract negotiation'. Include soft skills, domain-specific skills, certification topics. Be exhaustive — if they mentioned it, extract it."
  ],
  "experience": [
    {{
      "company": "Company name",
      "title": "Job title/role",
      "dates": "Date range as written",
      "location": "Location if mentioned",
      "bullets": ["Each achievement/responsibility as a separate string"]
    }}
  ],
  "education": [
    {{
      "degree": "Degree name",
      "institution": "University/school name",
      "dates": "Date range",
      "details": ["Coursework, dissertation, projects — each as separate string"]
    }}
  ],
  "projects": [
    {{
      "name": "Project name",
      "description": "What it does and what they built, in their words",
      "technologies": ["Each tool/framework named for THIS project"],
      "dates": "Date range if written"
    }}
  ],
  "certifications": [
    "Each certification with issuer and date, as a single string"
  ],
  "achievements": [
    "Every quantified achievement (percentages, metrics, time improvements, cost savings). Extract the full phrase, e.g., 'achieving 95% response accuracy', 'reducing query latency by 35%'"
  ],
  "experience_level": "One of: intern, junior, mid, senior, lead, principal, director — infer from experience duration and roles",
  "industries": ["Industries/domains they have experience in"],
  "languages": ["Human languages they speak, if mentioned"],
  "right_to_work": "Their stated work-authorisation status, VERBATIM, if the CV says it (e.g. 'British citizen', 'Indefinite Leave to Remain', 'Graduate Route visa until 2027', 'requires sponsorship'). Return null if the CV does not mention it — never infer it from nationality, name or place of study.",
  "career_domain": "The ONE coarse career bucket that best fits this person's OVERALL career. Must be EXACTLY one of: software_engineering, data_and_ai, product_and_design, marketing_and_growth, sales_and_bizdev, finance_and_accounting, operations_and_supply, human_resources, legal_and_compliance, healthcare_and_lifesciences, education_and_research, engineering_physical, customer_support, media_and_content, skilled_trades, other. Return null (not a guess) when the CV genuinely does not fit any bucket clearly."
}}

RULES:
1. Extract every CONCRETE skill — tools, technologies, frameworks, methods, named
   competencies, domains, certifications. When in doubt about a concrete skill, include it
   (a missed skill is a missed job match).
2. Skills should be individual items, not section labels. "Python" not "Programming Languages: Python".
3. For a single compound tool keep it whole: "AWS Bedrock". BUT when a skill lists tools in parentheses, extract the parent AND each tool inside as SEPARATE skills — "AWS (Bedrock, SageMaker, S3, CloudWatch)" → "AWS", "AWS Bedrock", "SageMaker", "S3", "CloudWatch"; "Python (Pandas, NumPy, Matplotlib)" → "Python", "Pandas", "NumPy", "Matplotlib"; "OCR (Tesseract)" → "OCR", "Tesseract".
4. Include achievements with their metrics: "achieving 90% accuracy" not just "90%".
5. If something appears in both the skills section AND experience bullets, include it once in skills.
6. Domain-agnostic: whether it's "TensorFlow" or "HIPAA compliance" or "Contract negotiation" — extract it.
7. Extract named CATEGORIES and ACRONYMS too, not only the products under them. When the CV
   names a category/acronym alongside specific tools (e.g. "EDR Tool: CrowdStrike, Defender"
   or "SIEM, EDR and MDR tools"), extract BOTH the acronym/category ("EDR", "MDR", "SIEM")
   AND each product ("CrowdStrike", "Defender"). Domain initialisms ARE skills.
8. Skills are concrete and transferable. Do NOT turn a vague job DUTY into a skill — skip
   generic activities like "reporting", "teamwork", "collaboration", "documentation",
   "communication" UNLESS stated as a named competency, framework, or methodology.
9. MINE THE PROSE — not just the skills section. A skill counts even when it appears ONLY
   inside a project, an experience bullet, or an education detail and is never listed in the
   skills section. When a sentence shows a technique, architecture, method, model, algorithm,
   or concept being used, built, trained, or applied, extract that technique/method/concept
   ITSELF as a skill (e.g. a bullet describing a system built with a convolutional network
   demonstrates that architecture; a bullet saying transfer learning was applied demonstrates
   that method; a bullet about a trained forecasting model demonstrates that modelling skill).
   This applies to EVERY domain, not just software (e.g. a clinical audit, a litigation
   strategy, a structural load calculation are skills demonstrated in prose). Do NOT limit
   skills to the items already written in the skills section — the strongest signal often
   hides in what the person actually DID.
10. For "career_domain", classify only when the evidence clearly points to ONE bucket from
    the fixed list above. A wrong guess corrupts downstream archetype-aware scoring, so a
    CV that spans multiple domains equally, or doesn't fit any bucket confidently, gets
    null — never invent a value outside the listed set.

CV TEXT:
---
{cv_text}
---"""


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


# ── LLM-powered CV analysis ─────────────────────────────────────

_SECTION_HINT_HEADINGS = (
    "summary",
    "experience",
    "education",
    "skills",
    "certifications",
    "projects",
    "achievements",
)


def _build_section_hint(file_path: str) -> str:
    """Batch 1.7b — pre-segment the PDF via font-size clustering and
    emit a compact hint block the LLM can use as structural guidance.

    Returns an empty string when the file isn't a PDF, pdfplumber
    can't read it, no sections are detected, or no recognised heading
    has a body. On success returns a ``SECTIONS_HINT:\\n[KEY]\\n
    body\\n...`` block suitable for appending to the prompt — the
    main prompt still hands the LLM the full raw text, so this hint
    supplements rather than replaces. Matches plan §4.7's "pre-
    segmented sections reduce ambiguity, do not gate extraction".
    """
    path = Path(file_path)
    if path.suffix.lower() != ".pdf":
        return ""
    sections = extract_sections_from_pdf(file_path)
    if not sections:
        return ""

    parts: list[str] = []
    for key in _SECTION_HINT_HEADINGS:
        body = sections.get(key, "").strip()
        if body:
            # Truncate to keep the hint compact; the main prompt
            # already has the full text, so hints stay brief.
            if len(body) > 1200:
                body = body[:1200] + "…"
            parts.append(f"[{key.upper()}]\n{body}")
    if not parts:
        return ""
    return (
        "\n\nPRE-SEGMENTED SECTIONS (from PDF layout analysis — use as "
        "structural hints; the full raw text above is authoritative):\n" + "\n\n".join(parts)
    )


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
# vocabularies that used to live here were removed. The deterministic pass does
# NOT carry skill knowledge; semantic prose→skill recognition is the LLM's job.


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
    """Pass 1 for the CV — pull base fields from text with NO LLM.

    Conservative by design: only the clearly-delimited "Skills" and
    "Summary" sections are read. Returns ``{"skills": [...], "summary": str}``.
    The LLM pass later enhances this; this pass guarantees *something* lands
    even when no LLM key is configured, and lets the orchestrator re-run on a
    later change from the stored ``raw_text``.
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
    # The deterministic pass reads STRUCTURE only (the Skills section + its
    # list/parenthesis tokens). Semantic skills stated in prose are the LLM
    # pass's job — see llm_cv_fields_from_text.
    summary = " ".join(_det_collect_section(lines, _DET_SUMMARY_HEADINGS)).strip()
    return {"skills": skills, "summary": summary}


async def parse_cv_async(file_path: str) -> CVData:
    """Parse a CV file using LLM analysis. Works for ANY professional domain.

    Batch 1.1 — routes through ``llm_extract_validated`` with
    ``CVSchema`` so LLM output is type-checked at the boundary and
    self-corrected on validation failure (up to 2 retries).

    Batch 1.7b — when the input is a PDF, font-size section
    segmentation supplements the raw text with a pre-segmented hint
    block. The LLM still sees the full raw text; hints just reduce
    ambiguity on multi-column or heading-heavy layouts. Graceful
    no-op for non-PDFs / pdfplumber failures / no-sections cases.

    The untyped ``_llm_result_to_cvdata`` adapter is kept below as a
    fallback for callers that pass pre-fetched dicts OR when strict
    validation fails after all retries (review fix #3).
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

    # PDF-only font-size section hint (needs the file). The LLM extraction
    # itself works purely off text — see ``llm_cv_fields_from_text``.
    #
    # OFFLOADED FOR THE SAME REASON AS `extract_text` THREE LINES UP, and it was
    # missed the first time round: `_build_section_hint` opens the PDF again and
    # pushes every page through `extract_sections_from_pdf`. Leaving it on the
    # loop meant a CV upload could still stall unrelated requests — the exact
    # defect this change exists to remove, surviving in the very next statement
    # after the fix. Offloading one of two blocking calls in a function leaves
    # the function blocking. (CodeRabbit, PR #386.)
    section_hint = await asyncio.to_thread(_build_section_hint, file_path)
    return await llm_cv_fields_from_text(raw_text, section_hint=section_hint)


async def llm_cv_fields_from_text(raw_text: str, section_hint: str = "") -> CVData:
    """Pass 2 for the CV — LLM extraction from raw text only (no file needed).

    Factored out of ``parse_cv_async`` so the two-pass orchestrator can re-run
    the CV LLM pass on a later profile change from the stored ``cv.raw_text``,
    without the original upload. Same validation + graceful-degradation
    contract as the file path (Batch 1.1 / review fix #3).
    """
    from src.services.profile.llm_provider import llm_extract, llm_extract_validated
    from src.services.profile.schemas import CVSchema, cv_schema_to_cvdata

    prompt = _CV_PROMPT.format(cv_text=raw_text) + section_hint

    try:
        schema = await llm_extract_validated(prompt, CVSchema, system=_CV_SYSTEM)
        return cv_schema_to_cvdata(schema, raw_text)
    except RuntimeError as e:
        # Review fix #3 — preserve pre-Batch-1.1 graceful-degradation
        # contract. Validation exhaustion (LLM produced JSON that
        # couldn't be coerced after retries) falls back to the
        # defensive path so callers still get a best-effort CVData.
        # Genuine provider-chain failures (no API keys, all providers
        # down) still raise so operators are alerted.
        msg = str(e).lower()
        if "validation" in msg:
            logger.warning("CVSchema validation exhausted retries; using defensive coercion: %s", e)
            try:
                raw = await llm_extract(prompt, system=_CV_SYSTEM)
                return _llm_result_to_cvdata(raw_text, raw)
            except Exception as e2:  # noqa: BLE001
                logger.warning("Defensive fallback also failed; returning CVData with raw_text only: %s", e2)
                return CVData(raw_text=raw_text)
        logger.error("LLM CV analysis failed: %s", e)
        raise


def parse_cv(file_path: str) -> CVData:
    """Synchronous wrapper for parse_cv_async (used by CLI)."""
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


from src.services.profile._llm_utils import coerce_str as _coerce_str  # noqa: E402
from src.services.profile._llm_utils import coerce_str_list as _coerce_str_list  # noqa: E402


def _maybe_normalise_skills_via_esco(
    skills: list[str],
) -> tuple[list[str], dict[str, str]]:
    """Step-1.5 S1.5-D — ESCO-normalise raw skill strings when the
    ``SEMANTIC_ENABLED`` flag is on AND the ESCO index artefacts are on
    disk. Returns ``(canonical_skills, {canonical_label: esco_uri})``.

    Skills with no confident match (cosine < 0.55) pass through unchanged
    and contribute no entry to the URI map. The flag-off / no-data path
    is the identity transform → graceful no-op (CLAUDE.md rule #18).

    The normaliser singleton lazy-loads sentence-transformers + the
    embedding matrix on first call, then caches both for the process
    lifetime — calling per-skill in a loop is intentional and cheap.
    """
    from src.core.settings import SEMANTIC_ENABLED  # noqa: PLC0415 — lazy

    if not SEMANTIC_ENABLED:
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


def _coerce_career_domain(value: Any) -> str | None:
    """Validate a raw LLM ``career_domain`` string against the same closed
    enum the typed path enforces (``schemas.CareerDomain``), so this untyped
    fallback adapter can't write a bucket the strict path would have
    rejected. Unknown/blank/invalid values become ``None`` (never guess) —
    mirrors ``CVSchema._domain_nullable``.

    Local import, not module-level: ``schemas.py`` imports THIS module at
    module level (for ``_maybe_normalise_skills_via_esco``), so a top-level
    `import schemas` here would cycle. cv_parser stays import-free of
    schemas at module scope; only this function pays the cost, once, on the
    rare defensive-fallback path.
    """
    from src.services.profile.schemas import CareerDomain  # noqa: PLC0415 — see docstring

    if not isinstance(value, str):
        return None
    v = value.strip().lower()
    if v in ("", "unknown", "n/a", "none"):
        return None
    try:
        return CareerDomain(v).value
    except ValueError:
        return None


def _llm_result_to_cvdata(raw_text: str, result: dict[str, Any]) -> CVData:
    """Convert LLM JSON response to CVData dataclass.

    Defensive: all fields are type-guarded so weaker LLMs (Cerebras llama3.1-8b,
    Groq llama-3.3-70b) that deviate from the schema don't crash the parser.
    """
    # Scoring-semantic fields (flow into SearchConfig)
    skills = _coerce_str_list(result.get("skills"))
    # Step-1.5 S1.5-D/E — opt-in ESCO normalisation. No-op when
    # SEMANTIC_ENABLED is false or the ESCO index is missing.
    skills, cv_skills_esco = _maybe_normalise_skills_via_esco(skills)

    # Display-only fields (NOT used in scoring — kept separate to avoid pollution)
    name = _coerce_str(result.get("name"))
    headline = _coerce_str(result.get("headline"))
    location = _coerce_str(result.get("location"))
    achievements = _coerce_str_list(result.get("achievements"))
    # ADAPTER-PARITY FIX (2026-08-07). `cv_schema_to_cvdata` (the live path)
    # has plumbed `industries` / `cv_languages` through since "review fix #1"
    # and now plumbs `career_domain` too — this fallback adapter (only
    # reached when strict CVSchema validation exhausts its retries) silently
    # dropped all three. Same bug shape as `cv_positions` before it: one
    # adapter got the fix, the other didn't. See test_adapter_parity.py.
    industries = _coerce_str_list(result.get("industries"))
    cv_languages = _coerce_str_list(result.get("languages"))
    career_domain = _coerce_career_domain(result.get("career_domain"))

    # Education: flatten nested dicts to list of strings for display
    education_lines: list[str] = []
    # Finding 7 (Pillar-1 closeout audit, 2026-08-16) — MIRRORS
    # schemas.cv_schema_to_cvdata. Per-degree details (dissertation,
    # coursework, course project) used to be appended straight into
    # `education_lines`, mixed in with the degree/institution text where
    # nothing could tell a detail bullet from a qualification line. They get
    # their own shelf now, same as the live adapter — this is exactly the
    # kind of one-adapter-fixed-the-other-wasn't drift `test_adapter_parity`
    # exists to catch.
    education_details: list[str] = []
    edu_raw = result.get("education", [])
    if isinstance(edu_raw, list):
        for edu in edu_raw:
            if isinstance(edu, dict):
                degree = _coerce_str(edu.get("degree"))
                institution = _coerce_str(edu.get("institution"))
                dates = _coerce_str(edu.get("dates"))
                if degree:
                    education_lines.append(degree)
                if institution:
                    line = institution
                    if dates:
                        line += f" | {dates}"
                    education_lines.append(line)
                education_details.extend(_coerce_str_list(edu.get("details")))
            elif isinstance(edu, str):
                education_lines.append(edu)

    # Experience: separate job_titles (roles) from companies — don't overload one field
    job_titles: list[str] = []
    companies: list[str] = []
    experience_lines: list[str] = []
    # STRUCTURED experience (2026-08-06). The prompt above has always asked for
    # {company, title, dates, location, bullets}, but this adapter kept only the
    # flattened title/company lists and discarded dates + location + the
    # title<->company pairing. Nothing downstream could then say which role was
    # held where, for how long, or how recently — the reason the engine has no
    # skill-recency signal. The flat lists stay (the scorer's SearchConfig and
    # every existing consumer read them); this preserves the full record too.
    cv_positions: list[dict[str, Any]] = []
    exp_raw = result.get("experience", [])
    if isinstance(exp_raw, list):
        for exp in exp_raw:
            if isinstance(exp, dict):
                company = _coerce_str(exp.get("company"))
                title = _coerce_str(exp.get("title"))
                bullets = _coerce_str_list(exp.get("bullets"))
                if title:
                    job_titles.append(title)
                if company:
                    companies.append(company)
                for bullet in bullets:
                    experience_lines.append(bullet)
                if title or company:
                    cv_positions.append({
                        "company": company,
                        "title": title,
                        "dates": _coerce_str(exp.get("dates")),
                        "location": _coerce_str(exp.get("location")),
                        "bullets": bullets,
                    })
            elif isinstance(exp, str):
                job_titles.append(exp)

    # Certifications: already type-guarded
    certifications = _coerce_str_list(result.get("certifications"))

    # Summary
    summary = _coerce_str(result.get("summary"))

    return CVData(
        raw_text=raw_text,
        # Scoring-semantic: ONLY clean skills (no name/headline/achievements pollution)
        skills=skills,
        job_titles=job_titles,
        companies=companies,
        education=education_lines,
        certifications=certifications,
        summary=summary,
        experience_text="\n".join(experience_lines),
        cv_positions=cv_positions,
        # Display-only (accessed via CVData.highlights property for CV viewer)
        name=name,
        headline=headline,
        location=location,
        achievements=achievements,
        cv_skills_esco=cv_skills_esco,
        cv_industries=industries,
        cv_languages=cv_languages,
        career_domain=career_domain,
        cv_education_details=education_details,
    )
