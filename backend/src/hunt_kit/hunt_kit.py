#!/usr/bin/env python3
"""Job360 hunt kit: a no-login job finder for YOUR assistant to run on YOUR machine.

It reads the public JSON job boards that companies publish themselves (Greenhouse,
Lever, Ashby, SmartRecruiters, Workable, Recruitee, Personio, Teamtailor, BambooHR,
Workday) plus open job feeds (freehire.me, Himalayas, Remotive, Arbeitnow, RemoteOK,
Jobicy, Working Nomads, We Work Remotely, The Muse, Hacker News "Who is hiring").
Official keyed APIs (Adzuna, Jooble) run only if you set their env vars.  Big job
boards (LinkedIn, Indeed, ...) are OFF unless you pass --boards (needs python-jobspy).

It never logs in.  Standard library only (Python 3.10+).  It never ranks or scores:
it filters by words and sorts newest first.  Your assistant reads each kept job's
full description, judges fit itself, and brings only fits into Job360
(bring_job + save_fit).  Job360 itself never runs this file.

Example (any country, e.g. a user who may work in India and Portugal):
    python hunt_kit.py --work-countries IN,PT --titles "ai engineer,agent engineer" \
        --out found.jsonl --stats

Importing this module does no network and reads no files.
"""

# ruff: noqa: UP031, S310
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import datetime as _dt
import functools
import html
import importlib
import ipaddress
import json
import re
import socket
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Iterable, cast

USER_AGENT = "job360-hunt-kit/1.0 (+https://job360.uk)"
MAX_WORKERS = 8
MAX_PER_HOST = 3
TIMEOUT_S = 30
RETRIES_429 = 2

BOARD_SOURCES = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workable",
    "recruitee",
    "personio",
    "teamtailor",
    "bamboohr",
    "workday",
)
FEED_SOURCES = (
    "freehire",
    "himalayas",
    "remotive",
    "arbeitnow",
    "remoteok",
    "jobicy",
    "workingnomads",
    "wwr",
    "themuse",
    "hn",
    "adzuna",
    "jooble",
)
SOURCE_ALIASES = {"weworkremotely": "wwr", "we-work-remotely": "wwr", "hackernews": "hn"}
ALL_SOURCES = BOARD_SOURCES + FEED_SOURCES
# "default" skips the two giant unvetted lists (bamboohr, workday: ~24k boards).
DEFAULT_SKIP = ("bamboohr", "workday")
JOBSPY_SITES = ("linkedin", "indeed", "glassdoor", "google", "naukri", "bayt", "zip_recruiter")

DEFAULT_TITLES = (
    "ai engineer, llm engineer, agent engineer, agentic engineer, generative ai engineer, "
    "genai engineer, applied ai engineer, applied ml engineer, machine learning engineer, "
    "ai software engineer, ai product engineer, ai solutions engineer, forward deployed engineer, "
    "founding engineer, member of technical staff, nlp engineer, ai developer, prompt engineer"
)
DEFAULT_EXCLUDE = (
    "senior,sr,staff,principal,lead,head,director,manager,intern,internship,vice,vp,"
    "distinguished,scientist,mlops,sales,recruiter,recruiting,marketing,account executive,"
    "support,ii,iii,iv"
)

# =============================================================== FILTERS
# English phrase lists in one place.  Ads in other languages can slip past these
# phrase filters: the assistant must read the description itself.
TITLE = re.compile(
    r"\bai\b|\ba\.i\.|llm|gen ?ai|generative|agentic|\bagents?\b|machine learning|\bml\b|"
    r"applied (ai|ml)|forward[- ]deployed|founding|solutions? engineer|member of technical staff|"
    r"\bmts\b|\bnlp\b|prompt|deployed engineer|conversational|copilot|\brag\b",
    re.I,
)
ENG = re.compile(
    r"engineer|developer|\bmts\b|technical staff|forward[- ]deployed|architect|programmer|founding|swe\b",
    re.I,
)
REMOTE = re.compile(r"remote|work from (home|anywhere)|distributed|anywhere|telecommut", re.I)
HYBRID = re.compile(r"hybrid|on-?site|in[- ]office", re.I)
WORLD = re.compile(
    r"worldwide|world[- ]wide|anywhere|global|any (country|location|timezone|time zone)|"
    r"international|all locations|earth",
    re.I,
)
DESC_WORLD = re.compile(
    r"work from anywhere|anywhere in the world|worldwide|any (country|time ?zone)|"
    r"fully remote[^.]{0,40}(global|world)|remote[- ]first[^.]{0,40}(global|world)",
    re.I,
)
# A sentence that limits WHERE you may live/work.  Countries named in it must include
# one of your work countries, or the job is dropped (career-ops country_eligibility idea).
LOCK_A = re.compile(
    r"(must|need to|required to|have to)\s+(be\s+)?(located|based|living|resid\w+|authori[sz]ed|eligible|legally)|"
    r"authori[sz]ed to work|right to work|work authori[sz]ation|eligible to work|"
    r"\bresidents?\s+of\b|\breside\s+in\b|\bresident\s+in\b|\bcitizens?\s+(of|only)\b",
    re.I,
)
LOCK_B = re.compile(r"\b(remote|applicants?|candidates?|hir(e|ing)|located|based|residents?|citizens?)\b", re.I)
VISA_POS = re.compile(
    r"visa sponsor|sponsor(?:s|ing)?\s+(?:a\s+|your\s+|work\s+|employment\s+)*visa|"
    r"we (?:can |do |will |are able to |are happy to )?sponsor[^.]{0,40}(?:visa|permit|immigration|candidates|work)|"
    r"(?:offer|provide|provides|offers|available)[^.]{0,30}(?:visa )?sponsorship|"
    r"sponsorship (?:is )?(?:available|provided|offered)|visa support|visa assistance|"
    r"work permit (?:support|sponsor|assist)|immigration (?:support|assistance|sponsor)|"
    r"relocation (?:support|assistance|package|bonus|allowance|budget)|relocation|h-?1b",
    re.I,
)
# Negation is sentence-wide: a negation word anywhere in a sentence that talks about sponsorship,
# visas or relocation makes the whole sentence NEGATIVE.  Negative always beats positive.
NEG_WORD = re.compile(
    r"\b(?:not|no|never|unable|cannot|without|unavailable|ineligible|neither|nor)\b|n't\b|\bcan not\b",
    re.I,
)
VISA_TOPIC = re.compile(r"sponsor|relocat|visa|work permit|immigration", re.I)
VISA_NEG = re.compile(
    r"require(?:s|d)?\s+(?:a\s+)?(?:work\s+)?(?:visa\s+)?sponsorship|"
    r"need(?:s|ed)?\s+(?:a\s+)?(?:work\s+)?(?:visa\s+)?sponsorship",
    re.I,
)


def visa_negated(sentence: str) -> bool:
    """True if the sentence refuses, or asks whether you NEED, visa sponsorship/relocation."""
    return bool(VISA_NEG.search(sentence) or (NEG_WORD.search(sentence) and VISA_TOPIC.search(sentence)))


