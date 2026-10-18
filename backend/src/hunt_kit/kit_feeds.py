"""Open job feeds for the Job360 hunt kit.

Each ``fetch_*`` function has the signature::

    fetch_x(queries, http, stats, *, days, countries) -> list[Job]

It never raises (any failure returns ``[]``) and bumps ``stats["tried"|"ok"|"jobs"]``.
Networking goes ONLY through the ``http`` callable the core passes in
(``http(url, *, method, data, headers, as_json) -> (status, body)``). Importing this
module does no network and no file IO. Standard library only.

Nothing here ranks or scores. ``queries`` is used only as a plain keyword match for
feeds that cannot search server-side. Pages are capped at ``MAX_PAGES`` per feed per
query. No logins, no cookies.

Feeds: Hacker News "Who is hiring", Jobicy, Working Nomads, We Work Remotely (RSS),
The Muse, Arbeitnow, Himalayas, Remotive, RemoteOK, plus the keyed Adzuna and Jooble
(env ``ADZUNA_APP_ID`` / ``ADZUNA_APP_KEY`` and ``JOOBLE_KEY``; both return [] when
the key is missing).
"""
from __future__ import annotations

import functools
import html as _html
import json
import os
import re

try:  # shared safe-XML helper (same folder)
    from kit_xml import safe_fromstring  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: find the sibling file
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _spec = _ilu.spec_from_file_location("kit_xml", _Path(__file__).with_name("kit_xml.py"))
    if _spec is None or _spec.loader is None:
        raise ImportError("kit_xml.py not found next to this file")
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    safe_fromstring = _mod.safe_fromstring

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import quote, urlencode

Job = dict[str, Any]
Stats = dict[str, Any]
HttpFn = Callable[..., "tuple[int, Any]"]

MAX_PAGES = 5          # hard cap: pages per feed per query
MAX_QUERIES = 5        # hard cap: queries sent to a feed that searches server-side

# Adzuna's documented country sites (path segment of the API URL).
ADZUNA_COUNTRIES = frozenset(
    "at au be br ca ch de es fr gb in it mx nl nz pl sg us za".split()
)

# ISO2 -> English name, only used to build the free-text "location" Jooble expects.
_COUNTRY_NAMES = {
    "AE": "United Arab Emirates", "AT": "Austria", "AU": "Australia", "BE": "Belgium",
    "BR": "Brazil", "CA": "Canada", "CH": "Switzerland", "DE": "Germany", "DK": "Denmark",
    "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "United Kingdom", "IE": "Ireland",
    "IN": "India", "IT": "Italy", "MX": "Mexico", "NL": "Netherlands", "NO": "Norway",
    "NZ": "New Zealand", "PL": "Poland", "PT": "Portugal", "SE": "Sweden", "SG": "Singapore",
    "US": "United States", "ZA": "South Africa",
}

_UA_NOTE = "hunt-kit"


