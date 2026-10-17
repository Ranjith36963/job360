#!/usr/bin/env python3
"""Job360 hunt kit: a no-login job finder for YOUR assistant to run on YOUR machine.

It reads the public JSON job boards that companies publish themselves (Greenhouse,
Lever, Ashby, SmartRecruiters, Workable, Recruitee, Personio, Teamtailor, BambooHR,
Workday) plus open job feeds (freehire.me, Himalayas, Remotive, Arbeitnow, RemoteOK,
Jobicy, Working Nomads, We Work Remotely, The Muse, Hacker News "Who is hiring").
Official keyed APIs (Adzuna, Jooble) run only if you set their env vars.  It never
reads Indeed, LinkedIn, Glassdoor or other big job sites; the assistant opens Indeed
and LinkedIn's logged-out pages itself, slowly (README "Indeed and LinkedIn").

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
import hashlib
import html
import importlib
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Iterable, cast

USER_AGENT = "job360-hunt-kit/1.0 (+https://job360.uk)"
MAX_WORKERS = 8
MAX_PER_HOST = 3
TIMEOUT_S = 30
RETRIES_429 = 2
TRANSIENT_5XX = (502, 503, 504)  # retried once, GET only; every other 4xx/5xx fails fast

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
    "githublists",
)
SOURCE_ALIASES = {"weworkremotely": "wwr", "we-work-remotely": "wwr", "hackernews": "hn", "simplify": "githublists"}
ALL_SOURCES = BOARD_SOURCES + FEED_SOURCES
# "default" skips the giant unvetted BambooHR list (~11k boards). Workday runs by default, but only a
# daily rotating slice of its open list (--workday-max), each board searched by the user's titles.
DEFAULT_SKIP = ("bamboohr",)
WORKDAY_MAX = 300  # universe Workday boards per run; the slice moves every day
WORKDAY_SEARCH_TITLES = 6  # Workday boards are searched by at most this many titles

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
        if c:
            # An explicit country list that excludes every work country wins over a region tag
            # ("Remote | APAC | Thailand" is a Thailand job, not an all-APAC one).
            return None
        if p.work and (info.regions & p.work):
            return "remote_region"
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

    429 backs off, retries twice; 502/503/504 on a GET retry once; other 4xx never retry.
    Status 0 = network error or refused URL. Max 3 in flight per host.
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
    retried_5xx = False
    attempt = 0  # counts 429 retries only; the one 5xx retry never eats one
    while True:
        with _host_sem(host):
            try:
                req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
                with _open(req, TIMEOUT_S) as r:
                    return r.status, r.geturl(), r.read(max_bytes)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries:
                    ra = e.headers.get("Retry-After", "") if e.headers else ""
                    wait = min(int(ra), 30) if ra.isdigit() else 4 * (attempt + 1)
                    attempt += 1
                elif e.code in TRANSIENT_5XX and not retried_5xx and data is None and method in (None, "GET"):
                    retried_5xx = True
                    wait = 2
                else:
                    return e.code, url, b""
            except (urllib.error.URLError, HTTPException, TimeoutError, ValueError, OSError):
                return 0, url, b""
        time.sleep(wait)


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


# ================================================================== stream
def _now() -> float:
    return time.monotonic()


FINGERPRINT_ARGS = (
    "titles", "exclude_title_words", "work_countries", "remote", "visa_onsite", "maybe_relocation",
    "only_countries", "exclude_countries", "any_location", "max_years", "days", "sources", "companies",
    "universe", "companies_extra", "skip_companies", "limit_boards", "freehire_pages", "workday_max",
)


def run_fingerprint(args: argparse.Namespace) -> str:
    """Hash of the options that decide WHAT is searched. A resume only continues the same search."""
    d = {k: getattr(args, k, None) for k in FINGERPRINT_ARGS}
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


class Stream:
    """Crash-safe output: every kept job is appended to a JSONL file the moment it is found.

    Next to it sits a small progress file (``<stream>.progress``): a header line with the run's
    fingerprint, one line per finished source, and ``#complete`` at the very end. ``--resume`` skips
    the finished sources and keeps the jobs already on disk. Plain files, no database.
    """

    def __init__(self, path: str, fingerprint: str, max_minutes: float = 0, max_sources: int = 0) -> None:
        self.path = path
        self.progress_path = path + ".progress"
        self.fingerprint = fingerprint
        self.deadline = _now() + max_minutes * 60 if max_minutes and max_minutes > 0 else None
        self.max_sources = max(0, int(max_sources or 0))
        self.t0 = _now()
        self.lock = threading.Lock()
        self.done: set[str] = set()
        self.complete = False
        self.count = 0  # jobs on disk (including any from the run being resumed)
        self.started = 0  # sources begun this session
        self.stop_reason = ""
        self._fh: Any = None
        self._pfh: Any = None

    def open(self, resume: bool) -> str:
        """Start or continue. Returns '', 'resumed', 'complete' or 'mismatch' (mismatch = started fresh)."""
        state = ""
        header, done, complete = self._read_progress()
        if resume and header is not None:
            if header != self.fingerprint:
                print("note: --resume ignored: the saved run in %s was a different search; starting fresh." % self.path,
                      file=sys.stderr)
                state = "mismatch"
            elif complete:
                self.complete = True
                self.count = sum(1 for _ in self.iter_records())
                return "complete"
            else:
                self.done = done
                state = "resumed"
        elif resume:
            print("note: --resume: nothing saved in %s yet; starting fresh." % self.path, file=sys.stderr)
        if state != "resumed":
            self._set_aside_old()
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        if state == "resumed":
            half_line = False
            try:
                with open(self.path, "rb") as r:
                    r.seek(0, os.SEEK_END)
                    if r.tell():
                        r.seek(-1, os.SEEK_END)
                        half_line = r.read(1) != b"\n"
            except OSError:
                pass
            self._fh = open(self.path, "a", encoding="utf-8")
            if half_line:  # a kill can leave a half line: end it so new lines start clean
                self._fh.write("\n")
            self.count = sum(1 for _ in self.iter_records())
            self._pfh = open(self.progress_path, "a", encoding="utf-8")
        else:
            self._fh = open(self.path, "w", encoding="utf-8")
            self._pfh = open(self.progress_path, "w", encoding="utf-8")
            self._pfh.write(self.fingerprint + "\n")
        self._pfh.flush()
        self._fh.flush()
        return state

    def _read_progress(self) -> tuple[str | None, set[str], bool]:
        try:
            with open(self.progress_path, encoding="utf-8") as f:
                lines = f.read().split("\n")
        except OSError:
            return None, set(), False
        lines = [x for x in lines[:-1] if x]  # the last piece has no newline: a cut-short write, ignore it
        if not lines:
            return None, set(), False
        body = lines[1:]
        return lines[0], {x for x in body if x != "#complete"}, "#complete" in body

    def _set_aside_old(self) -> None:
        """A fresh start never destroys an unfinished run: its jobs move to <stream>.prev."""
        header, _done, complete = self._read_progress()
        try:
            if header is not None and not complete and os.path.getsize(self.path) > 0:
                os.replace(self.path, self.path + ".prev")
                print("note: kept the unfinished earlier results as %s.prev" % self.path, file=sys.stderr)
        except OSError:
            pass

    def iter_records(self) -> Iterable[dict[str, Any]]:
        """Read the jobs back from disk, one line at a time (a half-written line is skipped)."""
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(rec, dict) and rec.get("url"):
                        yield rec
        except OSError:
            return

    def seed(self, col: Collector) -> None:
        """Teach the collector what is already on disk so a rerun never writes a job twice."""
        for rec in self.iter_records():
            col.seen_url.add(rec["url"])
            col.seen_key.setdefault(job_key(rec), rec["url"])

    def write(self, rec: dict[str, Any]) -> None:
        """Append one job and flush. The caller holds the collector lock."""
        self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self._fh.flush()
        self.count += 1

    def mark_done(self, task_id: str) -> None:
        with self.lock:
            self.done.add(task_id)
            self._pfh.write(task_id + "\n")
            self._pfh.flush()

    def mark_complete(self) -> None:
        with self.lock:
            self._pfh.write("#complete\n")
            self._pfh.flush()
            self.complete = True

    def admit(self) -> bool:
        """May another source start? Sets stop_reason once a budget runs out."""
        with self.lock:
            if self.stop_reason:
                return False
            if self.deadline is not None and _now() >= self.deadline:
                self.stop_reason = "time budget reached"
            elif self.max_sources and self.started >= self.max_sources:
                self.stop_reason = "source budget reached (%d)" % self.max_sources
            else:
                self.started += 1
                return True
            return False

    def stop(self, reason: str) -> None:
        with self.lock:
            self.stop_reason = self.stop_reason or reason

    def elapsed(self) -> float:
        return _now() - self.t0

    def close(self) -> None:
        for fh in (self._fh, self._pfh):
            try:
                if fh:
                    fh.close()
            except OSError:
                pass


# ================================================= run lock + run log (ws10b)
def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    except OSError:
        return False
    return True


OWNERLESS_GRACE_S = 1.0
RECOVER_GUARD_STALE_S = 30.0


class RunLock:
    """One run per stream at a time: ``<stream>.lock`` is a directory (mkdir is atomic).

    Mirrors career-ops pipeline-lock.mjs: the holder records owner.json (pid, token, started_at); a lock
    is stale when its owner pid is dead, or, with no readable owner, once the directory is older than a
    short grace period. A live holder is never taken over. Unlike pipeline-lock.mjs (which waits a few
    seconds for a short append) a hunt lasts minutes, so a second run fails fast instead of waiting.
    """

    def __init__(self, stream_path: str) -> None:
        self.dir = stream_path + ".lock"
        self.token = uuid.uuid4().hex
        self.held = False

    def _owner(self) -> dict[str, Any] | None:
        try:
            with open(os.path.join(self.dir, "owner.json"), encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def _stale(self) -> bool:
        owner = self._owner()
        if owner is not None and isinstance(owner.get("pid"), int):
            return not _pid_alive(owner["pid"])
        try:
            return time.time() - os.path.getmtime(self.dir) > OWNERLESS_GRACE_S
        except OSError:
            return True  # vanished while we looked: free

    def _reclaim(self) -> bool:
        """Remove a stale lock, serialized behind ``<lock>.recover`` (pipeline-lock.mjs:500-576).

        Without the guard, two callers that both judge the same lock stale can have the second one's
        delete remove the first one's freshly created lock, and both would then believe they hold it.
        Only the caller that wins the guard may decide-then-delete, and it deletes only the very
        directory it judged (same inode), never a newer lock that replaced it.
        """
        guard = self.dir + ".recover"
        try:
            os.mkdir(guard)
        except FileExistsError:
            # A guard normally lives for milliseconds; one left by a killed process ages out (:518-527).
            try:
                if time.time() - os.path.getmtime(guard) > RECOVER_GUARD_STALE_S:
                    shutil.rmtree(guard, ignore_errors=True)
            except OSError:
                pass
            return False
        except OSError:
            return False
        try:
            try:
                judged = os.stat(self.dir)
            except OSError:
                return False  # vanished: do not delete on that answer (:532-546)
            if not self._stale():
                return False
            try:
                now = os.stat(self.dir)
            except OSError:
                return False
            if (judged.st_ino, judged.st_dev) != (now.st_ino, now.st_dev):
                return False  # replaced under us: leave it alone (:560-565)
            shutil.rmtree(self.dir, ignore_errors=True)
            return True
        finally:
            shutil.rmtree(guard, ignore_errors=True)

    def acquire(self) -> str:
        """Take the lock. Returns '' on success, otherwise a message saying who holds it."""
        os.makedirs(os.path.dirname(os.path.abspath(self.dir)), exist_ok=True)
        for _attempt in range(6):
            try:
                os.mkdir(self.dir)
            except FileExistsError:
                if self._stale():
                    if self._reclaim():
                        continue
                    time.sleep(0.02)  # someone else is reclaiming: back off, then compete normally
                    continue
                owner = self._owner() or {}
                return "another run is writing %s (pid %s, started %s)" % (
                    self.dir[:-len(".lock")], owner.get("pid", "?"), owner.get("started_at", "?"))
            except OSError as e:
                return "could not take the run lock %s: %s" % (self.dir, e)
            try:
                with open(os.path.join(self.dir, "owner.json"), "w", encoding="utf-8") as f:
                    json.dump({"pid": os.getpid(), "token": self.token,
                               "started_at": _dt.datetime.now(_dt.timezone.utc).isoformat()}, f)
            except OSError:
                pass  # the directory itself is the lock; an ownerless one only ages out after the grace period
            self.held = True
            return ""
        return "could not take the run lock %s (lost a race)" % self.dir

    def release(self) -> None:
        """Remove the lock, but only if it is still ours (token check, as in pipeline-lock.mjs)."""
        if not self.held:
            return
        self.held = False
        owner = self._owner()
        if owner is None or owner.get("token") == self.token:
            shutil.rmtree(self.dir, ignore_errors=True)


def _atomic_write_text(path: str, text: str) -> None:
    """Write temp file, fsync, then os.replace (career-ops atomicWriteFile): never a half-written file."""
    tmp = "%s.tmp-%d-%s" % (path, os.getpid(), uuid.uuid4().hex)
    try:
        with open(tmp, "x", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass  # directory fsync is not supported everywhere (Windows)


MAX_FAILED_LISTED = 50


class RunLog:
    """One JSON line per run in ``hunt_runs.jsonl``: start, end, status and the counters.

    Mirrors career-ops scan-runs.tsv. The row is written for a finished run (``completed`` or ``partial``)
    and, from a snapshot, for a run that dies (``failed``) or is stopped (``interrupted``). Like scan.mjs
    registerRunFailureSnapshot the row is written once (a second signal cannot write a second row), and
    the failure path is best effort: it swallows its own errors and is not locked.
    """

    def __init__(self, path: str | None) -> None:
        self.path = None if not path or path.lower() == "none" else path
        self.started_at = _dt.datetime.now(_dt.timezone.utc)
        self.t0 = time.monotonic()
        self.col: Collector | None = None
        self.sink: Stream | None = None
        self.resumed = False
        self._armed = self.path is not None
        self._guard = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        col, sink = self.col, self.sink
        stats = col.stats if col is not None else {}

        def total(key: str) -> int:
            return sum(st[key] for st in list(stats.values()))

        failed = list(col.failed) if col is not None else []
        return {
            "start": self.started_at.isoformat(),
            "end": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "duration_s": round(time.monotonic() - self.t0, 1),
            "resumed": self.resumed,
            "stop_reason": sink.stop_reason if sink is not None else "",
            "sources_done": len(sink.done) if sink is not None else 0,
            "found": total("jobs_seen"),
            "kept": sink.count if sink is not None else 0,
            "skipped_seen": col.skipped_seen if col is not None else 0,
            "skipped_company": total("skipped_company"),
            "skipped_bad_url": total("skipped_bad_url"),
            "skipped_dead": total("live_dead"),
            "failed": len(failed),
            "failed_sources": failed[:MAX_FAILED_LISTED],
        }

    def write(self, status: str, locked: bool = False, **extra: Any) -> bool:
        """Append the row once. Returns False when disabled, already written, or the write failed."""
        path = self.path
        with self._guard:
            if not self._armed or path is None:
                return False
            self._armed = False
        lock = RunLock(path) if locked else None
        try:
            if lock is not None:
                for _ in range(40):  # the row is one short append: a brief wait for another writer is fine
                    if not lock.acquire():
                        break
                    time.sleep(0.05)
            row = {**self.snapshot(), "status": status, **extra}
            line = json.dumps(row, ensure_ascii=False) + "\n"
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
            return True
        except Exception:
            return False
        finally:
            if lock is not None:
                lock.release()


def resume_hint(argv: list[str], stream: Stream) -> str:
    """The exact command that continues a stopped run."""
    parts = [a for a in argv if a != "--resume"]
    cmd = "python hunt_kit.py " + " ".join(shlex.quote(a) for a in parts) + " --resume"
    n = len(stream.done)
    return ("%d source%s done so far, %d job%s saved in %s\nTo continue where it stopped, rerun with --resume:\n  %s"
            % (n, "" if n == 1 else "s", stream.count, "" if stream.count == 1 else "s", stream.path, cmd))


# ================================================================ collector
class Collector:
    """Thread-safe stats + results, applying the shared filter pipeline."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.lock = threading.Lock()
        self.stats: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
        self.results: list[dict[str, Any]] = []
        self.sink: Stream | None = getattr(args, "sink", None)
        self.tl = threading.local()  # per worker thread: jobs kept by the source it is running
        self.seen_url: set[str] = set()
        self.seen_key: dict[str, str] = {}
        self.failed: list[dict[str, str]] = []  # sources that raised: {source, error}
        self.skipped_seen = 0  # jobs dropped at output because history already has them
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

    def fail(self, source: str, err: BaseException) -> None:
        """Record a source that raised, with its error, for the run output and the run log."""
        with self.lock:
            self.failed.append({"source": source, "error": ("%s: %s" % (type(err).__name__, err))[:300]})

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
            self.bump(source, "skipped_bad_url")
            return  # javascript:, data:, file: and relative links are never kept
        if not self.matcher(title):
            return
        self.bump(source, "title_matches")
        if self.skipped(raw.get("company", ""), raw.get("board", "")):
            self.bump(source, "skipped_company")
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
            if self.sink is not None:
                self.sink.write(rec)  # on disk now; only small URL/key sets stay in memory
            else:
                self.results.append(rec)
            self.tl.n = getattr(self.tl, "n", 0) + 1
            self.stats[source]["kept"] += 1


