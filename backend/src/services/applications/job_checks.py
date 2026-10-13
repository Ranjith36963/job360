"""Job checks - deterministic FACTS about the one job the user's assistant brought.

Product rule 4: Job360 never searches, ranks, scores or recommends jobs and has
no LLM. These checks only report what the stored ad literally contains (word
count, link host, dates, a currency amount, the word "sponsor"). They are shown
on that job only and never used to sort or filter a list. Wording is facts, not
verdicts - nothing here says "ghost", "scam" or "risk".

Patterns adapted from open-source projects (MIT):
* word-count completeness bands - Gsync/jobsync src/lib/jobs/descriptionCompleteness.ts
* canonical URL key (host fold, tracking params dropped) - Gsync/jobsync src/lib/jobs/jobDedupe.ts
* official-ATS host match with look-alike defence - MadsLorentzen/ai-job-search
  .claude/commands/apply.md (exact apex or ``.<apex>`` suffix only)
* salary-present regex idea - speedyapply/JobSpy jobspy/util.py (presence only here)
* "facts, not verdicts" wording - career-ops-hq/career-ops company-history.mjs
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.core import settings

# Query keys that only track the click, never pick the job.
_TRACKING_KEYS = frozenset({"gclid", "fbclid", "ref", "trackingid", "refid", "msclkid", "mc_cid", "mc_eid"})

_CUR = r"(?:USD|EUR|GBP|CAD|AUD|CHF|INR|SGD|AED)"
_PAY_RE = re.compile(
    rf"(?:[$€£₹¥]\s?\d|\b{_CUR}\s?\d|\d\s?{_CUR}\b|\d\s?k\s?(?:/|per\s)?\s?(?:yr|year|annum)\b)",
    re.IGNORECASE,
)
_SPONSOR_RE = re.compile(r"\b(?:sponsor\w*|visas?)\b", re.IGNORECASE)


def word_count(text: str) -> int:
    """Whitespace-separated words."""
    return len(text.split()) if text and text.strip() else 0


def canonical_url(url: str) -> str:
    """One stable key for "the same ad link": lower-case host (``www.`` folded),
    ``utm_*`` / gclid / fbclid / ref / trackingId / refId and the fragment dropped,
    remaining query sorted, trailing slash dropped. ``''`` for a blank or
    unparseable link. Compared on read - nothing is stored."""
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return ""
    if not host or parts.scheme.lower() not in ("http", "https"):
        return ""
    host = host.removeprefix("www.")
    kept = sorted(
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_KEYS
    )
    netloc = host + (f":{port}" if port and port not in (80, 443) else "")
    return urlunsplit(("https", netloc, parts.path.rstrip("/"), urlencode(kept), ""))


# A link names ONE job when it carries a job id: a long number, a UUID-like
# token, or a known job-id query key. Board roots and careers home pages do not.
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_JOB_ID_SEGMENT = re.compile(rf"(?:^|[/_-])(?:\d{{4,}}|{_UUID})(?:$|[/_-])", re.I)
_JOB_ID_KEYS = frozenset({"jk", "vjk", "gh_jid", "currentjobid", "jobid", "job_id", "jid", "id", "req", "reqid"})


def is_job_specific_url(url: str) -> bool:
    """True when ``url`` identifies a single job (see the two patterns above)."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return False
    if _JOB_ID_SEGMENT.search(parts.path or ""):
        return True
    return any(k.lower() in _JOB_ID_KEYS and v.strip() for k, v in parse_qsl(parts.query))


