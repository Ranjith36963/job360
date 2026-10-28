"""Deterministic save-time checks for a saved CV / cover letter / message (no LLM, never blocks).

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
  * kevinastuhuaman/trackly-cli (MIT) lib/application-text.js:6-9 - the stock-phrase
    patterns in :func:`stock_phrases` (see there).
"""

from __future__ import annotations

import io
import re
from typing import Any, Optional

from src.core import settings

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


def profile_source_text(profile: Any, *, include_targets: bool = True) -> str:
    """Everything the user's OWN profile says: CV + LinkedIn raw text, dated positions,
    projects, education, certifications, skills, GitHub languages/topics/frameworks.

    ``include_targets=False`` leaves out ``target_job_titles``: those are roles the user
    WANTS, not roles they held, so they cannot vouch for a CV line (provenance view)."""
    cv = getattr(profile, "cv_data", None)
    if cv is None:
        return ""
    parts: list[str] = []
    for name in _SOURCE_FIELDS:
        parts.extend(_flat(getattr(cv, name, None)))
    prefs = getattr(profile, "preferences", None)
    if prefs is not None:
        names = ("additional_skills", "about_me") + (("target_job_titles",) if include_targets else ())
        for name in names:
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


# ── (c2) entry lines vs the profile's cv_positions ─────────────────────────

# MadsLorentzen/ai-job-search (MIT) .claude/commands/apply.md:169 "Compare every date, employer,
# job title ... in both drafts against the union of ... sources ... reframed emphasis is fine;
# changed facts ... are not", apply.md:117 (the Grounding Audit: "all dates, roles, and metrics
# match exactly") and .claude/agents/application-reviewer.md:11 ("Never propose a skill, employer,
# date ... those files do not contain"). Only the entry lines (``Company | Role | Place | dates``,
# the shape recipes/rules.md asks for) are compared; bullets are reframed emphasis and are not
# checked. The entry-line parser is ours (NOT IN REFERENCE). The only vocabulary is the profile's
# own cv_positions (hard rule #28); no positions means no check (hard rule #29).