# ================================================================= sources
def _offer_all(col: Collector, source: str, raws: Iterable[dict[str, Any]]) -> None:
    for r in raws:
        col.offer(source, r)


PARSE_CHUNK = 50


def _parse_chunks(
    parse: Callable[[Any, dict[str, Any]], list[dict[str, Any]]], d: Any, b: dict[str, Any]
) -> Iterable[list[dict[str, Any]]]:
    """Parse a board payload PARSE_CHUNK jobs at a time.

    The parse_* functions turn every job's full HTML description into text up front. On a big board
    that is thousands of descriptions alive at once, times up to 8 boards in parallel, for jobs the
    title filter drops a moment later. Chunking keeps only a few dozen alive at any time.
    """
    if isinstance(d, list):
        for i in range(0, len(d), PARSE_CHUNK):
            yield parse(d[i:i + PARSE_CHUNK], b)
        return
    items = d.get("jobs", [])
    if not isinstance(items, list):
        yield parse(d, b)
        return
    for i in range(0, len(items), PARSE_CHUNK):
        yield parse({**d, "jobs": items[i:i + PARSE_CHUNK]}, b)


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
    for raws in _parse_chunks(parse, d, b):
        _offer_all(col, source, raws)


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
            for raws in _parse_chunks(parse_lever, d, b):
                _offer_all(col, "lever", raws)
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