YRS = re.compile(r"(\d{1,2})\s*(?:\+|\s*(?:-|–|to)\s*(\d{1,2}))?\s*\+?\s*(?:years|yrs)", re.I)
DEAD_PAGE = re.compile(
    r"no longer (accepting|available)|position (has been|is) (filled|closed)|"
    r"job (is )?(no longer|has expired)|this job (has )?expired|page not found|"
    r"posting (has been )?(closed|removed)",
    re.I,
)
# ================================================================ places
# All place matching (country names, aliases, cities, region words) lives in kit_places.py
# and places.json, built from open datasets.  Nothing country-specific is hand-typed here.
_PLACES: Any = None
HERE = Path(__file__).resolve().parent


def _import_kit(name: str) -> Any:
    """Import a sibling module (kit_places, kit_ats, ...) or return None if it is missing."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    try:
        return importlib.import_module(name)
    except ImportError as e:
        print("note: %s unavailable (%s); its sources are skipped" % (name, e), file=sys.stderr)
        return None


def get_places() -> Any:
    """Load the Places object once (reads places.json lazily, never at import time)."""
    global _PLACES
    if _PLACES is None:
        kp = _import_kit("kit_places")
        if kp is None:
            raise RuntimeError("kit_places.py is required for location matching")
        _PLACES = kp.load_places(HERE / "places.json")
    return _PLACES


@dataclass(frozen=True)
class LocInfo:
    """What a location string says: explicit countries, region members, worldwide."""

    countries: frozenset[str] = frozenset()
    regions: frozenset[str] = frozenset()
    world: bool = False


def _region_spans(text: str, pl: Any) -> tuple[set[str], str]:
    """Find region words (APAC, EMEA, 'Latin America'...) in text.

    Returns (member countries of all region words found, text with those words blanked out).
    """
    words = [
        (m.start(), m.end(), re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", m.group()).lower()))
        for m in re.finditer(r"\w+", text)
    ]
    members: set[str] = set()
    masked = list(text)
    i = 0
    while i < len(words):
        hit = 0
        for n in range(min(3, len(words) - i), 0, -1):
            gram = " ".join(w[2] for w in words[i : i + n])
            got = pl.region_members(gram)
            if got and not (n == 1 and len(gram) == 2 and gram != "eu"):
                members |= set(got)
                hit = n
                break
        if hit:
            for j in range(words[i][0], words[i + hit - 1][1]):
                masked[j] = " "
            i += hit
        else:
            i += 1
    return members, "".join(masked)


@functools.lru_cache(maxsize=50000)
def parse_loc(text: str) -> LocInfo:
    """Find explicit countries, region members and 'worldwide' in a location string."""
    pl = get_places()
    members, masked = _region_spans(WORLD.sub(" ", text or ""), pl)
    explicit = frozenset(pl.countries_in(masked))
    return LocInfo(explicit, frozenset(members), bool(WORLD.search(text or "")))


def fold(s: str) -> str:
    """Lowercase, strip accents, keep letters/digits separated by single spaces."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))


def split_csv(value: str | None) -> list[str]:
    """Split a comma list into trimmed, non-empty items."""
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def parse_country_list(value: str | Iterable[str] | None) -> frozenset[str]:
    """Turn 'IN,PT' (or country names) into ISO2 codes. Raises ValueError for unknown ones."""
    items = split_csv(value) if isinstance(value, str) else list(value or [])
    if not items:
        return frozenset()
    pl = get_places()
    out: set[str] = set()
    for it in items:
        u = it.strip().upper()
        if u == "UK":
            u = "GB"
        if len(u) == 2 and u in pl.countries:
            out.add(u)
            continue
        found = set(pl.countries_in(it))
        if len(found) == 1:
            out |= found
        else:
            raise ValueError("unknown country: %s (use ISO2 codes like IN,PT,BR)" % it)
    return frozenset(out)


def country_name(cc: str) -> str:
    """A readable English name for an ISO2 code (for API parameters)."""
    return str(get_places().name(cc))


@dataclass(frozen=True)
class Prefs:
    """What the user can accept. Nothing here is country-specific."""

    work: frozenset[str] = frozenset()
    remote: bool = True
    visa_onsite: bool = True
    maybe_relocation: bool = False
    only: frozenset[str] = frozenset()
    exclude: frozenset[str] = frozenset()
    any_row: bool = False


def make_prefs(
    work: str | Iterable[str] | None = None,
    remote: bool = True,
    visa_onsite: bool = True,
    maybe_relocation: bool = False,
    only: str | Iterable[str] | None = None,
    exclude: str | Iterable[str] | None = None,
    any_row: bool = False,
) -> Prefs:
    """Build Prefs from CSV strings or iterables of ISO2 codes."""
    return Prefs(
        parse_country_list(work),
        remote,
        visa_onsite,
        maybe_relocation,
        parse_country_list(only),
        parse_country_list(exclude),
        any_row,
    )


