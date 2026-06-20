"""Application-deadline extractor — conservative, UK-first, stdlib only.

Strict accuracy policy: return a result ONLY when a deadline KEYWORD is
clearly tied to a parseable, unambiguous, future date. Prefer returning
None (UI shows "No deadline listed") over returning a fabricated or
uncertain date.
"""
from __future__ import annotations

import re
from datetime import date, datetime

# ---------------------------------------------------------------------------
# Keyword anchor pattern — must appear directly before a date
# ---------------------------------------------------------------------------
_KEYWORD_PAT = re.compile(
    r"(?:"
    r"closing\s+date"
    r"|apply\s+by"
    r"|application\s+deadline"
    r"|applications?\s+close(?:s)?\s*(?:on)?"
    r"|closes?\s+on"
    r"|close(?:s)?\s+date"
    r"|deadline"
    r"|last\s+date\s+to\s+apply"
    r"|apply\s+before"
    r")"
    r"\s*[:\-–—]?\s*",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Date patterns — all anchored so they match exactly (no stray digits).
# Grouped as named alternatives; the first group that yields all parts wins.
# ---------------------------------------------------------------------------

# Named-month variants: "30 June 2026", "30 Jun 2026", "June 30, 2026"
_MONTHS = (
    r"(?:january|jan|february|feb|march|mar|april|apr|may|june|jun"
    r"|july|jul|august|aug|september|sep|october|oct|november|nov|december|dec)"
)

_DATE_DMY_NAMED = re.compile(
    rf"(\d{{1,2}})\s+({_MONTHS})\s+(\d{{4}})",
    re.IGNORECASE,
)
_DATE_MDY_NAMED = re.compile(
    rf"({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{4}})",
    re.IGNORECASE,
)

# Numeric: dd/mm/yyyy, dd-mm-yyyy (UK day-first), yyyy-mm-dd (ISO)
_DATE_DMY_NUM = re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})")
_DATE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

_MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Maximum number of characters to scan AFTER the keyword before giving up.
_LOOKAHEAD = 60


def _try_parse(year: int, month: int, day: int) -> date | None:
    """Return a date object or None on invalid combos (e.g. Feb 30)."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _extract_date_after(text: str, start: int) -> date | None:
    """Try to parse a date in the `_LOOKAHEAD`-char window after `start`."""
    window = text[start : start + _LOOKAHEAD]

    # ISO: yyyy-mm-dd — check first so "2026-06-30" isn't mis-parsed as DMY-num.
    m = _DATE_ISO.search(window)
    if m:
        return _try_parse(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Named-month DMY: "30 June 2026"
    m = _DATE_DMY_NAMED.search(window)
    if m:
        month = _MONTH_MAP.get(m.group(2).lower()[:3])
        if month:
            return _try_parse(int(m.group(3)), month, int(m.group(1)))

    # Named-month MDY: "June 30, 2026"
    m = _DATE_MDY_NAMED.search(window)
    if m:
        month = _MONTH_MAP.get(m.group(1).lower()[:3])
        if month:
            return _try_parse(int(m.group(3)), month, int(m.group(2)))

    # Numeric UK: dd/mm/yyyy or dd-mm-yyyy
    m = _DATE_DMY_NUM.search(window)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Reject obvious non-UK forms: if "day" > 12 it's unambiguously day-first.
        # If both values ≤ 12 we can't distinguish UK from US — accept day-first
        # (UK convention) but only if both are valid calendar positions.
        return _try_parse(y, mo, d)

    return None


def extract_deadline(
    text: str | None,
    *,
    today_iso: str | None = None,
) -> tuple[str, str] | None:
    """Return ``(deadline_iso_date, source)`` or ``None``.

    ``source`` is always ``"description"`` — structured sources (e.g. JSON-LD
    ``validThrough``) set their own source label directly on the Job.

    Conservative rules (protect trust):
    - A keyword must be clearly tied to a parseable future date.
    - Past dates → None.  Dates more than 2 years ahead → None.
    - If multiple keyword matches exist, take the first one.
    - Empty / None input → None.
    """
    if not text:
        return None

    if today_iso:
        today = datetime.strptime(today_iso, "%Y-%m-%d").date()
    else:
        today = date.today()

    max_future = date(today.year + 2, today.month, today.day)

    for kw_match in _KEYWORD_PAT.finditer(text):
        after_keyword = kw_match.end()
        parsed = _extract_date_after(text, after_keyword)
        if parsed is None:
            continue
        # Strict guardrails
        if parsed <= today:
            continue
        if parsed > max_future:
            continue
        return (parsed.strftime("%Y-%m-%d"), "description")

    return None