def _freehire_offer(col: Collector, r: dict[str, Any], seen_rows: set[str]) -> None:
    """Send one freehire row through the filters (copies of big-site ads are never read)."""
    key = r.get("public_slug") or r.get("url")
    if key:
        if key in seen_rows:
            return
        seen_rows.add(key)
    url = r.get("url") or ""
    if r.get("source") == "telegram" or re.search(r"linkedin\.com|indeed\.|glassdoor", url, re.I):
        return
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


FREEHIRE_PAGE = 100  # the agent search's largest page (checked live 2026-10-17)
FREEHIRE_MAX_OFFSET = 10_000  # offset + limit may not pass this ("pagination too deep")


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
    seen_rows: set[str] = set()
    # freehire pages are at most 100 rows and offset+limit may not pass 10,000.
    pages = max(1, min(int(getattr(col.args, "freehire_pages", 10) or 10), FREEHIRE_MAX_OFFSET // FREEHIRE_PAGE))
    for q in queries:
        for f in filters:
            for page in range(pages):
                col.bump("freehire", "boards_tried")
                p = dict(
                    q=q,
                    limit=FREEHIRE_PAGE,
                    offset=page * FREEHIRE_PAGE,
                    description_format="text",
                    posted_within_days=col.args.days,
                )
                p.update(f)
                st, d = http_get_json("https://freehire.me/api/v1/agent/jobs/search", p)
                if st != 200 or not isinstance(d, dict):
                    break
                col.bump("freehire", "boards_ok")
                rows = d.get("data") or []
                n_rows = len(rows)
                for r in rows:  # one row at a time, straight to the filters: never hold every query's ads
                    _freehire_offer(col, r, seen_rows)
                del rows, d
                if n_rows < FREEHIRE_PAGE:
                    break


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


def rotate_workday(
    boards: list[dict[str, Any]], limit: int | None, today: _dt.date | None = None
) -> list[dict[str, Any]]:
    """Keep every non-universe board; keep only a daily rotating slice of the open Workday list.

    The open list has ~13k Workday sites, too many for one run. Each day takes the next ``limit``
    of them (wrapping round), so a daily run covers the whole list over time. 0 or None = all.
    """
    pool = [b for b in boards if b["ats"] == "workday" and b.get("source") == "universe"]
    if not limit or limit <= 0 or len(pool) <= limit:
        return boards
    start = ((today or _dt.date.today()).toordinal() * limit) % len(pool)
    keep = {id(b) for b in (pool + pool)[start:start + limit]}
    return [b for b in boards if not (b["ats"] == "workday" and b.get("source") == "universe") or id(b) in keep]


def select_sources(value: str) -> list[str]:
    """'all' = everything; 'default' = all except the giant BambooHR list; else a comma list."""
    v = (value or "default").strip().lower()
    if v == "all":
        return list(ALL_SOURCES)
    if v == "default":
        return [s for s in ALL_SOURCES if s not in DEFAULT_SKIP]
    return [SOURCE_ALIASES.get(s, s) for s in split_csv(v) if SOURCE_ALIASES.get(s, s) in ALL_SOURCES]


def _merge_stats(col: Collector, label: str, st: dict[str, Any]) -> None:
    col.bump(label, "boards_tried", int(st.get("tried", 0)))
    col.bump(label, "boards_ok", int(st.get("ok", 0)))


def _module_task(
    col: Collector, label: str, call: Callable[[dict[str, int]], list[dict[str, Any]]], tid: str = ""
) -> None:
    """Run one module call, merge its tried/ok stats, and offer every Job it returns."""
    st: dict[str, int] = {"tried": 0, "ok": 0, "jobs": 0}
    try:
        jobs = call(st) or []
    except Exception as e:  # modules must not raise; if one does, report and move on
        print("warning: %s failed: %s: %s" % (label, type(e).__name__, e), file=sys.stderr)
        col.fail(tid or label, e)
        jobs = []
    _merge_stats(col, label, st)
    for j in jobs:
        col.offer_job(label, j)


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
    run_log = getattr(args, "run_log_obj", None)
    if run_log is not None:
        run_log.col = col  # lets the failure-path snapshot see the live counters
    sources = select_sources(args.sources)
    queries = split_csv(args.titles or DEFAULT_TITLES)[:12]
    countries = sorted(col.prefs.only or col.prefs.work)
    sink = col.sink
    if sink is not None:
        sink.seed(col)
    tasks: list[tuple[str, str, Callable[[], None]]] = []  # (source label, task id, work)

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
    boards = rotate_workday(boards, getattr(args, "workday_max", WORKDAY_MAX))
    boards = [dict(b, search_texts=queries[:WORKDAY_SEARCH_TITLES]) if b["ats"] == "workday" else b
              for b in boards]
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
            tasks.append((b["ats"], "%s:%s" % (b["ats"], b["token"].lower()),
                          functools.partial(_BOARD_FN[b["ats"]], col, b)))
        elif b["ats"] in ats_fetchers:
            call = functools.partial(_ats_call, ats_fetchers[b["ats"]], b)
            tasks.append((b["ats"], "%s:%s" % (b["ats"], b["token"].lower()),
                          functools.partial(_module_task, col, b["ats"], call,
                                          "%s:%s" % (b["ats"], b["token"].lower()))))
    if "freehire" in sources:
        tasks.append(("freehire", "freehire", lambda: _freehire(col, queries)))
    wanted_feeds = [s for s in sources if s in FEED_SOURCES and s != "freehire"]
    if wanted_feeds:
        kit_feeds = _import_kit("kit_feeds")
        feeds = getattr(kit_feeds, "FEEDS", {}) if kit_feeds else {}
        for name in wanted_feeds:
            if name in feeds:
                tasks.append((name, "feed:" + name, functools.partial(
                    _module_task, col, name,
                    functools.partial(_feed_call, feeds[name], queries, args.days, countries), "feed:" + name,
                )))
            elif kit_feeds is not None:
                print("note: feed %s not provided by kit_feeds; skipped" % name, file=sys.stderr)

    if sink is not None and sink.done:
        before = len(tasks)
        tasks = [t for t in tasks if t[1] not in sink.done]
        print("resuming: %d of %d sources already done, %d jobs already on disk"
              % (before - len(tasks), before, sink.count), file=sys.stderr, flush=True)

    def guarded(src: str, tid: str, work: Callable[[], None]) -> None:
        """One source from start to finish: budget check, run, stream progress line, mark done."""
        if sink is not None and not sink.admit():
            return  # budget spent: leave it for --resume
        col.tl.n = 0
        t0 = _now()
        ok = True
        try:
            work()
        except Exception as e:  # one bad board must not stop the sweep (and is retried on --resume)
            ok = False
            print("warning: %s failed: %s: %s" % (tid, type(e).__name__, e), file=sys.stderr)
            col.fail(tid, e)
        except BaseException:  # Ctrl-C or a hard crash: no further source may start
            if sink is not None:
                sink.stop("interrupted")
            raise
        if sink is not None:
            if ok:
                sink.mark_done(tid)
            print("[%s] %d job%s, %.1fs (total %.0fs)" % (tid, col.tl.n, "" if col.tl.n == 1 else "s",
                  _now() - t0, sink.elapsed()), file=sys.stderr, flush=True)

    progress = Progress(col, [src for src, _, _ in tasks])
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(MAX_WORKERS, args.workers)))
    try:
        futs = {ex.submit(guarded, src, tid, work): src for src, tid, work in tasks}
        for f in concurrent.futures.as_completed(futs):
            try:
                f.result()
            except Exception as e:
                print("warning: task failed: %s: %s" % (type(e).__name__, e), file=sys.stderr)
            progress.done(futs[f])
    except BaseException:  # Ctrl-C or a crash: queued sources must not start while we unwind
        if sink is not None:
            sink.stop("interrupted")
        raise
    finally:
        ex.shutdown(wait=True, cancel_futures=True)
    return col


