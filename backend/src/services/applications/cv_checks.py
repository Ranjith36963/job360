"""Deterministic save-time checks for a saved CV / cover letter (no LLM, never blocks).

Job360 has no model of its own: the assistant writes the CV, we only compare the
text against the user's OWN profile data and look at the rendered PDF. Every
check WARNS (``severity`` "warn") or informs ("info"); none refuses a save.
No skill/keyword list lives here (hard rule #28) - the only vocabulary is the
profile itself.

Borrowed ideas (patterns only, rewritten for this codebase):
  * srbhr/Resume-Matcher (Apache-2.0) apps/backend/app/services/resume_preservation.py -
    number-with-unit regex, date-like numbers excluded, "novel number needs review".
  * srbhr/Resume-Matcher (Apache-2.0) app/services/ats_parse/ - garbled-glyph and
    contact-survives-extraction checks on the rendered PDF.
  * MadsLorentzen/ai-job-search (MIT) tools/verify_layout.py - "final page ends early" measure.
  * Pickle-Pixel/ApplyPilot validator.py (AGPL) - IDEA ONLY (flag leaked assistant
    chatter); the phrase lists below are our own, no code or list copied.
"""

from __future__ import annotations

import io
import re
from typing import Any, Optional

MAX_LISTED = 12  # items named in one detail line

# ── helpers ─────────────────────────────────────────────────────────────────

_URL_RE = re.compile(r"(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|io|uk|dev|org|net|ai|co)(?:/\S*)?", re.I)
_WS_SPLIT_RE = re.compile(r"(\s+)")


def _is_email_token(tok: str) -> bool:
    """A whitespace-free token that looks like ``name@host.tld`` (linear, no regex scan)."""
    local, sep, host = tok.partition("@")
    return bool(sep and local and "." in host.strip(".") and host[:1] not in ".-")


def _drop_emails(text: str) -> str:
    """Blank out email tokens. Token-wise instead of one regex over the whole text, so
    the work is linear in the text size however it is shaped (CodeQL py/polynomial-redos)."""
    return "".join(
        " " if (i % 2 == 0 and _is_email_token(part)) else part for i, part in enumerate(_WS_SPLIT_RE.split(text))
    )


_PHONE_RE = re.compile(r"(?<![\w.])\+?\(?\d[\d\s().-]{7,}\d(?![\w])")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RES = (
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\s+(?:19|20)\d{{2}}\b", re.I),  # "15 October 2026"
    re.compile(rf"\b{_MONTH}\s+(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s+)?(?:19|20)\d{{2}}\b", re.I),
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)?\d{2}\b"),
    re.compile(r"\b\d{1,2}/(?:19|20)\d{2}\b"),
    re.compile(r"\b(?:19|20)\d{2}\s*[-–—]\s*(?:(?:19|20)\d{2}|\d{2})\b"),
)
# Adapted from Resume-Matcher's _NUMBER_RE: optional currency, number, optional unit.
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<cur>[$€£])?(?P<num>(?=(?P<d0>\d+))(?P=d0)(?:,\d+)*(?:\.\d+)?)"
    r"(?:\s*(?P<unit>thousand|million|billion|percent|times|ms|gb|mb|tb|pb|kb|[kmb%x]))?"
    r"(?![A-Za-z\d])",
    re.I,
)


def _core(num: str) -> str:
    """``1,200.0`` -> ``1200``; the comparison key for a number."""
    n = num.replace(",", "")
    if "." in n:
        n = n.rstrip("0").rstrip(".")
    return n


def _scrub(text: str) -> str:
    """Remove what must never count as a claimed number: URLs, emails, phones, dates."""
    t = _drop_emails(text)
    t = _URL_RE.sub(" ", t)
    t = _PHONE_RE.sub(" ", t)
    for rx in _DATE_RES:
        t = rx.sub(" ", t)
    return t


def number_tokens(text: str) -> list[tuple[str, str]]:
    """``(shown, core)`` for each CLAIM-like number: has a unit/currency, or is a bare
    integer >= 10. Years 1950-2100, version-like ``2.1``, list numbering excluded."""
    t = _scrub(text)
    t = re.sub(r"(?m)^\s*\d{1,2}[.)]\s+", " ", t)  # "1. item" list numbering
    out: list[tuple[str, str]] = []
    for m in _NUMBER_RE.finditer(t):
        num, unit, cur = m.group("num"), m.group("unit"), m.group("cur")
        core = _core(num)
        if not unit and not cur:
            if "." in core:
                continue  # 2.1 - version-like
            try:
                value = int(core)
            except ValueError:
                continue
            if 1950 <= value <= 2100 or value < 10:
                continue
        out.append((m.group(0).strip(), core))
    return out