# ===================================================== pure filter functions
def html_to_text(raw: str | None) -> str:
    """Turn an HTML fragment into plain text, keeping line breaks between blocks."""
    h = html.unescape(raw or "")
    h = re.sub(r"(?i)</(p|li|div|h[1-6]|ul|ol|tr)>|<br\s*/?>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = html.unescape(h)
    h = re.sub(r"[ \t\r\f\v\xa0]+", " ", h)
    h = re.sub(r"\s*\n\s*", "\n", h)
    return h.strip()


def _phrase_regex(phrases: Iterable[str]) -> re.Pattern[str] | None:
    parts = [r"[\s\-]+".join(re.escape(w) for w in p.split()) for p in (x.strip() for x in phrases) if p]
    return re.compile(r"\b(?:%s)\b" % "|".join(parts), re.I) if parts else None


_DEFAULT_TITLE_RE = _phrase_regex(split_csv(DEFAULT_TITLES))
_DEFAULT_EXCLUDE_RE = _phrase_regex(split_csv(DEFAULT_EXCLUDE))


def make_title_matcher(
    titles: str | Iterable[str] | None = None,
    exclude_words: str | Iterable[str] | None = None,
) -> Callable[[str], bool]:
    """Return a title filter.

    With default titles the broad AI-word AND engineer-word style also applies.
    With custom titles only the phrases you gave count.
    """
    use_default = titles is None
    t_list = split_csv(titles) if isinstance(titles, str) else list(titles or [])
    x_list = split_csv(exclude_words) if isinstance(exclude_words, str) else list(exclude_words or [])
    phrase_re = _DEFAULT_TITLE_RE if use_default else _phrase_regex(t_list)
    excl_re = _DEFAULT_EXCLUDE_RE if exclude_words is None else _phrase_regex(x_list)

    def _match(title: str) -> bool:
        t = title or ""
        t_ex = re.sub(r"(?i)technical\s+staff", "technical", t)  # MTS is not seniority
        if excl_re and excl_re.search(t_ex):
            return False
        if phrase_re and phrase_re.search(t):
            return True
        return bool(use_default and TITLE.search(t) and ENG.search(t))

    return _match


_DEFAULT_MATCHER = make_title_matcher()


def title_ok(title: str, matcher: Callable[[str], bool] | None = None) -> bool:
    """True if the job title is an in-scope, non-senior engineering title."""
    return (matcher or _DEFAULT_MATCHER)(title)


def sentences(text: str) -> list[str]:
    """Split text into sentences (newlines also end a sentence)."""
    out: list[str] = []
    for line in (text or "").split("\n"):
        out.extend(s for s in re.split(r"(?<=[.!?])\s+", line) if s.strip())
    return out


def visa_signals(desc: str) -> tuple[list[str], list[str]]:
    """Return (sponsorship sentences, relocation-only sentences). Negated sentences are skipped."""
    pos: list[str] = []
    rel: list[str] = []
    for s in sentences(desc):
        if visa_negated(s):
            continue
        m = VISA_POS.search(s)
        if m:
            only_reloc = m.group(0).lower().startswith("relocation") and not re.search(
                r"visa|sponsor|h-?1b|permit", s, re.I
            )
            (rel if only_reloc else pos).append(s.strip()[:300])
    return pos, rel


def visa_lines(desc: str) -> list[str]:
    """Up to 2 exact sentences that mention sponsorship (or, failing that, relocation)."""
    pos, rel = visa_signals(desc)
    return (pos or rel)[:2]


def years_min(desc: str) -> int | None:
    """Smallest 'N years' figure stated as experience in the text, or None."""
    mins: list[int] = []
    for m in YRS.finditer(desc or ""):
        ctx = desc[max(0, m.start() - 80) : m.end() + 80].lower()
        if any(w in ctx for w in ("experience", "background", "working", "professional")):
            mins.append(int(m.group(1)))
    return min(mins) if mins else None


def to_date(value: Any) -> str:
    """Normalise epoch seconds/ms, date objects or ISO/RFC strings to 'YYYY-MM-DD' ('' if unknown)."""
    if value in (None, "", 0):
        return ""
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.strftime("%Y-%m-%d")
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d{9,13}", value.strip())):
            n = float(value)
            if n > 1e11:
                n /= 1000.0
            return _dt.datetime.fromtimestamp(n, _dt.timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""
    s = str(value).strip()
    if re.match(r"\d{4}-\d{2}-\d{2}", s):
        return s[:10]
    try:  # RFC 822 (RSS pubDate)
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(s).strftime("%Y-%m-%d")
    except (TypeError, ValueError):
        return ""


def within_days(posted: str, days: int, today: _dt.date | None = None) -> bool:
    """True if the posted date is within `days` of today. Unknown date = keep."""
    if not posted or not re.match(r"\d{4}-\d{2}-\d{2}", posted):
        return True
    try:
        d = _dt.date.fromisoformat(posted[:10])
    except ValueError:
        return True
    return d >= (today or _dt.date.today()) - _dt.timedelta(days=days)


def eligibility_locked(desc: str, work: frozenset[str]) -> bool:
    """True if the description limits applicants to places that exclude all your work countries.

    Looks at sentences like 'must be located in X', 'authorized to work in X',
    'residents of X only'.  No work countries given = never locked.
    """
    if not work:
        return False
    for s in sentences((desc or "")[:8000])[:120]:
        if not (LOCK_A.search(s) or (LOCK_B.search(s) and re.search(r"\bonly\b", s, re.I))):
            continue
        if VISA_POS.search(s) and not visa_negated(s):
            continue
        info = parse_loc(s[:400])
        allowed = set(info.countries) | set(info.regions)
        if allowed and not (allowed & work):
            return True
    return False


def classify_location(
    loc: str,
    desc: str = "",
    prefs: Prefs | None = None,
    visa_flag: bool = False,
) -> str | None:
    """Say which rule a job matches, or None.

    Rows: work_country (on-site/hybrid in a country you can work in), remote_world,
    remote_region (remote open to, or limited to, a place that includes a work country),
    visa_onsite (on-site elsewhere and the ad says it sponsors), maybe_relocation, any.
    """
    p = prefs or Prefs()
    loc = loc or ""
    desc = desc or ""
    info = parse_loc(loc)
    c = info.countries
    remote = bool(REMOTE.search(loc))
    hybrid = bool(HYBRID.search(loc))
    remote_only = remote and not hybrid

    if c and p.exclude and c <= p.exclude:
        return None
    if p.only and not (c & p.only):
        return None

    locked = bool(p.work) and eligibility_locked(desc, p.work)
    if c & p.work:
        if remote_only:
            return "remote_region" if p.remote and not locked else None
        return None if locked else "work_country"
    if remote_only:
        if not p.remote or locked:
            return None
        if p.work and (info.regions & p.work):
            return "remote_region"
        if c:
            return None  # locked to a country you cannot work in
        if info.world or DESC_WORLD.search(desc):
            return "remote_world"
        return None
    pos, rel = visa_signals(desc)
    if (pos or visa_flag) and p.visa_onsite:
        return "visa_onsite"
    if (rel or pos or visa_flag) and p.maybe_relocation:
        return "maybe_relocation"
    return "any" if p.any_row else None


# ================================================================== HTTP
_host_locks: dict[str, threading.BoundedSemaphore] = {}
_host_lock = threading.Lock()


def _host_sem(host: str) -> threading.BoundedSemaphore:
    with _host_lock:
        return _host_locks.setdefault(host, threading.BoundedSemaphore(MAX_PER_HOST))


def _public_host(host: str) -> bool:
    """False if the host is (or resolves to) a loopback/private/link-local/reserved address."""
    h = host.rsplit("@", 1)[-1]
    h = h[1 : h.index("]")] if h.startswith("[") else h.split(":")[0]
    try:
        infos = [ipaddress.ip_address(h)]
    except ValueError:
        try:
            infos = [ipaddress.ip_address(i[4][0]) for i in socket.getaddrinfo(h, None)]
        except (OSError, ValueError):
            return True  # cannot resolve: the request itself will fail, nothing internal is reached
    return all(i.is_global for i in infos)


def check_url(url: str) -> bool:
    """True only for http(s) URLs whose host is a public address."""
    try:
        sp = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return sp.scheme in ("http", "https") and bool(sp.hostname) and _public_host(sp.netloc)


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Apply the same scheme/host check to every redirect hop."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if not check_url(urllib.parse.urljoin(req.full_url, newurl)):
            raise urllib.error.URLError("redirect to a non-public or non-http(s) address refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER: Any = None


def _open(req: urllib.request.Request, timeout: int) -> Any:
    """Open a request through an opener that checks redirects (tests patch this)."""
    global _OPENER
    if _OPENER is None:
        _OPENER = urllib.request.build_opener(_SafeRedirect)
    return _OPENER.open(req, timeout=timeout)


def http_request(
    url: str,
    params: Any = None,
    data: bytes | None = None,
    retries: int = RETRIES_429,
    max_bytes: int = 20_000_000,
    method: str | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, str, bytes]:
    """GET (or POST/other when `data`/`method` is given). Returns (status, final_url, body).

    429 backs off and retries twice. Status 0 = network error or refused URL. Max 3 in flight per host.
    Only public http(s) hosts are contacted. The User-Agent is always ours. Never raises.
    """
    try:
        if params:
            url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
        if not check_url(url):
            return 0, url, b""
        host = urllib.parse.urlsplit(url).netloc
    except (ValueError, TypeError):
        return 0, str(url), b""
    hdrs = {"Accept": "application/json, text/xml, text/html;q=0.8, */*;q=0.5"}
    hdrs.update(headers or {})
    hdrs["User-Agent"] = USER_AGENT
    wait = 0
    for attempt in range(retries + 1):
        with _host_sem(host):
            try:
                req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
                with _open(req, TIMEOUT_S) as r:
                    return r.status, r.geturl(), r.read(max_bytes)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries:
                    ra = e.headers.get("Retry-After", "") if e.headers else ""
                    wait = min(int(ra), 30) if ra.isdigit() else 4 * (attempt + 1)
                else:
                    return e.code, url, b""
            except (urllib.error.URLError, HTTPException, TimeoutError, ValueError, OSError):
                return 0, url, b""
        time.sleep(wait)
    return 429, url, b""


def decode_body(body: bytes) -> str:
    """Decode leniently: UTF-8 first, then cp1252 (legacy feeds such as Personio), then latin-1."""
    for enc in ("utf-8", "cp1252"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    return body.decode("latin-1")


def http(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    as_json: bool = True,
) -> tuple[int, Any]:
    """The shared http helper handed to every kit module (contract signature).

    Returns (status, parsed_json_or_text_or_None); status 0 on network error.
    Rate limiting (3 per host), 429 backoff and the User-Agent are built in.
    """
    st, _u, body = http_request(url, data=data, method=method, headers=headers)
    if not (200 <= st < 300):
        return st, None
    text = decode_body(body)
    if not as_json:
        return st, text
    try:
        return st, json.loads(text)
    except ValueError:
        return 0, None


def http_get_json(url: str, params: Any = None, retries: int = RETRIES_429, data: Any = None) -> tuple[int, Any]:
    """GET/POST and parse JSON. Returns (status, data); data is None on failure."""
    body = json.dumps(data).encode() if data is not None else None
    st, _u, b = http_request(url, params, body, retries, headers={"Content-Type": "application/json"} if body else None)
    if st != 200:
        return st, None
    try:
        return 200, json.loads(b.decode("utf-8", "replace"))
    except ValueError:
        return 0, None


def http_get_text(url: str, params: Any = None, retries: int = RETRIES_429) -> tuple[int, str]:
    """GET and decode text. Returns (status, text)."""
    st, _u, b = http_request(url, params, None, retries)
    return st, b.decode("utf-8", "replace") if st == 200 else ""


# ============================================================= adapters
# Each adapter has a pure parse_* function (offline testable) and a fetch wrapper.
def parse_greenhouse(d: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """Greenhouse boards-api payload to raw jobs."""
    return [
        dict(
            title=j.get("title", ""),
            url=j.get("absolute_url"),
            loc=(j.get("location") or {}).get("name", ""),
            posted=to_date(j.get("first_published") or j.get("updated_at")),
            desc=html_to_text(j.get("content")),
            company=j.get("company_name") or b["name"],
            board=b["token"],
        )
        for j in d.get("jobs", [])
    ]


def parse_lever(d: list[Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """Lever postings payload to raw jobs."""
    out = []
    for j in d:
        c = j.get("categories") or {}
        loc = " | ".join(
            x
            for x in [
                c.get("location") or "",
                "; ".join(c.get("allLocations") or []),
                j.get("workplaceType") or "",
                j.get("country") or "",
            ]
            if x
        )
        desc = (
            (j.get("descriptionPlain") or "")
            + "\n"
            + "\n".join((x.get("text", "") + "\n" + html_to_text(x.get("content"))) for x in j.get("lists", []))
            + "\n"
            + (j.get("additionalPlain") or "")
        )
        out.append(
            dict(
                title=j.get("text", ""),
                url=j.get("hostedUrl"),
                loc=loc,
                posted=to_date(j.get("createdAt")),
                desc=re.sub(r"\n{3,}", "\n\n", desc).strip(),
                company=b["name"],
                board=b["token"],
            )
        )
    return out


def parse_ashby(d: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """Ashby posting-api payload to raw jobs."""
    out = []
    for j in d.get("jobs", []):
        pa = (j.get("address") or {}).get("postalAddress") or {}
        sec = "; ".join(str(s.get("location") if isinstance(s, dict) else s) for s in j.get("secondaryLocations") or [])
        parts = [
            j.get("location") or "",
            j.get("workplaceType") or "",
            "Remote" if j.get("isRemote") else "",
            pa.get("addressCountry") or "",
            sec,
        ]
        out.append(
            dict(
                title=j.get("title", ""),
                url=j.get("jobUrl"),
                loc=" | ".join(p for p in parts if p),
                posted=to_date(j.get("publishedAt")),
                desc=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                company=b["name"],
                board=b["token"],
            )
        )
    return out


def parse_smartrecruiters_list(d: dict[str, Any], b: dict[str, Any],
        detail: Callable[[str], dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """SmartRecruiters postings payload to raw jobs."""
    out = []
    for j in d.get("content", []):
        lc = j.get("location") or {}
        loc = " | ".join(
            x
            for x in [
                lc.get("city", ""),
                lc.get("country", ""),
                "remote" if lc.get("remote") else "",
                "hybrid" if lc.get("hybrid") else "",
            ]
            if x
        )
        raw = dict(
            title=j.get("name", ""),
            url="https://jobs.smartrecruiters.com/%s/%s" % (b["token"], j.get("id")),
            loc=loc,
            posted=to_date(j.get("releasedDate")),
            desc="",
            company=(j.get("company") or {}).get("name") or b["name"],
            board=b["token"],
        )
        if detail:
            raw["detail"] = functools.partial(detail, j.get("id"))
        out.append(raw)
    return out


# ================================================================ collector
class Collector:
    """Thread-safe stats + results, applying the shared filter pipeline."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.stats: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        self.results: list[dict[str, Any]] = []
        self.seen_url: set[str] = set()
        self.seen_key: dict[str, str] = {}
        self.matcher = make_title_matcher(args.titles, args.exclude_title_words)
        self.prefs = make_prefs(
            args.work_countries,
            args.remote,
            args.visa_onsite,
            args.maybe_relocation,
            args.only_countries,
            args.exclude_countries,
            args.any_location,
        )
        self.skip = [s.lower() for s in split_csv(args.skip_companies)]

    def bump(self, source: str, key: str, n: int = 1) -> None:
        with self.lock:
            self.stats[source][key] += n

    def skipped(self, *names: str) -> bool:
        low = " ".join(n.lower() for n in names if n)
        return any(s in low for s in self.skip)

    def offer_job(self, label: str, job: dict[str, Any]) -> None:
        """Offer a contract-shaped Job dict (title, company, url, location, posted, description, ...)."""
        loc = job.get("location") or ""
        if job.get("remote") is True and not REMOTE.search(loc):
            loc = (loc + " | Remote").strip(" |")
        self.offer(
            label,
            dict(
                title=job.get("title", ""),
                url=job.get("url"),
                loc=loc,
                posted=job.get("posted") or "",
                desc=job.get("description") or "",
                company=job.get("company", ""),
                board=job.get("board", ""),
                visa_flag=job.get("visa_flag") is True,
            ),
            rec_source=job.get("source") or label,
        )

    def offer(self, source: str, raw: dict[str, Any], rec_source: str | None = None) -> None:
        """Run one raw job through title, location, years, date filters and keep it."""
        a = self.args
        self.bump(source, "jobs_seen")
        title = raw.get("title") or ""
        url = raw.get("url") or ""
        if not isinstance(url, str) or urllib.parse.urlsplit(url.strip()).scheme not in ("http", "https"):
            return  # javascript:, data:, file: and relative links are never kept
        if not self.matcher(title):
            return
        self.bump(source, "title_matches")
        if self.skipped(raw.get("company", ""), raw.get("board", "")):
            return
        det = raw.get("detail")
        if callable(det):  # lazy detail fetch, only for title matches
            extra = det() or {}
            if extra.get("loc_extra"):
                raw["loc"] = (raw.get("loc", "") + " | " + extra["loc_extra"]).strip(" |")
            raw["desc"] = extra.get("desc") or raw.get("desc", "")
            raw["posted"] = extra.get("posted") or raw.get("posted", "")
        desc = raw.get("desc") or ""
        row = classify_location(raw.get("loc", ""), desc, self.prefs, bool(raw.get("visa_flag")))
        if not row:
            return
        self.bump(source, "location_matches")
        y = years_min(desc)
        if y is not None and y >= a.max_years:
            return
        if not within_days(raw.get("posted", ""), a.days):
            return
        self.bump(source, "years_ok")
        lines = visa_lines(desc)
        if not lines and raw.get("visa_flag"):
            lines = ["Feed field says visa sponsorship is offered."]
        rec = {
            "title": title,
            "company": raw.get("company") or raw.get("board") or "",
            "url": url,
            "location": re.sub(r"\s*\|\s*", " | ", (raw.get("loc") or "").strip(" |")),
            "posted": raw.get("posted", ""),
            "source": rec_source or source,
            "board": raw.get("board", ""),
            "row": row,
            "years_min": y,
            "visa_lines": lines,
            "description": desc,
        }
        key = job_key(rec)
        with self.lock:
            if url in self.seen_url:
                return
            self.seen_url.add(url)
            first = self.seen_key.setdefault(key, url)
            if first != url:
                rec["possible_duplicate_of"] = first  # same company+title, different URL: flag, never hide
            self.results.append(rec)
            self.stats[source]["kept"] += 1


# ================================================================= sources
def _offer_all(col: Collector, source: str, raws: Iterable[dict[str, Any]]) -> None:
    for r in raws:
        col.offer(source, r)


def _board_json(
    col: Collector, source: str, b: dict[str, Any], url: str,
    parse: Callable[[Any, dict[str, Any]], list[dict[str, Any]]],
    kind: type = dict
) -> None:
    col.bump(source, "boards_tried")
    st, d = http_get_json(url)
    if st != 200 or not isinstance(d, kind):
        return
    col.bump(source, "boards_ok")
    _offer_all(col, source, parse(d, b))


def _gh(col: Collector, b: dict[str, Any]) -> None:
    _board_json(
        col,
        "greenhouse",
        b,
        "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % b["token"],
        parse_greenhouse,
    )


def _lever(col: Collector, b: dict[str, Any]) -> None:
    col.bump("lever", "boards_tried")
    for base in ("https://api.lever.co", "https://api.eu.lever.co"):
        st, d = http_get_json("%s/v0/postings/%s?mode=json" % (base, b["token"]), retries=1)
        if st == 200 and isinstance(d, list):
            col.bump("lever", "boards_ok")
            _offer_all(col, "lever", parse_lever(d, b))
            return


def _ashby(col: Collector, b: dict[str, Any]) -> None:
    _board_json(
        col,
        "ashby",
        b,
        "https://api.ashbyhq.com/posting-api/job-board/%s?includeCompensation=true" % b["token"],
        parse_ashby,
    )


def _smartrecruiters(col: Collector, b: dict[str, Any]) -> None:
    t = b["token"]

    def detail(pid: str) -> dict[str, Any]:
        st, dd = http_get_json("https://api.smartrecruiters.com/v1/companies/%s/postings/%s" % (t, pid))
        if st != 200 or not isinstance(dd, dict):
            return {}
        secs = (dd.get("jobAd") or {}).get("sections") or {}
        return {"desc": "\n".join(html_to_text(v.get("text")) for v in secs.values() if isinstance(v, dict))}

    col.bump("smartrecruiters", "boards_tried")
    off, first = 0, True
    while True:
        st, d = http_get_json("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100&offset=%d" % (t, off))
        if st != 200 or not isinstance(d, dict):
            return
        if first:
            col.bump("smartrecruiters", "boards_ok")
            first = False
        _offer_all(col, "smartrecruiters", parse_smartrecruiters_list(d, b, detail))
        off += 100
        if off >= d.get("totalFound", 0) or not d.get("content"):
            return


def _freehire(col: Collector, queries: list[str]) -> None:
    pr = col.prefs
    pl = get_places()
    filters: list[dict[str, str]] = []
    if pr.remote:
        filters += [dict(work_mode="remote", regions="global"), dict(work_mode="remote", regions="none")]
        if pl.region_members("apac") & pr.work:
            filters.append(dict(work_mode="remote", regions="apac"))
    filters += [dict(countries=cc) for cc in sorted(pr.work)]
    if not filters:
        filters.append({})
    jobs: dict[str, dict[str, Any]] = {}
    for q in queries:
        for f in filters:
            for page in range(4):
                col.bump("freehire", "boards_tried")
                p = dict(
                    q=q,
                    limit=50,
                    offset=page * 50,
                    semantic_ratio=0,
                    include_description="true",
                    description_format="text",
                    posted_within_days=col.args.days,
                )
                p.update(f)
                st, d = http_get_json("https://freehire.me/api/v1/agent/jobs/search", p)
                if st != 200 or not isinstance(d, dict):
                    break
                col.bump("freehire", "boards_ok")
                rows = d.get("data") or []
                for r in rows:
                    jobs[r.get("public_slug") or r.get("url") or str(len(jobs))] = r
                if len(rows) < 50:
                    break
    for r in jobs.values():
        url = r.get("url") or ""
        if r.get("source") == "telegram" or re.search(r"linkedin\.com|indeed\.|glassdoor", url, re.I):
            continue  # copies of ads from big boards: those only run through --boards
        wm = r.get("work_mode") or ""
        regions = r.get("regions") or []
        extra = (["Worldwide"] if "global" in regions else []) + (["APAC"] if "apac" in regions else [])
        extra += [country_name(c) for c in r.get("countries") or []]
        loc = " | ".join(x for x in [r.get("location") or "", wm, "Remote" if wm == "remote" else "", *extra] if x)
        col.offer(
            "freehire",
            dict(
                title=r.get("title", ""),
                url=url,
                loc=loc,
                posted=to_date(r.get("posted_at")),
                desc=r.get("description") or "",
                company=r.get("company") or "",
                board=r.get("source") or "",
            ),
        )


# ============================================================ run / output
_BOARD_FN: dict[str, Callable[[Collector, dict[str, Any]], None]] = {
    "greenhouse": _gh,
    "lever": _lever,
    "ashby": _ashby,
    "smartrecruiters": _smartrecruiters,
}
_SOURCE_RANK = {"user": -1, "career-ops": 0, "jobsync": 1}


def parse_extra(value: str | None) -> list[dict[str, str]]:
    """Parse --companies-extra "ats:token,ats:token" into board rows (these run first)."""
    out: list[dict[str, str]] = []
    for item in split_csv(value):
        ats, _, tok = item.partition(":")
        ats, tok = ats.strip().lower(), tok.strip()
        if ats in BOARD_SOURCES and ats != "workday" and re.fullmatch(r"[A-Za-z0-9_.-]+", tok):
            out.append({"name": tok, "ats": ats, "token": tok, "source": "user"})
    return out


def load_companies(path: Path) -> list[dict[str, str]]:
    """Read the company list (JSON array of {name, ats, board, source[, tenant, wd, site]}).

    The file names the board slug ``board`` (a public company board name, e.g.
    "safetyculture-2") because secret scanners read ``"token": "<slug>"`` as an API
    key. Rows keep a ``token`` key in memory, which every fetcher reads; an older
    file that still says ``token`` loads too.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for r in data:
        if not r.get("token") and r.get("board"):
            r["token"] = r["board"]
    return [r for r in data if r.get("ats") in BOARD_SOURCES and r.get("token")]


UNIVERSE_BASE = "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/ab0f66fc09c9239bb4a36f5d0edb2dcaf9dcacf2/data/%s_companies.json"
UNIVERSE_ATS = ("greenhouse", "lever", "ashby", "bamboohr", "workday")
UNIVERSE_MAX_AGE_DAYS = 7
BIG_BOARD_NAME = re.compile(r"linkedin|indeed|glassdoor", re.I)


def universe_rows(ats: str, data: Any) -> list[dict[str, str]]:
    """Turn one downloaded universe list (list of tokens; workday 'tenant|wd|site') into board rows."""
    out: list[dict[str, str]] = []
    for t in data if isinstance(data, list) else []:
        if not isinstance(t, str) or BIG_BOARD_NAME.search(t):
            continue
        if ats == "workday":
            parts = t.split("|")
            if len(parts) != 3 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", x) for x in parts):
                continue
            out.append(
                {
                    "name": parts[0],
                    "ats": ats,
                    "token": t,
                    "tenant": parts[0],
                    "wd": parts[1],
                    "site": parts[2],
                    "source": "universe",
                }
            )
        elif re.fullmatch(r"[A-Za-z0-9_.-]+", t):
            out.append({"name": t, "ats": ats, "token": t, "source": "universe"})
    return out


def load_universe(
    ats_list: Iterable[str],
    cache_dir: Path | None = None,
    now: float | None = None,
) -> list[dict[str, str]]:
    """Board lists from the open job-board-aggregator project (MIT), fetched at run time.

    Cached in universe_cache/ next to the script for 7 days. A failed download warns and is skipped
    (a stale cache file is still used if there is one).
    """
    cache = cache_dir or (HERE / "universe_cache")
    t_now = time.time() if now is None else now
    rows: list[dict[str, str]] = []
    for ats in ats_list:
        if ats not in UNIVERSE_ATS:
            continue
        f = cache / ("%s_companies.json" % ats)
        data: Any = None
        try:
            if f.exists() and t_now - f.stat().st_mtime < UNIVERSE_MAX_AGE_DAYS * 86400:
                data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if data is None:
            try:
                st, got = http(UNIVERSE_BASE % ats)
            except Exception:  # noqa: BLE001 - the universe is optional; never abort the run
                st, got = 0, None
            if st == 200 and isinstance(got, list):
                data = got
                try:
                    cache.mkdir(parents=True, exist_ok=True)
                    f.write_text(json.dumps(got), encoding="utf-8")
                except OSError:
                    pass
            else:
                print(
                    "warning: could not download the %s company list (status %s); using companies.json only"
                    % (ats, st),
                    file=sys.stderr,
                )
                try:
                    data = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
                except (OSError, ValueError):
                    data = None
        rows += universe_rows(ats, data)
    return rows


def select_sources(value: str) -> list[str]:
    """'all' = everything; 'default' = all except the two giant lists; else a comma list."""
    v = (value or "default").strip().lower()
    if v == "all":
        return list(ALL_SOURCES)
    if v == "default":
        return [s for s in ALL_SOURCES if s not in DEFAULT_SKIP]
    return [SOURCE_ALIASES.get(s, s) for s in split_csv(v) if SOURCE_ALIASES.get(s, s) in ALL_SOURCES]


def _merge_stats(col: Collector, label: str, st: dict[str, Any]) -> None:
    if label == "jobspy":  # one row per site, honest about sites that return 0 or fail
        for site in JOBSPY_SITES:
            if st.get(site + "_tried"):
                col.bump("jobspy:" + site, "boards_tried", int(st.get(site + "_tried", 0)))
                col.bump("jobspy:" + site, "boards_ok", int(st.get(site + "_ok", 0)))
        for note in st.get("notes") or []:
            print("note: %s" % note, file=sys.stderr)
        return
    col.bump(label, "boards_tried", int(st.get("tried", 0)))
    col.bump(label, "boards_ok", int(st.get("ok", 0)))


def _module_task(
    col: Collector, label: str, call: Callable[[dict[str, int]], list[dict[str, Any]]], by_job_source: bool = False
) -> None:
    """Run one module call, merge its tried/ok stats, and offer every Job it returns."""
    st: dict[str, int] = {"tried": 0, "ok": 0, "jobs": 0}
    try:
        jobs = call(st) or []
    except Exception as e:  # modules must not raise; if one does, report and move on
        print("warning: %s failed: %s: %s" % (label, type(e).__name__, e), file=sys.stderr)
        jobs = []
    _merge_stats(col, label, st)
    for j in jobs:
        col.offer_job(j.get("source") or label if by_job_source else label, j)


def _ats_call(fetch: Callable[..., list[dict[str, Any]]], board: dict[str, Any],
              st: dict[str, int]) -> list[dict[str, Any]]:
    """Call one ATS fetcher with the kit's http function."""
    return fetch(board, http, st)


def _feed_call(fetch: Callable[..., list[dict[str, Any]]], queries: list[str], days: int,
               countries: list[str], st: dict[str, int]) -> list[dict[str, Any]]:
    """Call one open-feed fetcher with the kit's http function."""
    return fetch(queries, http, st, days=days, countries=countries)


def run(args: argparse.Namespace) -> Collector:
    """Fetch every selected source and return the Collector with results and stats."""
    col = Collector(args)
    sources = select_sources(args.sources)
    queries = split_csv(args.titles or DEFAULT_TITLES)[:12]
    countries = sorted(col.prefs.only or col.prefs.work)
    tasks: list[Callable[[], None]] = []

    base = load_companies(Path(args.companies))
    extra_universe = load_universe([a for a in UNIVERSE_ATS if a in sources]) if args.universe else []
    boards = parse_extra(args.companies_extra) + base + extra_universe
    seen_b: set[tuple[str, str]] = set()
    uniq = []
    for r in boards:
        k = (r["ats"], r["token"].lower())
        if k not in seen_b:
            seen_b.add(k)
            uniq.append(r)
    boards = [r for r in uniq if r["ats"] in sources and not col.skipped(r["name"], r["token"])]
    if args.limit_boards:
        counts: collections.Counter[str] = collections.Counter()
        picked = []
        for b in sorted(boards, key=lambda r: _SOURCE_RANK.get(r.get("source", ""), 2)):
            if counts[b["ats"]] < args.limit_boards:
                counts[b["ats"]] += 1
                picked.append(b)
        boards = picked
    kit_ats = _import_kit("kit_ats") if any(b["ats"] not in _BOARD_FN for b in boards) else None
    ats_fetchers = getattr(kit_ats, "ATS_FETCHERS", {}) if kit_ats else {}
    for b in boards:
        if b["ats"] in _BOARD_FN:
            tasks.append(functools.partial(_BOARD_FN[b["ats"]], col, b))
        elif b["ats"] in ats_fetchers:
            call = functools.partial(_ats_call, ats_fetchers[b["ats"]], b)
            tasks.append(functools.partial(_module_task, col, b["ats"], call))
    if "freehire" in sources:
        tasks.append(lambda: _freehire(col, queries))
    wanted_feeds = [s for s in sources if s in FEED_SOURCES and s != "freehire"]
    if wanted_feeds:
        kit_feeds = _import_kit("kit_feeds")
        feeds = getattr(kit_feeds, "FEEDS", {}) if kit_feeds else {}
        for name in wanted_feeds:
            if name in feeds:
                tasks.append(functools.partial(
                    _module_task, col, name,
                    functools.partial(_feed_call, feeds[name], queries, args.days, countries),
                ))
            elif kit_feeds is not None:
                print("note: feed %s not provided by kit_feeds; skipped" % name, file=sys.stderr)
    boards_opt = [s for s in split_csv(args.boards) if s in JOBSPY_SITES]
    if boards_opt:
        kit_boards = _import_kit("kit_boards")
        if kit_boards is not None:
            tasks.append(
                lambda: _module_task(
                    col,
                    "jobspy",
                    lambda st: kit_boards.run_boards(
                        boards_opt,
                        queries,
                        days=args.days,
                        remote=col.prefs.remote,
                        countries=countries,
                        results_wanted=max(1, min(int(args.results_wanted), 200)),
                        full_text=bool(args.full_text),
                        stats=st,
                    ),
                    by_job_source=True,
                )
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, args.workers))) as ex:
        for f in concurrent.futures.as_completed([ex.submit(t) for t in tasks]):
            try:
                f.result()
            except Exception as e:  # one bad board must not stop the sweep
                print("warning: task failed: %s: %s" % (type(e).__name__, e), file=sys.stderr)
    return col


def sort_newest(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Newest posted first; unknown dates last. No scoring, no ranking by fit."""
    out = sorted(results, key=lambda r: (r["company"].lower(), r["title"].lower()))
    return sorted(out, key=lambda r: r.get("posted") or "0000-00-00", reverse=True)


# -------------------------------------------------------------- history
def job_key(rec: dict[str, Any]) -> str:
    """'company|title' key, used only to flag possible duplicates."""
    return str(rec.get("company", "") + "|" + rec.get("title", "")).lower()


def load_history(path: str | None) -> tuple[set[str], set[str]]:
    """Read seen urls and 'company|title' keys from the history file (missing = empty)."""
    if not path or path.lower() == "none":
        return set(), set()
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return set(d.get("urls", [])), set(d.get("keys", []))
    except (OSError, ValueError, AttributeError):
        return set(), set()


def filter_new(results: list[dict[str, Any]], urls: set[str], keys: set[str]) -> tuple[list[dict[str, Any]], int]:
    """Drop jobs already seen. Returns (new_results, skipped_count)."""
    new = [r for r in results if r["url"] not in urls]  # URL is the identity; keys are informational
    return new, len(results) - len(new)


def save_history(path: str | None, urls: set[str], keys: set[str], results: list[dict[str, Any]]) -> None:
    """Add this run's output to the history file."""
    if not path or path.lower() == "none":
        return
    u = sorted(urls | {r["url"] for r in results})
    k = sorted(keys | {job_key(r) for r in results})
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"urls": u, "keys": k}, f)


# ------------------------------------------------------------ check-live
def is_dead_page(status: int, final_url: str, text: str) -> bool:
    """True if a job page shows the posting is gone (404/410, redirect with error, closed wording)."""
    if status in (404, 410):
        return True
    if status != 200:
        return False
    if re.search(r"[?&]error=true", final_url or ""):
        return True
    return bool(DEAD_PAGE.search(text[:200_000]))


def check_live(col: Collector, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Re-fetch each kept job once and drop the ones that are gone."""

    def one(rec: dict[str, Any]) -> bool:
        try:
            st, final, body = http_request(rec["url"], retries=1, max_bytes=300_000)
            dead = is_dead_page(st, final, body.decode("utf-8", "replace"))
        except Exception:  # a failed check keeps the job (never abort the run)
            return True
        col.bump(rec["source"], "live_checked")
        if dead:
            col.bump(rec["source"], "live_dead")
        return not dead

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        flags = list(ex.map(one, results))
    return [r for r, ok in zip(results, flags) if ok]


# -------------------------------------------------------------- discover
def slug_variants(name: str) -> list[str]:
    """Likely board tokens for a company name (joined, hyphenated, first word, ...)."""
    w = fold(name).split()
    if not w:
        return []
    cand = ["".join(w), "-".join(w), w[0], "".join(w) + "hq", "".join(w) + "inc", "".join(w) + "ai"]
    seen: list[str] = []
    for c in cand:
        if c and c not in seen:
            seen.append(c)
    return seen


def _titles_of(d: Any, key: str, tkey: str) -> list[str]:
    rows = d.get(key, []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
    return cast("list[str]", [j.get(tkey) for j in rows if isinstance(j, dict) and j.get(tkey)])


def discover(names: list[str]) -> list[dict[str, Any]]:
    """Probe likely board tokens on greenhouse/lever/ashby/workable/recruitee. Returns hits with jobs."""
    probes: dict[str, tuple[str, str, str]] = {
        "greenhouse": ("https://boards-api.greenhouse.io/v1/boards/%s/jobs", "jobs", "title"),
        "lever": ("https://api.lever.co/v0/postings/%s?mode=json", "", "text"),
        "ashby": ("https://api.ashbyhq.com/posting-api/job-board/%s", "jobs", "title"),
        "workable": ("https://apply.workable.com/api/v1/widget/accounts/%s", "jobs", "title"),
        "recruitee": ("https://%s.recruitee.com/api/offers/", "offers", "title"),
    }

    def one(job: tuple[str, str, str]) -> dict[str, Any] | None:
        name, ats, tok = job
        url, key, tkey = probes[ats]
        st, d = http_get_json(url % tok, retries=1)
        titles = _titles_of(d, key, tkey) if st == 200 else []
        if not titles:
            return None
        return {"name": name, "ats": ats, "token": tok, "jobs": len(titles), "sample_titles": titles[:3]}

    work = [(n, a, t) for n in names for t in slug_variants(n) for a in probes]
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        return [r for r in ex.map(one, work) if r]


# ------------------------------------------------------------------ CLI
def format_stats(col: Collector, skipped_seen: int = 0) -> str:
    """Human-readable coverage table."""
    cols = ("boards_tried", "boards_ok", "jobs_seen", "title_matches", "location_matches", "years_ok", "kept")
    lines = ["%-22s" % "source" + "".join("%17s" % c for c in cols)]
    for s in sorted(col.stats):
        st = col.stats[s]
        note = ""
        if st["boards_tried"] and not st["boards_ok"]:
            note = "  <- answered nothing (blocked, broken or no key)"
        if st["errors"]:
            note += "  errors=%d" % st["errors"]
        if st["live_checked"]:
            note += "  live_checked=%d dead=%d" % (st["live_checked"], st["live_dead"])
        lines.append("%-22s" % s + "".join("%17d" % st[c] for c in cols) + note)
    lines.append("total kept: %d   skipped as already seen: %d" % (len(col.results), skipped_seen))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """CLI definition."""
    p = argparse.ArgumentParser(description="Job360 hunt kit: public job boards + open feeds. No logins. No ranking.")
    p.add_argument("--titles", default=None, help="comma list of job titles to keep (default: AI/LLM/agent titles)")
    p.add_argument("--exclude-title-words", default=DEFAULT_EXCLUDE, help="comma list of words that drop a title")
    p.add_argument("--work-countries", default="", help="ISO2 list where you need NO visa, e.g. IN,PT")
    p.add_argument(
        "--remote",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep remote ads open worldwide or to a place that includes a work country",
    )
    p.add_argument(
        "--visa-onsite",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="keep on-site ads elsewhere only if the ad says it sponsors visas",
    )
    p.add_argument("--maybe-relocation", action="store_true", help="also keep ads that only mention relocation")
    p.add_argument("--only-countries", default="", help="optional ISO2 filter: keep only these countries")
    p.add_argument("--exclude-countries", default="", help="optional ISO2 filter: drop these countries")
    p.add_argument("--any-location", action="store_true", help="keep every title match regardless of place")
    p.add_argument("--max-years", type=int, default=5, help="drop jobs whose minimum stated years >= N")
    p.add_argument("--days", type=int, default=30, help="only jobs posted in the last N days")
    p.add_argument(
        "--sources",
        default="default",
        help="'default', 'all' (adds bamboohr+workday), or a comma list of: " + ",".join(ALL_SOURCES),
    )
    p.add_argument("--companies", default=str(Path(__file__).resolve().parent / "companies.json"))
    p.add_argument(
        "--universe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="also fetch the open job-board-aggregator company lists (cached 7 days)",
    )
    p.add_argument("--companies-extra", default="", help="your own boards first, e.g. ashby:acme,lever:foo")
    p.add_argument("--skip-companies", default="", help="comma list; names or tokens containing these are skipped")
    p.add_argument("--limit-boards", type=int, default=0, help="max boards per ATS (quick runs)")
    p.add_argument("--workers", type=int, default=MAX_WORKERS, help="parallel workers (max 8)")
    p.add_argument("--discover", default="", help='probe board tokens for "Company A,Company B" and print hits')
    p.add_argument("--check-live", action="store_true", help="re-fetch each kept job once; drop 404/410/closed")
    p.add_argument(
        "--history", default="seen_jobs.json", help="remember output; next run shows only NEW jobs ('none' = off)"
    )
    p.add_argument("--all", action="store_true", help="ignore history when choosing what to output")
    p.add_argument("--boards", default="", help="OPT-IN big boards via python-jobspy: " + ",".join(JOBSPY_SITES))
    p.add_argument("--results-wanted", type=int, default=50, help="per search, with --boards (cap 200)")
    p.add_argument(
        "--full-text", action="store_true", help="with --boards: fetch LinkedIn full text (one request per job)"
    )
    p.add_argument("--out", default="-", help="JSONL output path (default stdout)")
    p.add_argument("--stats", action="store_true", help="print coverage counts per source to stderr")
    return p


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        make_prefs(args.work_countries, only=args.only_countries, exclude=args.exclude_countries)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    bad = [s for s in split_csv(args.boards) if s not in JOBSPY_SITES]
    if bad:
        print("unknown --boards value: %s (use %s)" % (",".join(bad), ",".join(JOBSPY_SITES)), file=sys.stderr)
        return 2
    if args.discover:
        for hit in discover(split_csv(args.discover)):
            print(json.dumps(hit, ensure_ascii=False))
        return 0
    col = run(args)
    results = sort_newest(col.results)
    urls, keys = load_history(args.history)
    skipped = 0
    try:
        results, skipped = filter_new(results, set() if args.all else urls, set() if args.all else keys)
        if args.check_live:
            results = check_live(col, results)
    except Exception as e:  # a late step must not lose the results
        print("warning: late step failed: %s: %s" % (type(e).__name__, e), file=sys.stderr)
    col.results = results
    try:
        if args.out == "-":
            for r in results:
                sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
        else:
            with open(args.out, "w", encoding="utf-8") as f:
                for r in results:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
    finally:
        try:
            save_history(args.history, urls, keys, results)
        except OSError as e:
            print("warning: could not write history: %s" % e, file=sys.stderr)
    if args.stats or skipped:
        print(format_stats(col, skipped), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