_MONTH_NUMBERS = {
    m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)
}
_PRESENT_WORDS = frozenset({"present", "current", "now", "ongoing", "today", "date"})
_LEGAL_SUFFIXES = frozenset(
    {
        "ltd", "limited", "inc", "incorporated", "llc", "llp", "plc", "corp", "corporation",
        "gmbh", "co", "company", "ag", "sa", "bv", "pty", "the",
    }
)
# Every dash kind becomes " - ": a range is then split word by word, with no regex on caller text.
_DASHES = {ord(c): " - " for c in "-\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}
_WORD_RE = re.compile(r"[a-z0-9]+")
_ENTRY_LINE_MAX = 300
_EXPERIENCE_WORDS = ("experience", "employment", "work history", "career history")
_BULLET_STARTS = ("-", "*", "•", "▪", "–", "—")

# One end of a date range: (is_present, (year, month-or-None) or None).
_DatePoint = tuple[bool, Optional[tuple[int, Optional[int]]]]


def _date_point(text: str) -> _DatePoint:
    """One end of a range: present, or (year, month); ``(False, None)`` when unreadable."""
    words = _WORD_RE.findall(text.casefold())
    if any(w in _PRESENT_WORDS for w in words):
        return True, None
    year = next((int(w) for w in words if len(w) == 4 and w.isdigit() and 1950 <= int(w) <= 2100), None)
    if year is None:
        return False, None
    month: Optional[int] = None
    for w in words:
        if w.isalpha() and len(w) >= 3 and w[:3] in _MONTH_NUMBERS:
            month = _MONTH_NUMBERS[w[:3]]
            break
        if w.isdigit() and len(w) <= 2 and 1 <= int(w) <= 12:
            month = int(w)
            break
    return False, (year, month)


def _date_range(text: str) -> Optional[tuple[_DatePoint, _DatePoint]]:
    """Both ends of ``Mar 2023 - Present`` (any dash, or ``to``); ``None`` unless both ends read."""
    if len(text) > _ENTRY_LINE_MAX:
        return None
    words = text.translate(_DASHES).split()
    cuts = [i for i, w in enumerate(words) if w == "-" or w.casefold() == "to"]
    if len(cuts) != 1:
        return None
    parts = [" ".join(words[: cuts[0]]), " ".join(words[cuts[0] + 1 :])]
    if not all(parts):
        return None
    start, end = _date_point(parts[0]), _date_point(parts[1])
    if start[0] or start[1] is None or not (end[0] or end[1] is not None):
        return None
    return start, end


def _point_matches(a: _DatePoint, b: _DatePoint) -> bool:
    if a[0] or b[0]:
        return a[0] and b[0]
    pa, pb = a[1], b[1]
    if pa is None or pb is None or pa[0] != pb[0]:
        return False
    return pa[1] is None or pb[1] is None or pa[1] == pb[1]  # a month only counts when both give one


def _words(text: str) -> list[str]:
    return _WORD_RE.findall(_norm(text))


def _company_words(name: str) -> list[str]:
    words = [w for w in _words(_core_company(name)) if w not in _LEGAL_SUFFIXES]
    return words or _words(name)


def _has_run(hay: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return bool(n) and any(hay[i : i + n] == needle for i in range(len(hay) - n + 1))


def _same_company(a: str, b: str) -> bool:
    wa, wb = _company_words(a), _company_words(b)
    return bool(wa and wb) and (wa == wb or _has_run(wa, wb) or _has_run(wb, wa))


def _same_title(a: str, b: str) -> bool:
    """Generous: the shorter title's words all appear in the longer (``Engineer`` vs ``Senior Engineer``)."""
    sa, sb = set(_words(a)), set(_words(b))
    return bool(sa and sb) and (sa <= sb or sb <= sa)


def _is_heading(line: str) -> bool:
    s = line.strip()
    return bool(s) and "|" not in s and len(s) <= 40 and s == s.upper() and any(c.isalpha() for c in s)


def _experience_lines(text: str) -> list[str]:
    """Lines inside an EXPERIENCE-like section (no such heading: nothing, so nothing is checked)."""
    out: list[str] = []
    inside = False
    for line in text.splitlines():
        if _is_heading(line):
            inside = any(w in line.casefold() for w in _EXPERIENCE_WORDS)
            continue
        if inside and line.strip():
            out.append(line.strip())
    return out


def _entry_parts(line: str) -> Optional[tuple[list[str], str]]:
    """``(non-date parts, date text)`` for an entry line, else ``None``."""
    if line.startswith(_BULLET_STARTS) or "|" not in line or len(line) > _ENTRY_LINE_MAX:
        return None
    parts = [p.strip() for p in line.split("|") if p.strip()]
    for i in range(len(parts) - 1, 0, -1):
        if _date_range(parts[i]) is not None:
            return parts[:i] + parts[i + 1 :], parts[i]
    return None


def _quoted(text: str) -> str:
    return f'"{" ".join(text.split())}"'


def _compare_position(entry: list[str], cv_dates: str, pos: dict[str, Any]) -> list[str]:
    """What differs between one CV entry and one cv_positions record (empty list = same)."""
    diffs: list[str] = []
    cv_range, pos_dates = _date_range(cv_dates), str(pos.get("dates") or "").strip()
    pr = _date_range(pos_dates) if pos_dates else None
    if cv_range and pr and not (_point_matches(cv_range[0], pr[0]) and _point_matches(cv_range[1], pr[1])):
        diffs.append(f"dates: CV says {_quoted(cv_dates)}, profile says {_quoted(pos_dates)}")
    title = str(pos.get("title") or "").strip()
    others = [p for p in entry if not _same_company(p, str(pos.get("company") or ""))]
    if title and others and not any(_same_title(p, title) for p in others):
        diffs.append(f"title: CV says {_quoted(others[0])}, profile says {_quoted(title)}")
    return diffs


def cv_entry_mismatch(text: str, profile: Any) -> Optional[dict[str, str]]:
    """Advisory: a CV entry line whose dates or title differ from the profile's position at the
    same employer, or whose employer is in no cv_positions or linkedin_positions record. Warn only; check_submit does
    not read it. No cv_positions (or none with a company): silent."""
    cv = getattr(profile, "cv_data", None)
    positions = [
        p
        for p in (getattr(cv, "cv_positions", None) or [])
        if isinstance(p, dict) and str(p.get("company") or "").strip()
    ]
    if not positions:
        return None
    linkedin_companies = [
        str(p.get("company") or "").strip()
        for p in (getattr(cv, "linkedin_positions", None) or [])
        if isinstance(p, dict) and str(p.get("company") or "").strip()
    ]
    found: list[str] = []
    for line in _experience_lines(text):
        parsed = _entry_parts(line)
        if parsed is None:
            continue
        entry, cv_dates = parsed
        head = entry[:2]  # the company is the first part (rules.md shape) or, in older CVs, the second
        matched = [p for p in positions if any(_same_company(h, str(p["company"])) for h in head)]
        if not matched:
            # A job that is only on the user's LinkedIn is still theirs: not an unknown employer.
            if not any(_same_company(h, c) for h in head for c in linkedin_companies):
                found.append(f"{_quoted(entry[0])}: employer is in none of the profile's positions")
            continue
        options = [_compare_position(entry, cv_dates, p) for p in matched]
        if all(options):
            best = min(options, key=len)
            name = next(h for h in head if any(_same_company(h, str(p["company"])) for p in matched))
            found.append(f"{_quoted(name)} " + "; ".join(best))
    if not found:
        return None
    return _issue(
        "cv_entry_mismatch",
        "warn",
        f"CV entry lines that do not match the profile: {_listed(found)}. Fix the CV, or ask the user"
        " and update the profile if the CV is right.",
    )


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


# ── (f) writing: stock phrases in a letter, message or answer ───────────────

# kevinastuhuaman/trackly-cli (MIT) lib/application-text.js:6-9 GENERIC_PATTERNS
# (generic_ai_filler, formulaic_contrast), and career-ops (MIT) modes/followup.md:115
# (the four follow-up stock phrases rules.md already bans for employer emails).
# Writing phrases, not skills (hard rule #28 is about profile extraction).
_AI_FILLER_RE = re.compile(r"\b(?:delve|leverage my unique|thrilled to apply|dynamic team|esteemed company)\b", re.I)
_CONTRAST_RE = re.compile(r"\bnot just\b[^.]{0,160}\bbut also\b", re.I)
_STOCK_FOLLOW_UP_RE = re.compile(r"\b(?:just checking in|just following up|touching base|circling back)\b", re.I)
WRITING_KINDS = ("cover_letter", "outreach", "answers")


def stock_phrases(text: str) -> Optional[dict[str, str]]:
    """Phrases that read as generic AI or stock writing. Warn only: the user decides."""
    hits: list[str] = []
    for rx in (_AI_FILLER_RE, _STOCK_FOLLOW_UP_RE):
        hits.extend(f'"{m.group(0)}"' for m in rx.finditer(text))
    if _CONTRAST_RE.search(text):
        hits.append('"not just ... but also"')
    if not hits:
        return None
    return _issue(
        "stock_phrases", "warn",
        f"Reads as stock or AI writing: {_listed(hits)}. Say it plainly in the user's own words.",
    )


def linkedin_note_too_long(text: str, *, premium: bool) -> Optional[dict[str, str]]:
    """A LinkedIn message longer than a connection-request note may be. Warn only:
    a message to an existing connection may be longer, and the user decides.
    career-ops (MIT) modes/contacto.md:168: 200 on a free account, 300 on Premium."""
    limit = settings.LINKEDIN_NOTE_MAX_CHARS_PREMIUM if premium else settings.LINKEDIN_NOTE_MAX_CHARS
    n = len(text)
    if n <= limit:
        return None
    if premium:
        tier = "a Premium account"
        hint = ""
    else:
        tier = "a free account"
        hint = " If the user told you they have Premium, set preferences.linkedin_premium to true (300)."
    return _issue(
        "linkedin_note_too_long", "warn",
        f"{n} characters; a LinkedIn connection-request note on {tier} allows {limit} and longer ones are"
        f" cut off or refused. Shorten it if this is a connection note.{hint}",
    )


# ── entry points ────────────────────────────────────────────────────────────


def check_text(kind: str, text: str, profile: Any) -> list[dict[str, str]]:
    """All text checks for one saved version. ``profile`` may be ``None`` (then only
    chatter runs - an empty source would flag everything)."""
    out: list[Optional[dict[str, str]]] = [leaked_chatter(text)]
    if kind in WRITING_KINDS:
        out.append(stock_phrases(text))
    if kind == "cover_letter":
        out.append(letter_no_header(text, profile))
    if profile is not None:
        source = profile_source_text(profile)
        if source.strip():
            out.append(unsupported_numbers(text, source))
            if kind == "cv":
                out.append(unsupported_terms(text, source))
                out.append(missing_current_roles(text, profile))
                out.append(cv_entry_mismatch(text, profile))
    return [i for i in out if i]


_CID_RE = re.compile(r"\(cid:\d+\)")


def garbled(text: str) -> bool:
    """True when extracted text carries unmapped-glyph debris - hiring software reads it as noise."""
    return bool(_CID_RE.search(text)) or "�" in text



# ── PDF text-layer helpers (dates, reading order, links) ────────────────────

# Mirrors ai-job-search tools/verify_pdf.py:112-116 (NON_ASCII_DASHES): U+2010-2015 and the minus
# sign. Software that splits a date range only on U+002D drops the date (verify_pdf.py:13-16).
_NON_ASCII_DASHES = "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"
_LINE_CAP = 500  # a CV line is short; a longer one is cut before any scan
_OUTER_PUNCT = ".,;:!?()[]{}\"'"


def _is_year(s: str) -> bool:
    return len(s) == 4 and s.isascii() and s.isdigit() and s[:2] in ("19", "20")


def _year_ends(text: str) -> bool:
    """True when ``text`` ends with a standalone year (a digit before it would make it a longer number)."""
    return _is_year(text[-4:]) and not (len(text) > 4 and text[-5].isdigit())


def _year_starts(text: str) -> bool:
    return _is_year(text[:4]) and not text[4:5].isdigit()


def _bare_number(token: str) -> Optional[str]:
    """The token minus outer punctuation when it is a bare number (``1,500``, ``2.0M``, ``40%``), else None.
    A dotted or comma-separated token with a year group (``01.2016``) is a date, not a number."""
    core = token.strip(_OUTER_PUNCT)
    body = core.rstrip("kKmM%") if core[:1].isdigit() else ""
    groups = body.replace(",", ".").split(".")
    if not body or not all(g.isascii() and g.isdigit() for g in groups):
        return None
    if len(groups) > 1 and any(_is_year(g) for g in groups):
        return None
    return core


def _quantity_range(before: str, after: str) -> bool:
    """Both sides of the dash are bare numbers and not a year pair (``2016-2024``, ``2016-17``):
    ``EUR 1500-2000`` is a quantity, not a date. Mirrors verify_pdf.py:130-179 (_quantity_range)."""
    left = _bare_number(before.split()[-1]) if before.split() else None
    right = _bare_number(after.split()[0]) if after.split() else None
    if left is None or right is None:
        return False
    if _is_year(left) and (_is_year(right) or (len(right) == 2 and right.isdigit())):
        return False
    return True


def non_ascii_date_dashes(text: str) -> list[str]:
    """Hits (``'<context>' (U+2013)``) for a year joined to a non-ASCII dash on one line of the RAW text
    layer. Scans character by character, so the work is linear however the text is shaped."""
    hits: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line[:_LINE_CAP]
        for i, ch in enumerate(line):
            if ch not in _NON_ASCII_DASHES:
                continue
            before, after = line[:i].rstrip(), line[i + 1 :].lstrip()
            if not (_year_ends(before) or _year_starts(after)):
                continue
            if _quantity_range(before, after):
                continue
            context = line[max(0, i - 12) : i + 13].strip()
            hits.append(f'"{context}" (U+{ord(ch):04X})')
    return hits


_WORD_RE = re.compile(r"\w+")
_ORDER_PREFIX = 4  # tokens of a CV line matched in the extracted stream
_ORDER_MIN_LINES = 4
_ORDER_MAX_LINES = 400
_MIN_ORDER_FIDELITY = 0.9


def _kendall_fidelity(positions: list[int]) -> float:
    """Kendall tau of the observed positions vs the expected order, mapped to 0..1.
    Mirrors Resume-Matcher ats_parse/roundtrip.py:291-304 (kendall_fidelity)."""
    n = len(positions)
    if n < 2:
        return 1.0
    concordant = discordant = 0
    for i in range(n):
        for j in range(i + 1, n):
            if positions[j] > positions[i]:
                concordant += 1
            elif positions[j] < positions[i]:
                discordant += 1
    return ((concordant - discordant) / (n * (n - 1) / 2) + 1) / 2


def reading_order_fidelity(cv_text: str, extracted: str) -> Optional[float]:
    """How well the order of the CV's lines survives in the PDF text layer (1.0 = same order).
    None when too few lines can be located to judge. Each non-empty CV line is placed by its first
    four word tokens in the extracted token stream (a line that wraps still matches: tokens ignore
    line breaks); a line that cannot be placed in full is skipped, never counted as out of order."""
    doc = _WORD_RE.findall(extracted.casefold())
    index: dict[str, list[int]] = {}
    for pos, tok in enumerate(doc):
        index.setdefault(tok, []).append(pos)
    positions: list[int] = []
    last = -1
    for line in cv_text.splitlines()[: 4 * _ORDER_MAX_LINES]:
        prefix = _WORD_RE.findall(line[:_LINE_CAP].casefold())[:_ORDER_PREFIX]
        if len(prefix) < 2 or len(positions) >= _ORDER_MAX_LINES:
            continue
        found = [
            s
            for s in index.get(prefix[0], [])
            if doc[s : s + len(prefix)] == prefix  # the whole prefix, or the line is not placed
        ]
        if not found:
            continue
        pos = next((s for s in found if s > last), found[0])
        positions.append(pos)
        last = pos
    if len(positions) < _ORDER_MIN_LINES:
        return None
    return _kendall_fidelity(positions)


def _link_tokens(cv_text: str) -> list[str]:
    """http(s):// and www. tokens of the CV text, outer punctuation trimmed."""
    out: list[str] = []
    for tok in cv_text.split():
        t = tok.strip(_OUTER_PUNCT + "<>|")
        if t.casefold().startswith(("http://", "https://", "www.")) and len(t) > 8:
            out.append(t)
    return list(dict.fromkeys(out))


def _link_core(url: str) -> str:
    """The URL without scheme, ``www.`` and a trailing slash, whitespace-free and folded, for a text match."""
    low = url.casefold()
    for prefix in ("https://", "http://"):
        if low.startswith(prefix):
            low = low[len(prefix) :]
            break
    if low.startswith("www."):
        low = low[4:]
    return low.rstrip("/")


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
    # A link carried only by an annotation (target) and not printed is invisible to hiring software
    # (ai-job-search .claude/commands/apply.md:325): its text must be in the layer.
    for url in _link_tokens(cv_text):
        if _link_core(url) not in squashed:
            missing.append(url)
    if missing:
        out.append(
            _issue(
                "pdf_contact",
                "warn",
                f"Contact details or links in the CV text are not readable in the PDF: {_listed(missing)}.",
            )
        )
    dashes = non_ascii_date_dashes(full)
    if dashes:
        out.append(
            _issue(
                "pdf_date_dash",
                "warn",
                f"A year is joined to a long dash in the PDF text: {_listed(dashes)}. Some hiring software"
                " drops a date written that way: use a plain hyphen (Mon YYYY - Mon YYYY).",
            )
        )
    fidelity = reading_order_fidelity(cv_text, full)
    if fidelity is not None and fidelity < _MIN_ORDER_FIDELITY:
        out.append(
            _issue(
                "pdf_reading_order",
                "warn",
                f"The PDF text reads in a different order than the CV text (order match {fidelity:.2f}, below"
                f" {_MIN_ORDER_FIDELITY}). Hiring software may mix up sections; use a single-column layout.",
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
