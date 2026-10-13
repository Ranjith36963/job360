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
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"(?<![\w.])\+?\(?\d[\d\s().-]{7,}\d(?![\w])")
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
_DATE_RES = (
    re.compile(rf"\b{_MONTH}\s+(?:\d{{1,2}},?\s+)?(?:19|20)\d{{2}}\b", re.I),
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-](?:19|20)?\d{2}\b"),
    re.compile(r"\b\d{1,2}/(?:19|20)\d{2}\b"),
    re.compile(r"\b(?:19|20)\d{2}\s*[-–—]\s*(?:(?:19|20)\d{2}|\d{2})\b"),
)
# Adapted from Resume-Matcher's _NUMBER_RE: optional currency, number, optional unit.
_NUMBER_RE = re.compile(
    r"(?<![\w.])(?P<cur>[$€£])?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?:\s*(?P<unit>thousand|million|billion|percent|times|ms|gb|mb|tb|[kmb%x]))?"
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
    t = _EMAIL_RE.sub(" ", text)
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
    "raw_text", "linkedin_raw_text", "cv_positions", "cv_projects", "education",
    "cv_education_details", "certifications", "skills", "job_titles", "companies",
    "summary", "experience_text", "achievements", "linkedin_positions", "linkedin_skills",
    "linkedin_projects", "linkedin_summary", "linkedin_headline", "linkedin_honors",
    "linkedin_courses", "linkedin_publications", "github_languages", "github_topics",
    "github_frameworks", "github_skills_inferred", "github_llm_skills", "github_repos_brief",
    "github_bio", "github_profile_readme", "about_me_inferred_skills", "cv_languages",
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


def unsupported_numbers(text: str, source: str) -> Optional[dict[str, str]]:
    """Numbers in ``text`` whose value appears nowhere in the profile source."""
    have = _all_cores(source)
    bad = [shown for shown, core in number_tokens(text) if core not in have]
    if not bad:
        return None
    return _issue(
        "unsupported_numbers", "warn",
        f"Not found anywhere in the profile: {_listed(bad)}. Confirm with the user or remove.",
    )


# ── (b) terms ───────────────────────────────────────────────────────────────

_SKILL_LINE_RE = re.compile(r"^\s*(?P<label>[A-Za-z][A-Za-z &/+-]{1,30}):\s*(?P<body>.+)$")


def _split_items(body: str) -> list[str]:
    items: list[str] = []
    for raw in re.split(r"[,;•|]", body):
        raw = raw.strip(" .\t")
        # "Python (FastAPI, aiohttp)" -> Python, FastAPI, aiohttp
        inner = re.findall(r"\(([^)]*)\)", raw)
        outer = re.sub(r"\([^)]*\)", "", raw).strip(" .")
        for piece in [outer] + [p for grp in inner for p in grp.split(",")]:
            piece = piece.strip(" .")
            if piece:
                items.append(piece)
    return items


def terms_to_check(text: str) -> list[str]:
    """Skill-line items ("Label: a, b, c") and project tech after ``|`` - nothing else."""
    out: list[str] = []
    for line in text.splitlines():
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


def unsupported_terms(text: str, source: str) -> Optional[dict[str, str]]:
    hay = _norm(source)
    bad = [t for t in terms_to_check(text) if _norm(t) not in hay]
    if not bad:
        return None
    return _issue(
        "unsupported_terms", "warn",
        f"Skills/tools in the CV that the profile never mentions: {_listed(bad)}.",
    )


# ── (c) current roles ───────────────────────────────────────────────────────

_PRESENT_RE = re.compile(r"(present|current|now|ongoing)\s*$", re.I)


def missing_current_roles(text: str, profile: Any) -> Optional[dict[str, str]]:
    cv = getattr(profile, "cv_data", None)
    hay = _norm(text)
    gone: list[str] = []
    for pos in getattr(cv, "cv_positions", None) or []:
        if not isinstance(pos, dict):
            continue
        company = str(pos.get("company") or "").strip()
        if company and _PRESENT_RE.search(str(pos.get("dates") or "").strip()) and _norm(company) not in hay:
            gone.append(company)
    if not gone:
        return None
    return _issue("missing_current_roles", "warn", f"Current role missing from the CV: {_listed(gone)}.")


# ── (d) leaked chatter ──────────────────────────────────────────────────────

_CHATTER_ANYWHERE = (
    "as requested", "i hope this", "as an ai", "as a language model", "revised version",
    "updated version", "let me know", "feel free to", "i've tailored", "i have tailored",
    "i've updated", "i have updated", "happy to adjust", "hope this helps",
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


# ── entry points ────────────────────────────────────────────────────────────


def check_text(kind: str, text: str, profile: Any) -> list[dict[str, str]]:
    """All text checks for one saved version. ``profile`` may be ``None`` (then only
    chatter runs - an empty source would flag everything)."""
    out: list[Optional[dict[str, str]]] = [leaked_chatter(text)]
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
        out.append(_issue(
            "pdf_garbled", "warn",
            "Unreadable glyphs ((cid:N) or U+FFFD) in the PDF text; some characters will not "
            "parse."))
    squashed = re.sub(r"\s+", "", full).casefold()
    digits = re.sub(r"\D", "", full)
    missing: list[str] = []
    for email in dict.fromkeys(_EMAIL_RE.findall(cv_text)):
        if re.sub(r"\s+", "", email).casefold() not in squashed:
            missing.append(email)
    for phone in dict.fromkeys(_PHONE_RE.findall(cv_text)):
        d = re.sub(r"\D", "", phone)
        if len(d) >= 8 and d not in digits:
            missing.append(phone.strip())
    if missing:
        out.append(_issue("pdf_contact", "warn", f"Contact details in the CV text are not readable in the PDF: "
            f"{_listed(missing)}."))
    if template_pages and n_pages > template_pages:
        out.append(_issue("pdf_pages", "warn", f"The PDF is {n_pages} pages; the CV design asks for {template_pages}. "
            "Shorten the text."))
    if last_height and last_bottom < 0.70 * last_height:
        room = int(max(0.0, 0.90 * last_height - last_bottom) // line_h)
        if room >= 1:
            out.append(_issue("pdf_space", "info", f"Room for about {room} more lines on the last page."))
    return out