class Progress:
    """One short stderr line per source: at the start, then every ~25% of its boards."""

    def __init__(self, col: Collector, sources: Iterable[str]) -> None:
        self.col = col
        self.lock = threading.Lock()
        self.total = collections.Counter(sources)
        self.finished: collections.Counter[str] = collections.Counter()
        self.step: dict[str, int] = {}
        for src in sorted(self.total):
            print("[%s] starting: %d board%s" % (src, self.total[src], "" if self.total[src] == 1 else "s"),
                  file=sys.stderr, flush=True)

    def done(self, src: str) -> None:
        with self.lock:
            self.finished[src] += 1
            n, tot = self.finished[src], self.total[src]
            quarter = n * 4 // tot
            if tot < 4 and n < tot:
                return
            if quarter <= self.step.get(src, 0) and n < tot:
                return
            self.step[src] = quarter
            kept = self.col.stats[src]["kept"]
            print("[%s] %d/%d boards (%d%%), %d kept so far" % (src, n, tot, n * 100 // tot, kept),
                  file=sys.stderr, flush=True)


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


HISTORY_RUNS_KEPT = 100


def load_history_runs(path: str | None) -> list[dict[str, Any]]:
    """The per-run skipped counters saved in the history file (missing or old-format file = empty)."""
    if not path or path.lower() == "none":
        return []
    try:
        with open(path, encoding="utf-8") as f:
            runs = json.load(f).get("runs", [])
        return [r for r in runs if isinstance(r, dict)]
    except (OSError, ValueError, AttributeError):
        return []


def save_history(
    path: str | None, urls: set[str], keys: set[str], results: list[dict[str, Any]],
    run: dict[str, Any] | None = None,
) -> None:
    """Add this run's output (and its skipped_* counters) to the history file, atomically.

    Written to a temp file and swapped in with os.replace, so a kill mid-write leaves the old file whole.
    """
    if not path or path.lower() == "none":
        return
    u = sorted(urls | {r["url"] for r in results})
    k = sorted(keys | {job_key(r) for r in results})
    runs = load_history_runs(path)
    if run:
        runs = (runs + [run])[-HISTORY_RUNS_KEPT:]
    _atomic_write_text(path, json.dumps({"urls": u, "keys": k, "runs": runs}))


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
        "--workday-max",
        type=int,
        default=WORKDAY_MAX,
        help="open-list Workday boards per run, a slice that moves daily (default 300; 0 = all)",
    )
    p.add_argument(
        "--freehire-pages",
        type=int,
        default=10,
        help="freehire pages of 100 per title and place (default 10; at most 100)",
    )
    p.add_argument(
        "--sources",
        default="default",
        help="'default', 'all' (adds bamboohr), or a comma list of: " + ",".join(ALL_SOURCES),
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
    p.add_argument("--out", default="-", help="JSONL output path (default stdout)")
    p.add_argument(
        "--stream",
        default=None,
        help="file jobs are appended to as they are found (default: OUT.stream, or hunt_stream.jsonl)",
    )
    p.add_argument("--resume", action="store_true", help="continue a stopped run: skip sources already done")
    p.add_argument(
        "--max-minutes", type=float, default=15.0,
        help="stop starting new sources after N minutes (default 15, 0 = no limit); rerun with --resume",
    )
    p.add_argument("--max-sources", type=int, default=0, help="stop after N sources (0 = no limit)")
    p.add_argument(
        "--run-log", default=None,
        help="JSONL file one summary row is appended to for every run, failed ones too "
             "(default: hunt_runs.jsonl next to the stream; 'none' = off)",
    )
    p.add_argument("--stats", action="store_true", help="print coverage counts per source to stderr")
    return p


def format_failed(col: Collector) -> str:
    """The sources that raised, one per line: name and error (empty string when none failed)."""
    if not col.failed:
        return ""
    lines = ["Failed sources (%d):" % len(col.failed)]
    lines += ["  %s: %s" % (f["source"], f["error"]) for f in sorted(col.failed, key=lambda f: f["source"])]
    return "\n".join(lines)


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
    if args.discover:
        for hit in discover(split_csv(args.discover)):
            print(json.dumps(hit, ensure_ascii=False))
        return 0
    stream_path = args.stream or ("hunt_stream.jsonl" if args.out == "-" else args.out + ".stream")
    lock = RunLock(stream_path)
    busy = lock.acquire()
    if busy:
        print("%s. Wait for it to finish, or use another --stream." % busy, file=sys.stderr)
        return 3
    try:
        return _main_locked(args, stream_path, list(sys.argv[1:] if argv is None else argv))
    finally:
        lock.release()


def _main_locked(args: argparse.Namespace, stream_path: str, cli: list[str]) -> int:
    run_log = RunLog(args.run_log or os.path.join(os.path.dirname(os.path.abspath(stream_path)), "hunt_runs.jsonl"))
    sink = Stream(stream_path, run_fingerprint(args), args.max_minutes, args.max_sources)
    run_log.sink = sink
    try:
        state = sink.open(args.resume)
        if state == "complete":  # nothing ran: not a run, so no run-log row
            print("That run already finished: %d jobs are in %s. Rerun without --resume to search again."
                  % (sink.count, stream_path), file=sys.stderr)
            return 0
        run_log.resumed = state == "resumed"
        args.sink = sink
        args.run_log_obj = run_log
        print("Results stream to %s as they come; if the run stops, rerun with --resume to continue."
              % stream_path, file=sys.stderr, flush=True)
        try:
            col = run(args)
            partial = bool(sink.stop_reason)
            stored = list(sink.iter_records()) if sink.count else col.results
            results = sort_newest(stored)
            del stored
            urls, keys = load_history(args.history)
            skipped = 0
            try:
                results, skipped = filter_new(results, set() if args.all else urls, set() if args.all else keys)
                col.skipped_seen = skipped
                if args.check_live and not partial:
                    results = check_live(col, results)
            except Exception as e:  # a late step must not lose the results
                print("warning: late step failed: %s: %s" % (type(e).__name__, e), file=sys.stderr)
            col.results = results
            if args.out == "-":
                for r in results:
                    sys.stdout.write(json.dumps(r, ensure_ascii=False) + "\n")
                sys.stdout.flush()
            else:
                with open(args.out, "w", encoding="utf-8") as f:
                    for r in results:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            counts = {"kept": len(results), "stored": sink.count}
            if not partial:  # a partial run must not mark its jobs "seen": the resumed run still outputs them
                # Only now, after the output was written in full: a run that died before this line
                # leaves the history untouched, so the next run still outputs these jobs.
                try:
                    snap = run_log.snapshot()
                    row = {k: snap[k] for k in ("end", "found", "skipped_seen", "skipped_company",
                                                "skipped_bad_url", "skipped_dead", "failed")}
                    row["kept"] = len(results)
                    save_history(args.history, urls, keys, results, run=row)
                except OSError as e:
                    print("warning: could not write history: %s" % e, file=sys.stderr)
            if args.stats or skipped:
                print(format_stats(col, skipped), file=sys.stderr)
            if col.failed:
                print(format_failed(col), file=sys.stderr)
            if partial:
                print("\nStopped early: %s. The %d job%s found so far %s written to %s.\n%s"
                      % (sink.stop_reason, len(results), "" if len(results) == 1 else "s",
                         "was" if len(results) == 1 else "were", "stdout" if args.out == "-" else args.out,
                         resume_hint(cli, sink)), file=sys.stderr)
                if args.check_live:
                    print("(--check-live was skipped on this partial run.)", file=sys.stderr)
            else:
                sink.mark_complete()
            run_log.write("partial" if partial else "completed", locked=True, **counts)
            return 0
        except KeyboardInterrupt:  # scan.mjs: process.once('SIGINT', ...) writes the failure row, exits 130
            print("\nInterrupted.\n" + resume_hint(cli, sink), file=sys.stderr)
            run_log.write("interrupted")
            return 130
        except BaseException as e:  # scan.mjs: main().catch(... writeRunFailureRow('failed'))
            run_log.write("failed", error="%s: %s" % (type(e).__name__, e))
            raise
    finally:
        sink.close()


if __name__ == "__main__":
    sys.exit(main())
