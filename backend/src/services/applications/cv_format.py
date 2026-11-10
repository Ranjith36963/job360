"""ATS format checks on a CV's text: the layout rules hiring software (and a recruiter's first glance) rely on.

Pure and offline: ``format_findings(cv_text, profile)`` returns closed-code findings (cv benchmark slice S3);
the caller (``cv_review.verify``) maps any finding to the downgrade ``ats_format``. A finding holds a code, line
NUMBERS (at most 20) and a short detail drawn from closed words and counts, never a word of the CV (the stored
review event holds no CV text, cv_review.py:36).

Rules (no regex on caller text, CodeQL; every scan is word by word and bounded):
- ``missing_section``  no heading for experience, skills or education (detail names them, in that order).
- ``no_headline``      no headline line under the name and no summary/profile section.
- ``contact_incomplete``  no email and/or no phone in the header block.
- ``mixed_date_format``  month dates written in more than one style ("Jan 2024", "January 2024", "01/2024");
                       lines are the entry lines (not bullets) that differ from the first style seen.
                       Bare years are never a style.
- ``markup_left``      markdown or HTML left in the text, table rows, tab columns.
- ``too_long`` / ``too_short``  character count outside the bounds below.
Headings are matched against a closed list of ATS-standard names and their common variants.
"""
from __future__ import annotations

from typing import Any, Optional

from src.services.applications import cv_checks

CODES = (
    "missing_section", "no_headline", "contact_incomplete", "mixed_date_format", "markup_left", "too_long", "too_short",
)
FORMAT_MIN_CHARS = 700  # a CV shorter than this has no body to parse
FORMAT_MAX_CHARS = 9000  # about three dense pages; past it a recruiter stops reading
MAX_LINES = 600  # lines scanned: a CV is ~50 lines, anything past this is not a CV
MAX_SCAN_CHARS = 400  # characters scanned per line (an entry line is 300 at most)
MAX_LINE_NUMBERS = 20
HEADER_LINES = 8  # the header block (name, headline, contact) is the first non-blank lines before a heading
HEADLINE_MAX_CHARS = 120
PHONE_DIGITS = (8, 15)

_EXPERIENCE = frozenset({
    "experience", "work experience", "professional experience", "relevant experience", "employment",
    "employment history", "work history", "career history", "professional background",
})
_SKILLS = frozenset({
    "skills", "technical skills", "core skills", "key skills", "core competencies", "skills and tools",
    "technical skills and tools", "skills and technologies", "technologies",
})
_EDUCATION = frozenset({
    "education", "education and certification", "education and certifications", "education and training",
    "education and qualifications", "academic background", "academic qualifications", "qualifications",
})
_SUMMARY = frozenset({
    "summary", "professional summary", "career summary", "profile", "professional profile", "personal profile",
    "about me", "about", "objective", "career objective",
})
_PROJECTS = frozenset({"projects", "portfolio projects", "personal projects", "selected projects", "key projects"})
_OTHER = frozenset({
    "certifications", "certification", "languages", "awards", "achievements", "publications", "volunteering",
    "interests", "references", "courses", "training", "additional information",
})
_SECTION_OF = {
    **dict.fromkeys(_EXPERIENCE, "experience"), **dict.fromkeys(_SKILLS, "skills"),
    **dict.fromkeys(_EDUCATION, "education"), **dict.fromkeys(_SUMMARY, "summary"),
    **dict.fromkeys(_PROJECTS, "projects"), **dict.fromkeys(_OTHER, "other"),
}
REQUIRED_SECTIONS = ("experience", "skills", "education")

_FULL_MONTHS = frozenset({
    "january", "february", "march", "april", "june", "july", "august", "september", "october", "november", "december",
})
_SHORT_MONTHS = frozenset({"jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"})
_NUMERIC_SEPARATORS = "/-."
_ENTITIES = ("&nbsp;", "&amp;", "&lt;", "&gt;")
_TABLE_CHARS = frozenset("|-:+= ")
_BULLETS = tuple(cv_checks._BULLET_STARTS)


def _section(line: str) -> Optional[str]:
    """The section a heading line names (``experience``, ``skills``...), or ``None`` for any other line."""
    s = line[:MAX_SCAN_CHARS].strip()
    if not s or len(s) > 60:
        return None
    words: list[str] = []
    for w in s.strip("#*_:. \t").replace("&", " and ").replace("/", " and ").casefold().split():
        if w == "and" and words and words[-1] == "and":
            continue
        words.append(w)
    return _SECTION_OF.get(" ".join(words))


def _is_year(tok: str) -> bool:
    return len(tok) == 4 and tok.isdigit() and 1950 <= int(tok) <= 2100


def _month_year(parts: list[str]) -> bool:
    """``a/b`` or ``a-b`` or ``a.b`` as month + year in either order."""
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    first, second = parts
    if _is_year(second) and len(first) <= 2:
        return 1 <= int(first) <= 12
    return _is_year(first) and len(second) == 2 and 1 <= int(second) <= 12