def _all_cores(text: str) -> set[str]:
    return {_core(m.group(0)) for m in re.finditer(r"\d[\d,]*(?:\.\d+)?", text)}


_SIZE_UNITS = frozenset({"kb", "mb", "gb", "tb", "pb"})


def _size_claims(text: str) -> set[tuple[str, str]]:
    """``(core, unit)`` for each data-size number (``2TB``, ``2 TB``) in ``text``. A bare
    ``2`` elsewhere in the profile must not vouch for ``2TB``."""
    t = _scrub(text)
    return {
        (_core(m.group("num")), m.group("unit").lower())
        for m in _NUMBER_RE.finditer(t)
        if (m.group("unit") or "").lower() in _SIZE_UNITS
    }


def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).casefold()


def _flat(value: Any) -> list[str]:
    """Every string inside a profile value (str / list / dict), for the source haystack."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in (_flat(k) + _flat(v))]
    if isinstance(value, (list, tuple, set)):
        return [s for v in value for s in _flat(v)]
    return [str(value)]


_SOURCE_FIELDS = (
    "raw_text",
    "linkedin_raw_text",
    "cv_positions",
    "cv_projects",
    "education",
    "cv_education_details",
    "certifications",
    "skills",
    "job_titles",
    "companies",
    "summary",
    "experience_text",
    "achievements",
    "linkedin_positions",
    "linkedin_skills",
    "linkedin_projects",
    "linkedin_summary",
    "linkedin_headline",
    "linkedin_honors",
    "linkedin_courses",
    "linkedin_publications",
    "github_languages",
    "github_topics",
    "github_frameworks",
    "github_skills_inferred",
    "github_llm_skills",
    "github_repos_brief",
    "github_bio",
    "github_profile_readme",
    "about_me_inferred_skills",
    "cv_languages",
)


def profile_source_text(profile: Any) -> str:
    """Everything the user's OWN profile says: CV + LinkedIn raw text, dated positions,
    projects, education, certifications, skills, GitHub languages/topics/frameworks."""
    cv = getattr(profile, "cv_data", None)
    if cv is None:
        return ""
    parts: list[str] = []
    for name in _SOURCE_FIELDS:
        parts.extend(_flat(getattr(cv, name, None)))
    prefs = getattr(profile, "preferences", None)
    if prefs is not None:
        for name in ("additional_skills", "about_me", "target_job_titles"):
            parts.extend(_flat(getattr(prefs, name, None)))
    return "\n".join(parts)


def _issue(code: str, severity: str, detail: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "detail": detail}


def _listed(items: list[str]) -> str:
    seen: list[str] = []
    for i in items:
        if i not in seen:
            seen.append(i)
    extra = len(seen) - MAX_LISTED
    return ", ".join(seen[:MAX_LISTED]) + (f" (+{extra} more)" if extra > 0 else "")


# ── (a) numbers ─────────────────────────────────────────────────────────────


def _size_unit_missing(shown: str, core: str, have_sizes: set[tuple[str, str]]) -> bool:
    """True when ``shown`` is a data-size claim whose number+unit pair is not in the profile."""
    unit = "".join(ch for ch in shown if ch.isalpha()).lower()
    return unit in _SIZE_UNITS and (core, unit) not in have_sizes


def unsupported_numbers(text: str, source: str) -> Optional[dict[str, str]]:
    """Numbers in ``text`` whose value appears nowhere in the profile source."""
    have = _all_cores(source)
    have_sizes = _size_claims(source)
    bad = [
        shown
        for shown, core in number_tokens(text)
        if core not in have or _size_unit_missing(shown, core, have_sizes)
    ]
    if not bad:
        return None
    return _issue(
        "unsupported_numbers",
        "warn",
        f"Not found anywhere in the profile: {_listed(bad)}. Confirm with the user or remove.",
    )


# ── (b) terms ───────────────────────────────────────────────────────────────

_SKILL_LINE_RE = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z &/+-]{1,30}):\s*(?P<body>\S.*)$")


def _paren_parts(raw: str) -> tuple[list[str], list[str]]:
    """``"a (b) c"`` -> ``(["b"], ["a ", " c"])``. Same result as a
    parenthesis-group regex pair, but linear (find-based), so many open parens stay cheap."""
    inner: list[str] = []
    outer: list[str] = []
    pos = 0
    while True:
        i = raw.find("(", pos)
        j = raw.find(")", i + 1) if i >= 0 else -1
        if j < 0:
            break
        outer.append(raw[pos:i])
        inner.append(raw[i + 1 : j])
        pos = j + 1
    outer.append(raw[pos:])
    return inner, outer


def _split_top(body: str) -> list[str]:
    """Split on ``, ; • |`` only at parenthesis depth 0, so "AWS (Bedrock, S3)" stays whole."""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if depth == 0 and ch in ",;•|":
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    if depth > 0:  # unmatched "(": fall back to the plain split
        return re.split(r"[,;•|]", body)
    return parts


def _split_items(body: str) -> list[str]:
    items: list[str] = []
    for raw in _split_top(body):
        raw = raw.strip(" .\t")
        # "Python (FastAPI, aiohttp)" -> Python, FastAPI, aiohttp
        inner, outer_parts = _paren_parts(raw)
        outer = "".join(outer_parts)
        for piece in [outer] + [p for grp in inner for p in grp.split(",")]:
            piece = piece.replace("(", "").replace(")", "").strip(" .")
            if piece:
                items.append(piece)
    return items


_HEADING_WORD_RE = re.compile(r"[A-Z]+")


def _is_section_heading(line: str) -> bool:
    """SUMMARY / EXPERIENCE / TECHNICAL SKILLS: non-empty, alphabetic words only, all upper-case."""
    s = line.strip(" \t#*:")
    words = [w for w in s.replace("&", " ").replace("/", " ").split() if w]
    return bool(words) and all(_HEADING_WORD_RE.fullmatch(w) for w in words)


def _is_skill_line(line: str) -> bool:
    m = _SKILL_LINE_RE.match(line)
    return bool(m and len(m.group("label").split()) <= 4 and m.group("body").count(",") >= 2)


_HEADER_WINDOW = 6 # a heading ends the header block only within the first N non-empty lines


def _body_lines(text: str) -> list[str]:
    """CV lines after the header block (name / title headline / contact), i.e. from the
    first all-caps section heading on, provided it sits within the first few non-empty
    lines. The very first non-empty line is the name, never a heading. Otherwise nothing
    is skipped."""
    lines = text.splitlines()
    seen = 0
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        seen += 1
        if seen == 1:
            continue
        if seen > _HEADER_WINDOW:
            break
        if _is_section_heading(line):
            kept = [ln for ln in lines[:i] if _is_skill_line(ln)]
            return kept + lines[i:]
    return lines


def terms_to_check(text: str) -> list[str]:
    """Skill-line items ("Label: a, b, c") and project tech after ``|`` - nothing else."""
    out: list[str] = []
    for line in _body_lines(text):
        m = _SKILL_LINE_RE.match(line)
        if m and len(m.group("label").split()) <= 4 and m.group("body").count(",") >= 2:
            out.extend(_split_items(m.group("body")))
            continue
        if " | " in line:
            for seg in line.split("|")[1:]:
                if re.search(r"\d|https?://|@", seg):
                    continue
                if "," in seg:
                    out.extend(_split_items(seg))

    def _plausible(i: str) -> bool:
        return 1 < len(i) <= 40 and len(i.split()) <= 4 and "@" not in i and not re.search(r"\d{4,}", i)

    return [i for i in out if _plausible(i)]


_COMBINE_RE = re.compile(r"\s+(?:and|&)\s+|/")


def _found(part: str, hay: str) -> bool:
    p = _norm(part)
    if not p:
        return False
    if p in hay:
        return True
    head, sep, last = p.rpartition(" ")
    if last.endswith("s") and len(last) > 3:  # APIs -> API
        stem = head + sep + last[:-1]
        return re.search(rf"(?<!\w){re.escape(stem)}(?!\w)", hay) is not None
    return False


def _supported(term: str, hay: str) -> bool:
    if _norm(term) in hay:
        return True
    parts = [p for p in _COMBINE_RE.split(term) if p.strip()]
    return len(parts) > 1 and all(_found(p, hay) for p in parts)


def unsupported_terms(text: str, source: str) -> Optional[dict[str, str]]:
    hay = _norm(source)
    bad = [t for t in terms_to_check(text) if not _supported(t, hay)]
    if not bad:
        return None
    return _issue(
        "unsupported_terms",
        "warn",
        f"Skills/tools in the CV that the profile never mentions: {_listed(bad)}.",
    )


# ── (c) current roles ───────────────────────────────────────────────────────

_PRESENT_RE = re.compile(r"(present|current|now|ongoing)\s*$", re.I)


_DOMAIN_TAIL_RE = re.compile(r"\s+[\w-]+(?:\.[\w-]+)*\.(?:com|io|uk|dev|org|net|ai|co)\s*$", re.I)


def _core_company(name: str) -> str:
    """"Job360 (job360.uk)" -> "Job360": parentheticals dropped, then a trailing domain."""
    inner, outer = _paren_parts(name)
    core = " ".join("".join(outer).split()) if inner else name.strip()
    return _DOMAIN_TAIL_RE.sub("", core).strip(" .,")


def missing_current_roles(text: str, profile: Any) -> Optional[dict[str, str]]:
    cv = getattr(profile, "cv_data", None)
    hay = _norm(text)
    gone: list[str] = []
    for pos in getattr(cv, "cv_positions", None) or []:
        if not isinstance(pos, dict):
            continue
        company = str(pos.get("company") or "").strip()
        if not (company and _PRESENT_RE.search(str(pos.get("dates") or "").strip())):
            continue
        core = _norm(_core_company(company))
        if _norm(company) in hay or (core and core in hay):
            continue
        gone.append(company)
    if not gone:
        return None
    return _issue("missing_current_roles", "warn", f"Current role missing from the CV: {_listed(gone)}.")


# ── (d) leaked chatter ──────────────────────────────────────────────────────

_CHATTER_ANYWHERE = (
    "as requested",
    "i hope this",
    "as an ai",
    "as a language model",
    "revised version",
    "updated version",
    "let me know",
    "feel free to",
    "i've tailored",
    "i have tailored",
    "i've updated",
    "i have updated",
    "happy to adjust",
    "hope this helps",
)
_CHATTER_LINE_START = ("here is", "here's", "here are", "certainly", "sure,", "sure!", "of course", "below is")


def leaked_chatter(text: str) -> Optional[dict[str, str]]:
    low = text.casefold()
    hit = [p for p in _CHATTER_ANYWHERE if re.search(rf"\b{re.escape(p)}", low)]
    for line in low.splitlines():
        s = line.strip(" \t*#>-")
        hit.extend(p for p in _CHATTER_LINE_START if s.startswith(p))
    if not hit:
        return None
    quoted = [f'"{h}"' for h in hit]
    return _issue("leaked_chatter", "warn", f"Looks like assistant chat, not CV text: {_listed(quoted)}.")


# ── (e) cover letter letterhead + date ─────────────────────────────────────

_EMAIL_IN_TEXT_RE = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}")
_LETTER_DATE_RES = (
    re.compile(
        rf"\b(?:\d{{1,2}}(?:st|nd|rd|th)?\s+)?{_MONTH}\s+(?:\d{{1,2}}(?:st|nd|rd|th)?,?\s+)?(?:19|20)\d{{2}}\b",
        re.I,
    ),
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)?\d{2}\b"),
    re.compile(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b"),
)
_LETTER_HEAD_LINES = 8
_LETTER_HEAD_CHARS = 600
_SALUTATION_RE = re.compile(r"(?:dear|to whom|hello|hi)\b", re.I)


def letter_no_header(text: str, profile: Any = None) -> Optional[dict[str, str]]:
    """A cover letter is printed verbatim, so its letterhead and date must be in the
    text. Looks at the first 8 non-blank lines: contact = the profile's email or phone
    digits (``user_info.contact``) when the profile has them, else ANY email address or
    phone number; date = a month-name date or a numeric date. Warn only."""
    lines = [ln for ln in text.splitlines() if ln.strip()][:_LETTER_HEAD_LINES]
    # The letterhead sits ABOVE the salutation; a date or number in the body is not one.
    cut = next((i for i, ln in enumerate(lines) if _SALUTATION_RE.match(ln.lstrip()[:20])), None)
    # Bounded: a letterhead is short, and the regexes below never see a long run.
    head = "\n".join(lines[:cut] if cut is not None else lines[:4])[:_LETTER_HEAD_CHARS]
    contact = getattr(getattr(profile, "user_info", None), "contact", None)
    contact = contact if isinstance(contact, dict) else {}
    email = str(contact.get("email") or "").strip().casefold()
    phone = re.sub(r"\D", "", str(contact.get("phone") or ""))
    if email or phone:
        has_contact = bool(email and email in head.casefold()) or bool(
            phone and phone[-9:] in re.sub(r"\D", "", head)
        )
    else:  # nothing on file to match: any email address or phone number will do
        has_contact = bool(_EMAIL_IN_TEXT_RE.search(head)) or any(
            len(re.sub(r"\D", "", m)) >= 8 and not any(r.fullmatch(m.strip()) for r in _LETTER_DATE_RES)
            for ln in head.splitlines()  # per line: a phone match must never join a date to the next line
            for m in _PHONE_RE.findall(ln)
        )
    has_date = any(r.search(head) for r in _LETTER_DATE_RES)
    if has_contact and has_date:
        return None
    missing = " and ".join(n for n, ok in (("contact line", has_contact), ("date", has_date)) if not ok)
    return _issue(
        "letter_no_header",
        "warn",
        f"The letter's first lines have no {missing}: put name, phone | email | city and the date first.",
    )


# ── entry points ────────────────────────────────────────────────────────────


def check_text(kind: str, text: str, profile: Any) -> list[dict[str, str]]:
    """All text checks for one saved version. ``profile`` may be ``None`` (then only
    chatter runs - an empty source would flag everything)."""
    out: list[Optional[dict[str, str]]] = [leaked_chatter(text)]
    if kind == "cover_letter":
        out.append(letter_no_header(text, profile))
    if profile is not None:
        source = profile_source_text(profile)
        if source.strip():
            out.append(unsupported_numbers(text, source))
            if kind == "cv":
                out.append(unsupported_terms(text, source))
                out.append(missing_current_roles(text, profile))
    return [i for i in out if i]


_CID_RE = re.compile(r"\(cid:\d+\)")


def garbled(text: str) -> bool:
    """True when extracted text carries unmapped-glyph debris - hiring software reads it as noise."""
    return bool(_CID_RE.search(text)) or "�" in text


def check_pdf(pdf_bytes: bytes, cv_text: str, template_pages: Optional[int] = None) -> list[dict[str, str]]:
    """Parse the RENDERED CV back with pdfplumber (lazy import) and report what an ATS would see."""
    try:
        import pdfplumber  # noqa: PLC0415 - heavy, lazy (rule #16)
    except ImportError:  # pragma: no cover
        return []
    out: list[dict[str, str]] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = list(pdf.pages)
            full = "\n".join((p.extract_text() or "") for p in pages)
            n_pages = len(pages)
            last_bottom = last_height = 0.0
            line_h = 12.0
            if pages:
                words = pages[-1].extract_words()
                last_height = float(pages[-1].height)
                if words:
                    last_bottom = max(float(w["bottom"]) for w in words)
                    hs = sorted(float(w["bottom"]) - float(w["top"]) for w in words)
                    line_h = max(8.0, hs[len(hs) // 2] * 1.35)
    except Exception as exc:  # noqa: BLE001 - a corrupt render must never break a save
        return [_issue("pdf_unreadable", "warn", f"The rendered PDF could not be read back ({type(exc).__name__}).")]
    if not full.strip():
        return [_issue("pdf_no_text", "warn", "The rendered PDF has no text layer; hiring software would see nothing.")]
    if garbled(full):
        out.append(
            _issue(
                "pdf_garbled",
                "warn",
                "Unreadable glyphs ((cid:N) or U+FFFD) in the PDF text; some characters will not parse.",
            )
        )
    squashed = re.sub(r"\s+", "", full).casefold()
    digits = re.sub(r"\D", "", full)
    missing: list[str] = []
    _trim = ".,;:()<>\"'"
    cands = (t.strip(_trim) for t in cv_text.split())
    for email in dict.fromkeys(c for c in cands if _is_email_token(c)):
        if re.sub(r"\s+", "", email).casefold() not in squashed:
            missing.append(email)
    for phone in dict.fromkeys(_PHONE_RE.findall(cv_text)):
        d = re.sub(r"\D", "", phone)
        if len(d) >= 8 and d not in digits:
            missing.append(phone.strip())
    if missing:
        out.append(
            _issue(
                "pdf_contact",
                "warn",
                f"Contact details in the CV text are not readable in the PDF: {_listed(missing)}.",
            )
        )
    if template_pages and n_pages > template_pages:
        out.append(
            _issue(
                "pdf_pages",
                "warn",
                f"The PDF is {n_pages} pages; the CV design asks for {template_pages}. Shorten the text.",
            )
        )
    if last_height and last_bottom < 0.70 * last_height:
        room = int(max(0.0, 0.90 * last_height - last_bottom) // line_h)
        if room >= 1:
            out.append(_issue("pdf_space", "info", f"Room for about {room} more lines on the last page."))
    return out
