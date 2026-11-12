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
CONTACT_BLOCK_LINES = 4  # lines read under a CONTACT heading
ENTRY_MAX_WORDS = 12  # an entry / header line is short; longer is prose
PHONE_DIGITS = (8, 15)

_SUMMARY = frozenset({
    "summary", "professional summary", "career summary", "profile", "professional profile", "personal profile",
    "about me", "about", "objective", "career objective",
})
_PROJECTS = frozenset({"projects", "portfolio projects", "personal projects", "selected projects", "key projects"})
_CONTACT = frozenset({"contact", "contact details", "contact information", "contact me", "personal details"})
_OTHER = frozenset({
    "certifications", "certification", "languages", "awards", "achievements", "publications", "volunteering",
    "interests", "references", "courses", "training", "additional information",
})
_SECTION_OF = {
    **dict.fromkeys(_SUMMARY, "summary"), **dict.fromkeys(_PROJECTS, "projects"),
    **dict.fromkeys(_CONTACT, "contact"), **dict.fromkeys(("work history", "career history"), "experience"),
    **dict.fromkeys(_OTHER, "other"),
}
# Any of these words in a short heading names the section (a heading with two keywords names both).
_KEYWORDS = {
    **dict.fromkeys(("experience", "employment", "internship", "internships"), "experience"),
    **dict.fromkeys(("skills", "skill", "expertise", "technologies", "proficiencies", "competencies"), "skills"),
    **dict.fromkeys(("education", "educational", "academic", "qualifications"), "education"),
}
HEADING_MAX_WORDS = 6
REQUIRED_SECTIONS = ("experience", "skills", "education")

# "May", "March", "April", "June" and "July" are the same word long or short: they carry no style.
_FULL_MONTHS = frozenset({"january", "february", "august", "september", "october", "november", "december"})
_SHORT_MONTHS = frozenset({"jan", "feb", "aug", "sep", "sept", "oct", "nov", "dec"})
_NUMERIC_SEPARATORS = "/-."
_ENTITIES = ("&nbsp;", "&amp;", "&lt;", "&gt;")
_TABLE_CHARS = frozenset("|-:+= ")
_BULLETS = tuple(cv_checks._BULLET_STARTS)


def _section(line: str) -> frozenset[str]:
    """The sections a heading line names; empty for any other line. A heading is short, has no digits or ``|``,
    and is not a bullet: "Experience with dbt in production" (a sentence) is never one."""
    s = line[:MAX_SCAN_CHARS].strip()
    if not s or len(s) > 60 or s.startswith(_BULLETS) or "|" in s or any(c.isdigit() for c in s):
        return frozenset()
    words: list[str] = []
    for w in s.strip("#*_:. \t").replace("&", " and ").replace("/", " and ").casefold().split():
        if w == "and" and words and words[-1] == "and":
            continue
        words.append(w.strip(":,"))
    if len(words) >= 4 and all(len(w) == 1 for w in words):
        words = ["".join(words)]  # "E X P E R I E N C E"
    if len(words) > HEADING_MAX_WORDS:
        return frozenset()
    found = {_KEYWORDS[w] for w in words if w in _KEYWORDS}
    exact = _SECTION_OF.get(" ".join(words))
    if exact:
        found.add(exact)
    return frozenset(found)


def _is_year(tok: str) -> bool:
    return len(tok) == 4 and tok.isdigit() and 1950 <= int(tok) <= 2100


def _month_year(parts: list[str], sep: str) -> bool:
    """``mm/yyyy``, ``mm-yyyy``, ``mm.yyyy``, and ``yyyy/mm``, ``yyyy.mm``. "2010-12" is a year span, never a date."""
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return False
    first, second = parts
    if _is_year(second) and len(first) <= 2:
        return 1 <= int(first) <= 12
    return sep != "-" and _is_year(first) and len(second) == 2 and 1 <= int(second) <= 12


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
            if any(_month_year(c.split(sep), sep) for c in cands for sep in _NUMERIC_SEPARATORS if sep in c):
                found.append("numeric")
    return found


_HTML_TAGS = frozenset({
    "a", "b", "i", "u", "p", "br", "hr", "em", "li", "ul", "ol", "td", "th", "tr", "div", "span", "font", "img",
    "pre", "sub", "sup", "code", "table", "strong", "h1", "h2", "h3", "h4", "h5", "h6",
})
TAB_ROW_MIN = 3  # consecutive lines with 2+ tabs each make a tab-column table


def _html_tag(s: str) -> bool:
    """A real html tag from the closed list: "<5ms", "List<String>", "<name@host>" are not."""
    at = s.find("<")
    while at != -1:
        end = at + 1
        while end < len(s) and s[end].isalnum():
            end += 1
        name = s[at + 1 : end].casefold() or s[at + 1 : at + 2]
        closing = s[at + 1 : at + 2] in ("/", "!")
        if (name in _HTML_TAGS or closing) and not (at and s[at - 1].isalnum()) and s.find(">", end) != -1:
            return True
        at = s.find("<", at + 1)
    return False


_DASH_WORDS = frozenset({"-", "–", "—", "to"})