def _date_styles(line: str) -> list[str]:
    """The month-date styles on one line, in order: ``short`` (Jan 2024), ``full`` (January 2024), ``numeric``
    (01/2024, 2024-01). "May" is both spellings and "Present"/bare years carry no style."""
    words = [w.strip(",;()|[]") for w in line[:MAX_SCAN_CHARS].split()]
    found: list[str] = []
    for i, word in enumerate(words):
        low = word.casefold().rstrip(".")
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if _is_year(nxt) and low in _FULL_MONTHS:
            found.append("full")
        elif _is_year(nxt) and low in _SHORT_MONTHS:
            found.append("short")
        else:
            cands = [word] + (word.split("-") if "/" in word and word.count("-") == 1 else [])
            if any(_month_year(c.split(sep)) for c in cands for sep in _NUMERIC_SEPARATORS if sep in c):
                found.append("numeric")
    return found


def _has_markup(line: str) -> bool:
    s = line[:MAX_SCAN_CHARS]
    t = s.strip()
    if not t:
        return False
    if "**" in s or "__" in s or "~~" in s or "`" in s or "](" in s or s.count("\t") >= 2:
        return True
    if any(e in s for e in _ENTITIES):
        return True
    if t.startswith("#") and t.lstrip("#")[:1] in ("", " "):
        return True
    if "|" in t and (t.startswith("|") or t.endswith("|")) and (t.count("|") >= 2 or set(t) <= _TABLE_CHARS):
        return True
    at = s.find("<")
    while at != -1:  # an html tag: "<" + a letter, "/" or "!" and a later ">" ("<5 years" is not one)
        if s[at + 1 : at + 2].isalpha() or s[at + 1 : at + 2] in ("/", "!"):
            if s.find(">", at + 2) != -1:
                return True
        at = s.find("<", at + 1)
    return False


def _phone_in(line: str) -> bool:
    """A run of phone-looking tokens ("+44", "7700", "900123") holding 8-15 digits."""
    run_digits = 0
    for raw in line[:MAX_SCAN_CHARS].split() + [""]:
        tok = raw.strip(",;")
        if tok and set(tok) <= set("0123456789+()-."):
            run_digits += sum(c.isdigit() for c in tok)
            if run_digits > PHONE_DIGITS[1]:
                run_digits = PHONE_DIGITS[1] + 1  # too long to be a number; keep scanning for the next run
            continue
        if PHONE_DIGITS[0] <= run_digits <= PHONE_DIGITS[1]:
            return True
        run_digits = 0
    return False


def _email_in(line: str) -> bool:
    return any(cv_checks._is_email_token(t.strip(",;()<>|[]")) for t in line[:MAX_SCAN_CHARS].split())


def _finding(code: str, lines: Optional[list[int]] = None, detail: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"code": code}
    if lines:
        out["lines"] = lines[:MAX_LINE_NUMBERS]
    if detail:
        out["detail"] = detail
    return out


def format_findings(cv_text: str, profile: Any = None) -> list[dict[str, Any]]:
    """Format findings for a CV text, in :data:`CODES` order; ``[]`` when the CV is ATS-ready.

    ``profile`` only teaches the contact-line reader the owner's own place and site words (headline detection)."""
    from src.services.applications import cv_review  # noqa: PLC0415 - cv_review imports this module's caller

    text = cv_text if isinstance(cv_text, str) else ""
    lines = text[: MAX_LINES * MAX_SCAN_CHARS].splitlines()[:MAX_LINES]
    numbered = [(i + 1, ln) for i, ln in enumerate(lines) if ln.strip()]
    sections = [(n, _section(ln)) for n, ln in numbered]
    present = {s for _, s in sections if s}

    out: list[dict[str, Any]] = []
    missing = [s for s in REQUIRED_SECTIONS if s not in present]
    if missing:
        out.append(_finding("missing_section", detail=",".join(missing)))

    first_heading = next((k for k, (_, s) in enumerate(sections) if s), len(numbered))
    header = numbered[: min(first_heading, HEADER_LINES)]
    places, links = cv_review.place_words(profile), cv_review.own_links(profile)
    has_headline = "summary" in present or any(
        len(ln.strip()) <= HEADLINE_MAX_CHARS
        and not (_email_in(ln) or _phone_in(ln) or cv_review.is_contact_line(ln[:MAX_SCAN_CHARS], places, links))
        for _, ln in header[1:]
    )
    if not has_headline:
        out.append(_finding("no_headline"))

    lacking = [name for name, seen in (("email", any(_email_in(ln) for _, ln in header)),
                                       ("phone", any(_phone_in(ln) for _, ln in header))) if not seen]
    if lacking:
        out.append(_finding("contact_incomplete", detail=",".join(lacking)))

    first_style = ""
    odd: list[int] = []
    for n, ln in numbered:
        styles = [] if ln.lstrip().startswith(_BULLETS) else _date_styles(ln)  # a bullet's date is prose, not an entry
        if styles and not first_style:
            first_style = styles[0]
        if first_style and any(s != first_style for s in styles) and len(odd) < MAX_LINE_NUMBERS:
            odd.append(n)
    if odd:
        out.append(_finding("mixed_date_format", odd))

    marked = [n for n, ln in numbered if _has_markup(ln)]
    if marked:
        out.append(_finding("markup_left", marked))

    size = len(text)
    if size > FORMAT_MAX_CHARS:
        out.append(_finding("too_long", detail=f"{size}>{FORMAT_MAX_CHARS}"))
    if size < FORMAT_MIN_CHARS:
        out.append(_finding("too_short", detail=f"{size}<{FORMAT_MIN_CHARS}"))
    return out