# --------------------------------------------------------------------------- helpers
def strip_html(text: str | None) -> str:
    """Turn an HTML fragment into plain text (block tags become newlines)."""
    if not text:
        return ""
    s = str(text)
    s = re.sub(r"(?is)<(script|style)\b.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)<p\b[^>]*>", "\n\n", s)
    s = re.sub(r"(?i)</(p|div|h[1-6]|ul|ol|tr)\s*>", "\n", s)
    s = re.sub(r"(?i)<li\b[^>]*>", "\n- ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = _html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r" ?\n ?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _date(value: Any) -> str:
    """Best-effort 'YYYY-MM-DD' from epoch seconds, ISO text or RFC-822 text; '' if unknown."""
    if value in (None, "", 0):
        return ""
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime("%Y-%m-%d")
        s = str(value).strip()
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):
            return s[:10]
        return parsedate_to_datetime(s).astimezone(timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def _job(
    *, title: Any, company: Any, url: Any, location: Any = "", posted: str = "",
    description: Any = "", source: str, board: str = "", remote: bool | None = None,
    visa_flag: bool | None = None, extra: dict[str, Any] | None = None,
) -> Job:
    """Build a Job dict with every contract key present."""
    return {
        "title": str(title or "").strip(),
        "company": str(company or "").strip(),
        "url": str(url or "").strip(),
        "location": str(location or "").strip(),
        "posted": posted,
        "description": strip_html(description) if description else "",
        "source": source,
        "board": board,
        "remote": remote,
        "visa_flag": visa_flag,
        "extra": extra or {},
    }


def _matches(job: Job, queries: list[str]) -> bool:
    """Plain keyword match (NOT ranking): any query whose every word occurs in the job text."""
    qs = [q.strip().lower() for q in (queries or []) if q and q.strip()]
    if not qs:
        return True
    hay = " ".join((job["title"], job["company"], job["location"], job["description"],
                    " ".join(str(t) for t in job["extra"].get("tags", [])))).lower()
    return any(all(w in hay for w in q.split()) for q in qs)


def _recent(job: Job, days: int) -> bool:
    """True if the posting date is within ``days`` (jobs with unknown date are kept)."""
    if not days or days <= 0 or not job["posted"]:
        return True
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    return bool(job["posted"] >= cutoff)


def _keep(jobs: list[Job], queries: list[str], days: int, *, search: bool = True) -> list[Job]:
    """Drop jobs without title/url, too old, or (when ``search``) not matching any query."""
    return [j for j in jobs if j["title"] and j["url"] and _recent(j, days)
            and (not search or _matches(j, queries))]


def _get(http: HttpFn, stats: Stats, url: str, **kw: Any) -> Any:
    """One request through the core's http helper; returns body on HTTP 200, else None."""
    stats["tried"] = stats.get("tried", 0) + 1
    status, body = http(url, **kw)
    if status == 200 and body is not None:
        stats["ok"] = stats.get("ok", 0) + 1
        return body
    return None


def _queries(queries: list[str]) -> list[str]:
    """Distinct non-empty queries, capped at MAX_QUERIES."""
    seen: list[str] = []
    for q in queries or []:
        q = (q or "").strip()
        if q and q.lower() not in [s.lower() for s in seen]:
            seen.append(q)
    return seen[:MAX_QUERIES]


def _gated(countries: list[str], code: str) -> bool:
    """A national service runs for its country, or everywhere when the core passes "*" (--visa-onsite)."""
    have = {c.upper() for c in countries}
    return code in have or "*" in have


def _feed(fn: Callable[..., list[Job]]) -> Callable[..., list[Job]]:
    """Wrap a fetcher: never raise, always count ``stats['jobs']``."""
    @functools.wraps(fn)
    def wrapper(queries: list[str], http: HttpFn, stats: Stats, *, days: int = 30,
                countries: list[str] | None = None) -> list[Job]:
        for k in ("tried", "ok", "jobs"):
            stats.setdefault(k, 0)
        try:
            jobs = fn(list(queries or []), http, stats, days=days, countries=list(countries or []))
        except Exception:  # noqa: BLE001 - contract: feeds never raise
            jobs = []
        stats["jobs"] += len(jobs)
        return jobs
    return wrapper


# --------------------------------------------------------------- Hacker News: who is hiring
_HN_ROLE = re.compile(
    r"engineer|developer|scientist|designer|manager|architect|analyst|devops|sre|"
    r"researcher|lead|director|founder|swe|intern|programmer|administrator|specialist|"
    r"product|support|recruiter|consultant", re.I)
_HN_NOISE = re.compile(
    r"^(full[- ]?time|part[- ]?time|contract(or)?|intern(ship)?|permanent|visa|equity|"
    r"hiring|apply|\$|€|£|\d+k)", re.I)
_VISA_NO = re.compile(
    r"(no|not|unable to|cannot|can't|can not|don't|do not|won't|will not)\W+(\w+\W+){0,3}"
    r"(visa|sponsor)|visa\W+(sponsorship\W+)?(is\W+)?not\W+(available|offered|provided)", re.I)
_VISA_YES = re.compile(r"visa\W+sponsor|sponsor\w*\W+(\w+\W+){0,2}visa|will sponsor|can sponsor|"
                       r"relocation\W+(and|&)\W+visa|visa support|we sponsor", re.I)


def parse_hn_comment(raw_html: str, comment_id: Any, created_at: str = "") -> Job | None:
    """Parse one top-level "Who is hiring" comment into a Job (None if it has no text)."""
    if not raw_html:
        return None
    url_m = re.search(r'href="([^"]+)"', raw_html)
    link = _html.unescape(url_m.group(1)) if url_m and url_m.group(1).lower().startswith("http") else ""
    hn_url = f"https://news.ycombinator.com/item?id={comment_id}"
    text = strip_html(raw_html)
    if not text:
        return None
    first = text.split("\n", 1)[0].strip()
    parts = [p.strip() for p in re.split(r"\s+[|—–]\s+|\s*\|\s*", first) if p.strip()]
    company = parts[0] if parts else first
    loc_bits: list[str] = []
    paren = re.search(r"\(([^)]*)\)", company)
    if paren and re.search(r"remote|onsite|on-site|hybrid", paren.group(1), re.I):
        loc_bits.append(paren.group(1).strip())
        company = (company[:paren.start()] + company[paren.end():]).strip()
    title = ""
    rest = parts[1:]
    for p in rest:
        if _HN_ROLE.search(p):
            title = p
            break
    if not title:
        title = first[:120]  # no recognisable role word: keep the whole header line
    for p in rest:
        if p == title or _HN_NOISE.search(p):
            continue
        if re.search(r"remote|onsite|on-site|hybrid|,|europe|\busa?\b|\buk\b|worldwide|anywhere", p, re.I):
            loc_bits.append(p)
    location = ", ".join(dict.fromkeys(loc_bits))
    head = first.lower()
    if re.search(r"\bremote\b", head) and not re.search(r"\bnot remote\b|\bno remote\b", head):
        remote: bool | None = True
    elif re.search(r"on-?site|hybrid", head):
        remote = False
    else:
        remote = None
    visa: bool | None = None
    if _VISA_NO.search(text):
        visa = False
    elif _VISA_YES.search(text):
        visa = True
    return _job(
        title=title, company=company, url=link or hn_url, location=location,
        posted=_date(created_at), description=raw_html, source="feed:hn", board="hn",
        remote=remote, visa_flag=visa, extra={"hn_id": str(comment_id), "hn_url": hn_url},
    )


def parse_hn_thread(item: dict[str, Any]) -> list[Job]:
    """All top-level comments of a thread item (Algolia ``/items/{id}`` shape) as Jobs."""
    jobs: list[Job] = []
    for c in (item or {}).get("children") or []:
        if not isinstance(c, dict) or not c.get("text"):
            continue
        j = parse_hn_comment(c["text"], c.get("id"), c.get("created_at") or "")
        if j:
            jobs.append(j)
    return jobs


@_feed
def fetch_hn(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Latest Hacker News 'Ask HN: Who is hiring?' thread, keyword-matched on each comment."""
    search = _get(http, stats,
                  "https://hn.algolia.com/api/v1/search_by_date?tags=story,author_whoishiring"
                  "&query=who%20is%20hiring")
    hits = (search or {}).get("hits") or []
    story = next((h for h in hits if "who is hiring" in str(h.get("title", "")).lower()), None)
    if not story:
        return []
    item = _get(http, stats, f"https://hn.algolia.com/api/v1/items/{story['objectID']}")
    return _keep(parse_hn_thread(item or {}), queries, days)


# ------------------------------------------------------------------------------- Jobicy
def parse_jobicy(payload: dict[str, Any]) -> list[Job]:
    """Parse a Jobicy v2 response."""
    out: list[Job] = []
    for r in (payload or {}).get("jobs") or []:
        geo = re.sub(r"\s*,\s*", ", ", str(r.get("jobGeo") or "")).strip()
        out.append(_job(
            title=r.get("jobTitle"), company=r.get("companyName"), url=r.get("url"),
            location=geo, posted=_date(r.get("pubDate")),
            description=r.get("jobDescription") or r.get("jobExcerpt"),
            source="feed:jobicy", board="jobicy", remote=True,
            extra={"level": r.get("jobLevel"), "types": r.get("jobType"),
                   "industry": r.get("jobIndustry"), "id": r.get("id")}))
    return out


@_feed
def fetch_jobicy(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Jobicy remote jobs; one request per query (as ``tag``) or one unfiltered request."""
    jobs: list[Job] = []
    for q in (_queries(queries) or [""]):
        url = "https://jobicy.com/api/v2/remote-jobs?count=50"
        if q:
            url += "&tag=" + quote(q)
        jobs += parse_jobicy(_get(http, stats, url) or {})
    return _keep(jobs, queries, days, search=False)


# -------------------------------------------------------------------------- Working Nomads
def parse_workingnomads(payload: list[Any]) -> list[Job]:
    """Parse the Working Nomads exposed_jobs array."""
    out: list[Job] = []
    for r in payload or []:
        tags = [t.strip() for t in str(r.get("tags") or "").split(",") if t.strip()]
        out.append(_job(
            title=r.get("title"), company=r.get("company_name"), url=r.get("url"),
            location=r.get("location"), posted=_date(r.get("pub_date")),
            description=r.get("description"), source="feed:workingnomads",
            board="workingnomads", remote=True,
            extra={"category": r.get("category_name"), "tags": tags}))
    return out


@_feed
def fetch_workingnomads(
    queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]
) -> list[Job]:
    """Working Nomads (one request, whole feed, keyword-matched locally)."""
    body = _get(http, stats, "https://www.workingnomads.com/api/exposed_jobs/")
    return _keep(parse_workingnomads(body if isinstance(body, list) else []), queries, days)


# --------------------------------------------------------------------- We Work Remotely
WWR_CATEGORIES = (
    "remote-programming-jobs", "remote-full-stack-programming-jobs",
    "remote-devops-sysadmin-jobs", "remote-back-end-programming-jobs",
)


def parse_wwr(xml_text: str) -> list[Job]:
    """Parse a We Work Remotely category RSS feed."""
    root = safe_fromstring(xml_text)
    if root is None:
        return []  # empty, malformed, or declares DOCTYPE/ENTITY
    out: list[Job] = []
    for it in root.iter("item"):
        raw_title = (it.findtext("title") or "").strip()
        company, _, title = raw_title.partition(": ")
        if not title:
            company, title = "", raw_title
        link = (it.findtext("link") or it.findtext("guid") or "").strip()
        out.append(_job(
            title=title, company=company, url=link, location=it.findtext("region") or "",
            posted=_date(it.findtext("pubDate")), description=it.findtext("description"),
            source="feed:wwr", board="weworkremotely", remote=True,
            extra={"category": it.findtext("category"), "type": it.findtext("type"),
                   "skills": it.findtext("skills")}))
    return out


@_feed
def fetch_wwr(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """We Work Remotely: four programming-related category feeds, keyword-matched locally."""
    jobs: list[Job] = []
    for cat in WWR_CATEGORIES:
        body = _get(http, stats, f"https://weworkremotely.com/categories/{cat}.rss", as_json=False)
        jobs += parse_wwr(body if isinstance(body, str) else "")
    seen: set[str] = set()
    uniq: list[Job] = []
    for j in jobs:
        if j["url"] not in seen:
            seen.add(j["url"])
            uniq.append(j)
    return _keep(uniq, queries, days)


# ------------------------------------------------------------------------------ The Muse
def parse_themuse(payload: dict[str, Any]) -> list[Job]:
    """Parse a The Muse public jobs page."""
    out: list[Job] = []
    for r in (payload or {}).get("results") or []:
        locs = [str(x.get("name", "")) for x in r.get("locations") or [] if x.get("name")]
        loc = ", ".join(locs)
        out.append(_job(
            title=r.get("name"), company=(r.get("company") or {}).get("name"),
            url=(r.get("refs") or {}).get("landing_page"), location=loc,
            posted=_date(r.get("publication_date")), description=r.get("contents"),
            source="feed:themuse", board="themuse",
            remote=True if re.search(r"remote|flexible", loc, re.I) else None,
            extra={"levels": [x.get("name") for x in r.get("levels") or []],
                   "categories": [x.get("name") for x in r.get("categories") or []],
                   "tags": [x.get("name") for x in r.get("tags") or []]}))
    return out


@_feed
def fetch_themuse(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """The Muse: first MAX_PAGES pages, keyword-matched locally."""
    jobs: list[Job] = []
    for page in range(1, MAX_PAGES + 1):
        body = _get(http, stats, f"https://www.themuse.com/api/public/jobs?page={page}")
        if not isinstance(body, dict):
            break
        jobs += parse_themuse(body)
        if page >= int(body.get("page_count") or 0):
            break
    return _keep(jobs, queries, days)


# ---------------------------------------------------------------------------- Arbeitnow
def parse_arbeitnow(payload: dict[str, Any]) -> list[Job]:
    """Parse an Arbeitnow job-board page (maps ``remote`` and ``visa_sponsorship``)."""
    out: list[Job] = []
    for r in (payload or {}).get("data") or []:
        visa = r.get("visa_sponsorship")
        out.append(_job(
            title=r.get("title"), company=r.get("company_name"), url=r.get("url"),
            location=r.get("location"), posted=_date(r.get("created_at")),
            description=r.get("description"), source="feed:arbeitnow", board="arbeitnow",
            remote=r["remote"] if isinstance(r.get("remote"), bool) else None,
            visa_flag=visa if isinstance(visa, bool) else None,
            extra={"tags": r.get("tags") or [], "job_types": r.get("job_types") or [],
                   "slug": r.get("slug")}))
    return out


@_feed
def fetch_arbeitnow(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Arbeitnow (mostly Germany/EU): first MAX_PAGES pages, keyword-matched locally."""
    jobs: list[Job] = []
    for page in range(1, MAX_PAGES + 1):
        body = _get(http, stats, f"https://www.arbeitnow.com/api/job-board-api?page={page}")
        page_jobs = parse_arbeitnow(body if isinstance(body, dict) else {})
        if not page_jobs:
            break
        jobs += page_jobs
    return _keep(jobs, queries, days)


# --------------------------------------------------------------------------- Himalayas
def parse_himalayas(payload: dict[str, Any]) -> list[Job]:
    """Parse a Himalayas jobs API page (``locationRestrictions`` becomes location)."""
    out: list[Job] = []
    for r in (payload or {}).get("jobs") or []:
        restr = [str(x) for x in r.get("locationRestrictions") or []]
        out.append(_job(
            title=r.get("title"), company=r.get("companyName"),
            url=r.get("applicationLink") or r.get("guid"),
            location=", ".join(restr) if restr else "Worldwide",
            posted=_date(r.get("pubDate")), description=r.get("description") or r.get("excerpt"),
            source="feed:himalayas", board="himalayas", remote=True,
            extra={"seniority": r.get("seniority"), "employment_type": r.get("employmentType"),
                   "tags": r.get("categories") or [], "min_salary": r.get("minSalary"),
                   "max_salary": r.get("maxSalary"), "currency": r.get("currency"),
                   "timezones": r.get("timezoneRestrictions")}))
    return out


HIMALAYAS_PAGE = 20  # the size the API serves, whatever limit is asked


@_feed
def fetch_himalayas(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Himalayas remote jobs: its search API per query (pages of 20), then keyword-matched locally.

    The list feed (/jobs/api) caps a page at 20 whatever limit is asked and has deprecated offset
    paging (its own notice, 2026-08-21), so a fixed 100-row stride read only 1 row in 5 of the newest
    jobs. /jobs/api/search?q= answers each query server-side with the same job fields (checked live
    2026-10-18).
    """
    jobs: list[Job] = []
    seen: set[str] = set()
    for q in _queries(queries) or [""]:
        cursor = ""
        for page in range(1, MAX_PAGES + 1):
            if q:
                url = f"https://himalayas.app/jobs/api/search?{urlencode({'q': q, 'page': page})}"
            else:  # no words given: the newest jobs, paged by the cursor the feed hands back
                url = "https://himalayas.app/jobs/api" + (f"?{urlencode({'cursor': cursor})}" if cursor else "")
            body = _get(http, stats, url)
            got = [j for j in parse_himalayas(body if isinstance(body, dict) else {}) if j["url"] not in seen]
            seen.update(j["url"] for j in got)
            jobs += got
            served = len(body.get("jobs") or []) if isinstance(body, dict) else 0
            cursor = str(body.get("nextCursor") or "") if isinstance(body, dict) else ""
            if served < HIMALAYAS_PAGE or not (q or cursor):  # a short or failed page is the last one
                break
    return _keep(jobs, queries, days)


# ---------------------------------------------------------------------------- Remotive
def parse_remotive(payload: dict[str, Any]) -> list[Job]:
    """Parse a Remotive API response."""
    out: list[Job] = []
    for r in (payload or {}).get("jobs") or []:
        out.append(_job(
            title=r.get("title"), company=(r.get("company_name") or "").strip(),
            url=r.get("url"), location=r.get("candidate_required_location"),
            posted=_date(r.get("publication_date")), description=r.get("description"),
            source="feed:remotive", board="remotive", remote=True,
            extra={"category": r.get("category"), "tags": r.get("tags") or [],
                   "job_type": r.get("job_type"), "salary": r.get("salary")}))
    return out


@_feed
def fetch_remotive(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Remotive: server-side ``search`` per query (or one unfiltered request)."""
    jobs: list[Job] = []
    for q in (_queries(queries) or [""]):
        url = "https://remotive.com/api/remote-jobs" + (f"?search={quote(q)}" if q else "")
        jobs += parse_remotive(_get(http, stats, url) or {})
    return _keep(jobs, queries, days, search=False)


# ---------------------------------------------------------------------------- RemoteOK
def parse_remoteok(payload: list[Any]) -> list[Job]:
    """Parse RemoteOK's array; the first row is a legal notice and is skipped."""
    out: list[Job] = []
    for r in payload or []:
        if not isinstance(r, dict) or not r.get("position"):
            continue  # legal row / junk
        out.append(_job(
            title=r.get("position"), company=r.get("company"),
            url=r.get("url") or r.get("apply_url"), location=r.get("location"),
            posted=_date(r.get("date") or r.get("epoch")), description=r.get("description"),
            source="feed:remoteok", board="remoteok", remote=True,
            extra={"tags": r.get("tags") or [], "salary_min": r.get("salary_min"),
                   "salary_max": r.get("salary_max"), "attribution": "Source: RemoteOK"}))
    return out


@_feed
def fetch_remoteok(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """RemoteOK (one request, keyword-matched locally). Link back to remoteok.com when showing."""
    body = _get(http, stats, "https://remoteok.com/api")
    return _keep(parse_remoteok(body if isinstance(body, list) else []), queries, days)


# ------------------------------------------------------------------------------ Adzuna
def parse_adzuna(payload: dict[str, Any], country: str = "") -> list[Job]:
    """Parse an Adzuna search response (descriptions are API-truncated snippets)."""
    out: list[Job] = []
    for r in (payload or {}).get("results") or []:
        out.append(_job(
            title=r.get("title"), company=(r.get("company") or {}).get("display_name"),
            url=r.get("redirect_url"), location=(r.get("location") or {}).get("display_name"),
            posted=_date(r.get("created")), description=r.get("description"),
            source="feed:adzuna", board=country,
            extra={"salary_min": r.get("salary_min"), "salary_max": r.get("salary_max"),
                   "contract_type": r.get("contract_type"), "truncated": True,
                   "category": (r.get("category") or {}).get("label")}))
    return out


@_feed
def fetch_adzuna(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Adzuna official API. Needs env ADZUNA_APP_ID + ADZUNA_APP_KEY; only supported countries."""
    app_id, app_key = os.environ.get("ADZUNA_APP_ID", ""), os.environ.get("ADZUNA_APP_KEY", "")
    if not app_id or not app_key:
        return []
    codes = [c.lower() for c in countries if c and c.lower() in ADZUNA_COUNTRIES]
    jobs: list[Job] = []
    for cc in dict.fromkeys(codes):
        for q in (_queries(queries) or [""]):
            for page in range(1, MAX_PAGES + 1):
                params = {"app_id": app_id, "app_key": app_key, "results_per_page": 50,
                          "content-type": "application/json"}
                if q:
                    params["what"] = q
                if days and days > 0:
                    params["max_days_old"] = days
                body = _get(http, stats,
                            f"https://api.adzuna.com/v1/api/jobs/{cc}/search/{page}?"
                            + urlencode(params))
                page_jobs = parse_adzuna(body if isinstance(body, dict) else {}, cc)
                if not page_jobs:
                    break
                jobs += page_jobs
    return _keep(jobs, queries, days, search=False)


# ------------------------------------------------------------------------------ Jooble
def parse_jooble(payload: dict[str, Any]) -> list[Job]:
    """Parse a Jooble search response (snippets, not full descriptions)."""
    out: list[Job] = []
    for r in (payload or {}).get("jobs") or []:
        out.append(_job(
            title=r.get("title"), company=r.get("company"), url=r.get("link"),
            location=r.get("location"), posted=_date(r.get("updated")),
            description=r.get("snippet"), source="feed:jooble", board="jooble",
            extra={"salary": r.get("salary"), "type": r.get("type"),
                   "origin": r.get("source"), "truncated": True}))
    return out


@_feed
def fetch_jooble(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Jooble official API (POST). Needs env JOOBLE_KEY. Locations come from ``countries``."""
    key = os.environ.get("JOOBLE_KEY", "")
    if not key:
        return []
    locs = [_COUNTRY_NAMES.get(c.upper(), c) for c in dict.fromkeys(countries or []) if c and c != "*"] or [""]
    jobs: list[Job] = []
    for loc in locs:
        for q in (_queries(queries) or [""]):
            for page in range(1, MAX_PAGES + 1):
                data = json.dumps({"keywords": q, "location": loc, "page": str(page)}).encode()
                body = _get(http, stats, f"https://jooble.org/api/{quote(key, safe='')}",
                            method="POST", data=data,
                            headers={"Content-Type": "application/json"})
                page_jobs = parse_jooble(body if isinstance(body, dict) else {})
                if not page_jobs:
                    break
                jobs += page_jobs
    return _keep(jobs, queries, days, search=False)


# ---------------------------------------------------------------------------- GitHub job lists
# Open new-grad and intern lists kept on GitHub by SimplifyJobs (the same files freehire's
# harvest reads). Read at run time; nothing is stored. They carry no description: open the link.
GITHUB_LISTS = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
)


def parse_githublists(payload: Any, list_url: str = "") -> list[Job]:
    """Parse one SimplifyJobs ``listings.json`` (a JSON array); only active, visible rows."""
    out: list[Job] = []
    for r in payload if isinstance(payload, list) else []:
        if not isinstance(r, dict) or not r.get("active") or r.get("is_visible") is False:
            continue
        locs = [str(x) for x in r.get("locations") or [] if x]
        out.append(_job(
            title=r.get("title"), company=r.get("company_name"), url=r.get("url"),
            location=" | ".join(locs), posted=_date(r.get("date_posted")),
            source="feed:githublists", board="githublists",
            remote=True if any("remote" in x.lower() for x in locs) else None,
            extra={"sponsorship": r.get("sponsorship"), "category": r.get("category"),
                   "list": list_url.split("/SimplifyJobs/", 1)[-1].split("/", 1)[0],
                   "attribution": "SimplifyJobs GitHub list", "title_only": True}))
    return out


@_feed
def fetch_githublists(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """SimplifyJobs new-grad and intern lists on GitHub, keyword-matched locally (title only)."""
    jobs: list[Job] = []
    for url in GITHUB_LISTS:
        jobs += parse_githublists(_get(http, stats, url), url)
    return _keep(jobs, queries, days)


# ---------------------------------------------------------------------------- national job services
# Public government job searches, run only when the user's countries include that country.
# Endpoints and field names follow career-ops' providers (MIT): arbeitsagentur.mjs, platsbanken.mjs,
# mycareersfuture.mjs, jobbankca.mjs. No code copied.
ARBEITSAGENTUR_URL = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs"
ARBEITSAGENTUR_KEY = "jobboerse-jobsuche"  # the public client id arbeitsagentur.de's own pages send
JOBTECH_URL = "https://jobsearch.api.jobtechdev.se/search"
MCF_URL = "https://api.mycareersfuture.gov.sg/v2/search"
JOBBANK_URL = "https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed"
NATIONAL_PAGES = 2  # pages per title per service


def parse_arbeitsagentur(payload: Any) -> list[Job]:
    """Parse a Jobsuche v6 search (``ergebnisliste``). Title, employer and place; open the link for the ad."""
    out: list[Job] = []
    for r in (payload or {}).get("ergebnisliste") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or not r.get("referenznummer"):
            continue
        locs = r.get("stellenlokationen") or []
        adr = (locs[0] or {}).get("adresse") if locs and isinstance(locs[0], dict) else None
        place = str((adr or {}).get("ort") or "").strip() if isinstance(adr, dict) else ""
        out.append(_job(
            title=r.get("stellenangebotsTitel"), company=r.get("firma"),
            url="https://www.arbeitsagentur.de/jobsuche/jobdetail/" + quote(str(r["referenznummer"]), safe=""),
            location=_join_place(place, "Germany"), source="feed:arbeitsagentur", board="arbeitsagentur",
            extra={"title_only": True, "attribution": "Bundesagentur fuer Arbeit"}))
    return out


def parse_jobtech(payload: Any) -> list[Job]:
    """Parse a Swedish JobTech (Platsbanken) search (``hits``), full description included."""
    out: list[Job] = []
    for r in (payload or {}).get("hits") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or r.get("id") in (None, ""):
            continue
        wa = r.get("workplace_address") or {}
        place = next((str(wa.get(k)) for k in ("city", "municipality", "region") if wa.get(k)), "")
        out.append(_job(
            title=r.get("headline"), company=(r.get("employer") or {}).get("name"),
            url="https://arbetsformedlingen.se/platsbanken/annonser/" + quote(str(r["id"]).strip(), safe=""),
            location=_join_place(place, str(wa.get("country") or "Sweden")),
            posted=_date(r.get("publication_date")), description=(r.get("description") or {}).get("text"),
            source="feed:jobtech", board="platsbanken", extra={"attribution": "Arbetsformedlingen JobTech"}))
    return out


def parse_mycareersfuture(payload: Any) -> list[Job]:
    """Parse a Singapore MyCareersFuture search (``results``)."""
    out: list[Job] = []
    for r in (payload or {}).get("results") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict):
            continue
        md = r.get("metadata") or {}
        districts = (r.get("address") or {}).get("districts") or []
        place = ", ".join(str(d.get("location")) for d in districts if isinstance(d, dict) and d.get("location"))
        out.append(_job(
            title=r.get("title"), url=md.get("jobDetailsUrl"),
            company=(r.get("hiringCompany") or {}).get("name") or (r.get("postedCompany") or {}).get("name"),
            location=_join_place(place, "Singapore"), posted=_date(md.get("newPostingDate")),
            description=r.get("description"), source="feed:mycareersfuture", board="mycareersfuture",
            extra={"attribution": "MyCareersFuture"}))
    return out


_ATOM = "{http://www.w3.org/2005/Atom}"


def _summary_field(summary: str, label: str) -> str:
    m = re.search(rf"<strong>{re.escape(label)}:</strong>\s*(.*?)\s*(?:<br\s*/?>|$)", summary, re.I | re.S)
    return strip_html(_html.unescape(m.group(1))).strip() if m else ""


def parse_jobbank(xml_text: str) -> list[Job]:
    """Parse Canada Job Bank's public search feed (Atom despite the RSS name)."""
    root = safe_fromstring(xml_text)
    if root is None:
        return []
    out: list[Job] = []
    for e in root.iter(_ATOM + "entry"):
        links = e.findall(_ATOM + "link")
        alt = next((x for x in links if (x.get("rel") or "alternate") == "alternate"), links[0] if links else None)
        summary = e.findtext(_ATOM + "summary") or ""
        place = _summary_field(summary, "Location")
        out.append(_job(
            title=e.findtext(_ATOM + "title"), company=_summary_field(summary, "Employer"),
            url=(alt.get("href") if alt is not None else ""), location=_join_place(place, "Canada"),
            posted=_date(e.findtext(_ATOM + "updated")), source="feed:jobbank", board="jobbank",
            extra={"title_only": True, "attribution": "Job Bank (Government of Canada)"}))
    return out


def _join_place(place: str, country: str) -> str:
    return ", ".join(x for x in (place.strip(), country) if x) if country.lower() not in place.lower() else place


@_feed
def fetch_arbeitsagentur(
    queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]
) -> list[Job]:
    """Germany's Bundesagentur fuer Arbeit job search, only when DE is one of the user's countries."""
    if not _gated(countries, "DE"):
        return []
    jobs: list[Job] = []
    for q in _queries(queries):
        for page in range(1, NATIONAL_PAGES + 1):
            params: dict[str, Any] = {"was": q, "size": 100, "page": page, "angebotsart": 1}
            if days and days > 0:  # no age limit = leave the window out, like the other feeds
                params["veroeffentlichtseit"] = min(days, 100)
            qs = urlencode(params)
            got = parse_arbeitsagentur(_get(http, stats, f"{ARBEITSAGENTUR_URL}?{qs}",
                                            headers={"X-API-Key": ARBEITSAGENTUR_KEY, "Accept": "application/json"}))
            jobs += got
            if len(got) < 100:
                break
    return _keep(jobs, queries, days, search=False)  # the service matched the query itself


@_feed
def fetch_jobtech(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Sweden's JobTech (Platsbanken) search, only when SE is one of the user's countries."""
    if not _gated(countries, "SE"):
        return []
    jobs: list[Job] = []
    for q in _queries(queries):
        for page in range(NATIONAL_PAGES):
            qs = urlencode({"q": q, "limit": 100, "offset": page * 100})
            got = parse_jobtech(_get(http, stats, f"{JOBTECH_URL}?{qs}"))
            jobs += got
            if len(got) < 100:
                break
    return _keep(jobs, queries, days, search=False)  # the service matched the query itself


@_feed
def fetch_mycareersfuture(
    queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]
) -> list[Job]:
    """Singapore's MyCareersFuture search (POST; the query string's page pages), only for SG users."""
    if not _gated(countries, "SG"):
        return []
    jobs: list[Job] = []
    for q in _queries(queries):
        for page in range(NATIONAL_PAGES):
            body = json.dumps({"search": q, "sortBy": ["new_posting_date"], "page": page}).encode()
            got = parse_mycareersfuture(_get(http, stats, f"{MCF_URL}?limit=100&page={page}", method="POST",
                                             data=body, headers={"Content-Type": "application/json"}))
            jobs += got
            if len(got) < 100:
                break
    return _keep(jobs, queries, days, search=False)  # the service matched the query itself


@_feed
def fetch_jobbank(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Canada's Job Bank search feed, only when CA is one of the user's countries."""
    if not _gated(countries, "CA"):
        return []
    jobs: list[Job] = []
    for q in _queries(queries):
        for page in range(1, NATIONAL_PAGES + 1):
            qs = urlencode({"searchstring": q, "locationstring": "", "page": page})
            body = _get(http, stats, f"{JOBBANK_URL}?{qs}", as_json=False)
            known = {j["url"] for j in jobs}
            got = [j for j in parse_jobbank(body if isinstance(body, str) else "") if j["url"] not in known]
            jobs += got
            if not got:  # empty, or a page that repeats the last one
                break
    return _keep(jobs, queries, days, search=False)  # the service matched the query itself


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "hn": fetch_hn,
    "jobicy": fetch_jobicy,
    "workingnomads": fetch_workingnomads,
    "wwr": fetch_wwr,
    "themuse": fetch_themuse,
    "arbeitnow": fetch_arbeitnow,
    "himalayas": fetch_himalayas,
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "adzuna": fetch_adzuna,
    "jooble": fetch_jooble,
    "githublists": fetch_githublists,
    "arbeitsagentur": fetch_arbeitsagentur,
    "jobtech": fetch_jobtech,
    "mycareersfuture": fetch_mycareersfuture,
    "jobbank": fetch_jobbank,
}