def apply_host(url: str) -> str:
    """The real host of ``url`` ('' when it is not an http(s) link). ``urlsplit``
    takes everything after the LAST ``@`` as the host, so
    ``https://greenhouse.io@evil.com/`` reads as ``evil.com``."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return host if parts.scheme.lower() in ("http", "https") else ""


def _is_or_under(host: str, apex: str) -> bool:
    return host == apex or host.endswith("." + apex)


def _is_indeed(host: str) -> bool:
    labels = host.split(".")
    if len(labels) >= 2 and labels[-2] == "indeed" and len(labels[-1]) >= 2:
        return True  # indeed.com, uk.indeed.com, indeed.de
    return len(labels) >= 3 and labels[-3] == "indeed" and labels[-2] in ("co", "com") and len(labels[-1]) == 2


def classify_host(host: str) -> str:
    """``official_ats`` | ``job_board`` | ``unknown``. STRICT: the host must BE
    the vendor domain or end with ``.<domain>``. ``evil-greenhouse.io`` and
    ``greenhouse.io.evil.com`` are ``unknown``."""
    if not host:
        return "unknown"
    if any(_is_or_under(host, a) for a in settings.OFFICIAL_ATS_HOSTS):
        return "official_ats"
    if any(_is_or_under(host, b) for b in settings.JOB_BOARD_HOSTS) or _is_indeed(host):
        return "job_board"
    return "unknown"


def parse_day(value: Optional[str]) -> Optional[date]:
    """``YYYY-MM-DD`` (a longer ISO stamp is cut to its date) or None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def job_checks(
    description: str,
    apply_url: str,
    posted_at: Optional[str],
    employer_published_at: Optional[str],
    today: date,
) -> list[dict[str, Any]]:
    """The checks, each ``{"code", "ok" (bool|None), "reason"}`` plus a ``value``
    (and ``host``) where one helps. ``ok`` None = nothing to say. Never raises."""
    out: list[dict[str, Any]] = []

    words = word_count(description)
    if words < settings.JOB_CHECK_PARTIAL_MIN_WORDS:
        band, why = "title_only", f"{words} words - this is a title or a teaser; fetch the full ad before tailoring."
    elif words < settings.JOB_CHECK_FULL_MIN_WORDS:
        band, why = "partial", f"{words} words - the ad looks cut short; fetch the full ad before tailoring."
    else:
        band, why = "full", f"{words} words - the ad text looks complete."
    out.append({"code": "description_completeness", "ok": band == "full", "reason": why, "value": band})

    host = apply_host(apply_url)
    kind = classify_host(host)
    if not host:
        out.append({"code": "apply_host", "ok": None, "value": "unknown", "host": "",
                    "reason": "No apply link was given."})
    elif kind == "official_ats":
        out.append({"code": "apply_host", "ok": True, "value": kind, "host": host,
                    "reason": f"{host} is a known employer ATS host."})
    elif kind == "job_board":
        out.append({"code": "apply_host", "ok": False, "value": kind, "host": host,
                    "reason": f"{host} is a job board; the employer's own posting is the better source."})
    else:
        out.append({"code": "apply_host", "ok": None, "value": kind, "host": host,
                    "reason": f"{host} is not a known ATS or job board; tell the user which site the link goes to."})

    posted, employer = parse_day(posted_at), parse_day(employer_published_at)
    newest = max((d for d in (posted, employer) if d), default=None)
    if newest is None:
        out.append({"code": "ad_age_days", "ok": None, "value": None, "reason": "No posting date was given."})
    else:
        age = (today - newest).days
        out.append({"code": "ad_age_days", "ok": None, "value": age,
                    "reason": f"Newest date given is {newest.isoformat()}, {age} days ago."})
    if posted and employer:
        gap = abs((posted - employer).days)
        reposted = gap > settings.JOB_CHECK_REPOST_GAP_DAYS
        why = (
            f"The listing says {posted.isoformat()} but the employer's posting says "
            f"{employer.isoformat()} ({gap} days apart): reposted."
            if reposted else f"Listing {posted.isoformat()} and employer {employer.isoformat()} agree."
        )
        out.append({"code": "reposted", "ok": not reposted, "value": reposted, "reason": why})

    pay = bool(_PAY_RE.search(description or ""))
    out.append({"code": "pay_stated", "ok": pay, "value": pay,
                "reason": "The ad text contains an amount of money." if pay
                else "No amount of money appears in the ad text."})
    spon = bool(_SPONSOR_RE.search(description or ""))
    out.append({"code": "sponsorship_mentioned", "ok": spon, "value": spon,
                "reason": "The ad text mentions sponsorship or visas - read what it says." if spon
                else "The ad text does not mention sponsorship or visas."})
    return out


def checks_for_application(app_row: Mapping[str, Any], description: Optional[str] = None) -> list[dict[str, Any]]:
    """Checks for one stored application row (``SELECT *``). ``description``
    overrides the application's snapshot when the caller holds the catalog text."""
    text = description if description is not None else str(app_row.get("job_description_snapshot") or "")
    return job_checks(
        text, str(app_row.get("job_url") or ""),
        app_row.get("ad_posted_at"), app_row.get("employer_published_at"), date.today(),
    )