def _entry_line(line: str) -> bool:
    """A dated header line ("Acme | Engineer | Mar 2019 - Present", "Acme, London, Mar 2019 - Present"): short,
    not a bullet, and shaped by a ``|``, a comma or a dash range. Prose lines never set the CV's date style."""
    words = line[:MAX_SCAN_CHARS].split()
    if not words or len(words) > ENTRY_MAX_WORDS or line.lstrip().startswith(_BULLETS):
        return False
    return "|" in line or "," in line or any(w in _DASH_WORDS for w in words)


def _has_markup(line: str) -> bool:
    s = line[:MAX_SCAN_CHARS]
    t = s.strip()
    if not t:
        return False
    first = t.split(None, 1)[0].strip(",;:.()")
    if "**" in s or "~~" in s or "`" in s or "](" in s or any(e in s for e in _ENTITIES):
        return True
    if len(first) > 4 and first.startswith("__") and first.endswith("__"):  # __bold__ opening a line
        return True
    if t.startswith("#") and t.lstrip("#")[:1] in ("", " "):
        return True
    if "|" in t and (t.startswith("|") or t.endswith("|")) and (t.count("|") >= 2 or set(t) <= _TABLE_CHARS):
        return True
    return _html_tag(s)


def _tab_rows(raw: list[str]) -> list[int]:
    """Line numbers of runs of :data:`TAB_ROW_MIN`+ consecutive lines that each hold 2+ tabs."""
    out: list[int] = []
    run: list[int] = []
    for i, ln in enumerate(raw + [""]):
        if ln[:MAX_SCAN_CHARS].count("\t") >= 2:
            run.append(i + 1)
            continue
        if len(run) >= TAB_ROW_MIN:
            out += run
        run = []
    return out


def _unlabel(tok: str) -> str:
    """"Mobile:07700" -> "07700", "Email:a@b.co" -> "a@b.co"; a URL ("https://x") is left alone."""
    head, colon, rest = tok.partition(":")
    return rest if colon and rest and not rest.startswith("//") and head.isalpha() else tok


def _phone_in(line: str) -> bool:
    """A run of phone-looking tokens ("+44", "7700", "900123") holding 8-15 digits."""
    run_digits = 0
    for raw in line[:MAX_SCAN_CHARS].split() + [""]:
        tok = _unlabel(raw.strip(",;"))
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
    return any(cv_checks._is_email_token(_unlabel(t.strip(",;()<>|[]"))) for t in line[:MAX_SCAN_CHARS].split())


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
    present = set().union(*(sec for _, sec in sections))

    out: list[dict[str, Any]] = []
    missing = [s for s in REQUIRED_SECTIONS if s not in present]
    if missing:
        out.append(_finding("missing_section", detail=",".join(missing)))

    first_heading = next((k for k, (_, sec) in enumerate(sections) if sec), len(numbered))
    header = numbered[:HEADER_LINES]
    for k, (_, sec) in enumerate(sections):  # a CONTACT block anywhere is part of the header
        if "contact" in sec:
            header += numbered[k + 1 : k + 1 + CONTACT_BLOCK_LINES]
    places, links = cv_review.place_words(profile), cv_review.own_links(profile)
    has_headline = "summary" in present or any(
        len(ln.strip()) <= HEADLINE_MAX_CHARS
        and not (_email_in(ln) or _phone_in(ln) or cv_review.is_contact_line(ln[:MAX_SCAN_CHARS], places, links))
        for _, ln in numbered[1 : min(first_heading, HEADER_LINES)]
    )
    if not has_headline:
        out.append(_finding("no_headline"))

    lacking = [name for name, seen in (("email", any(_email_in(ln) for _, ln in header)),
                                       ("phone", any(_phone_in(ln) for _, ln in header))) if not seen]
    if lacking:
        out.append(_finding("contact_incomplete", detail=",".join(lacking)))

    dated: list[tuple[int, list[str]]] = []
    in_summary = False
    for (n, ln), (_, sec) in zip(numbered, sections):
        if sec:
            in_summary = bool(sec & {"summary", "other", "contact"})
            continue
        if not in_summary and _entry_line(ln):
            styles = _date_styles(ln)
            if styles:
                dated.append((n, styles))
    totals = {st: sum(s_.count(st) for _, s_ in dated) for st in ("short", "full", "numeric")}
    majority = ""
    for _, styles in dated:  # most dates; a tie goes to the style seen first
        if not majority or totals[styles[0]] > totals[majority]:
            majority = styles[0]
    odd = [n for n, styles in dated if any(st != majority for st in styles)][:MAX_LINE_NUMBERS]
    if odd:
        out.append(_finding("mixed_date_format", odd))

    marked = sorted({n for n, ln in numbered if _has_markup(ln)} | set(_tab_rows(lines)))
    if marked:
        out.append(_finding("markup_left", marked))

    size = len(text)
    if size > FORMAT_MAX_CHARS:
        out.append(_finding("too_long", detail=f"{size}>{FORMAT_MAX_CHARS}"))
    if size < FORMAT_MIN_CHARS:
        out.append(_finding("too_short", detail=f"{size}<{FORMAT_MIN_CHARS}"))
    return out
