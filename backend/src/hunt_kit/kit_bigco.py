"""Amazon, Google and Apple career sites (Meta, IBM and Uber follow) for the Job360 hunt kit (big-company module).

Same contract as ``kit_regional.FEEDS`` / ``kit_bigboards.FEEDS``: ``fetch_x(queries, http, stats, *, days,
countries) -> list[Job]``, never raises, networking only through the ``http`` callable the core passes in. A
fetcher runs ONLY when one of the user's ``countries`` has a place name (``kit_bigboards.PLACE_NAMES``) or is "*"
(then it searches with no location and the CORE keeps only ads that say they sponsor a visa, as for kit_gov),
and returns ``[]`` with no request otherwise. Nothing here ranks or scores.

HARD LIMITS per site per run: at most ``MAX_SEARCHES`` (3) searches, each one (title, country); at most ``PAGES``
(2) result pages per search; at most ``DETAIL_CAP`` (10) detail fetches; ``PAUSE_S`` seconds between two requests
to the same site. The whole site stops at the first answer that is not HTTP 200 and at the first login, CAPTCHA
or "security check" page: nothing is retried, solved or worked around (the core's http is called with
``once=True``). No login, no cookies, no proxy, no key. Standard library only.

Every ad names its own employer (Amazon: ``company_name``; Google: the card's/record's hiring brand; Meta and
Uber: the page's ``hiringOrganization``; IBM and Apple: the company itself) and links back to the original
posting; the site name is never put in ``company`` unless it IS the employer.

The references are MIT-licensed freehire and career-ops (see NOTICE.md), cited above each fetcher; the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on
fixtures shaped like the references' test data only. These are the sites' own front-end endpoints: they can change
or refuse a script without notice, in which case the feed simply returns nothing. Meta and Uber sit behind a
Cloudflare/edge check that freehire passes only with a Chrome TLS fingerprint (metacareers.go:12-14,
uber.go:8-11); ``urllib`` cannot imitate that, so they may answer 403 from some networks and the site then stops.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

try:
    import kit_bigboards as _kb  # type: ignore[import-not-found]  # sibling file, not a package module
    import kit_feeds as _kf  # type: ignore[import-not-found]
    import kit_regional as _kr  # type: ignore[import-not-found]
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_bigboards as _kb
    import kit_feeds as _kf
    import kit_regional as _kr

Job = dict[str, Any]
Stats = dict[str, Any]
HttpFn = Callable[..., "tuple[int, Any]"]

# Sibling-module helpers, typed here because mypy cannot see the sibling files.
_feed: Callable[[Callable[..., list[Job]]], Callable[..., list[Job]]] = _kf._feed
_keep: Callable[..., list[Job]] = _kf._keep
_queries: Callable[[list[str]], list[str]] = _kf._queries
_date: Callable[[Any], str] = _kf._date
strip_html: Callable[[Any], str] = _kf.strip_html
_made: Callable[..., Job] = _kr._made  # one Job: title_only when no description was read
_unique: Callable[[list[Job]], list[Job]] = _kr._unique
_ld_posting: Callable[[str], dict[str, Any]] = _kr._ld_posting
_posting_place: Callable[[dict[str, Any]], tuple[str, str]] = _kr._posting_place
PLACE_NAMES: dict[str, str] = _kb.PLACE_NAMES
_blocked: Callable[[Any], bool] = _kb._blocked

MAX_SEARCHES = 3  # searches (one title in one country) per site per run
PAGES = 2  # result pages per search
DETAIL_CAP = 10  # ads whose detail page is fetched, per site per run
PAUSE_S = 1.0  # seconds between two requests to one site (tests set it to 0)

_WS = re.compile(r"\s+")
JSON_HEADERS = {"accept": "application/json", "content-type": "application/json"}  # career-ops ibm.mjs:108-110
HTML_HEADERS = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}  # NOT IN REFERENCE

# ISO-2 -> ISO-3, only the codes career-ops providers/_country.mjs:31-62 (ALPHA3_TO_ALPHA2) lists, inverted.
ISO3 = {
    "AE": "ARE", "AR": "ARG", "AT": "AUT", "AU": "AUS", "BE": "BEL", "BG": "BGR", "BH": "BHR", "BR": "BRA",
    "CA": "CAN", "CH": "CHE", "CL": "CHL", "CN": "CHN", "CO": "COL", "CZ": "CZE", "DE": "DEU", "DK": "DNK",
    "EG": "EGY", "ES": "ESP", "FI": "FIN", "FR": "FRA", "GB": "GBR", "GR": "GRC", "HK": "HKG", "HU": "HUN",
    "ID": "IDN", "IE": "IRL", "IL": "ISR", "IN": "IND", "IT": "ITA", "JP": "JPN", "KR": "KOR", "KW": "KWT",
    "MX": "MEX", "MY": "MYS", "NG": "NGA", "NL": "NLD", "NO": "NOR", "NZ": "NZL", "OM": "OMN", "PH": "PHL",
    "PK": "PAK", "PL": "POL", "PT": "PRT", "QA": "QAT", "RO": "ROU", "SA": "SAU", "SE": "SWE", "SG": "SGP",
    "TH": "THA", "TR": "TUR", "UA": "UKR", "US": "USA", "VN": "VNM", "ZA": "ZAF",
}
_FROM_ISO3 = {v: k for k, v in ISO3.items()}

# Country text recognition for the sites that offer no country filter (Apple, Meta, Uber, Google records, IBM).
# NOT IN REFERENCE: the kit's own rule. A place that names a known country is kept only when that country is one
# of the user's; a place that names none ("San Diego") is kept and marked extra.country_unchecked.
_ALIASES = {"usa": "US", "u.s.": "US", "united states of america": "US", "uk": "GB", "u.k.": "GB",
            "great britain": "GB", "england": "GB", "scotland": "GB", "wales": "GB", "uae": "AE",
            "czech republic": "CZ", "korea, republic of": "KR", "republic of korea": "KR", "holland": "NL"}
_NAME_RX = re.compile(
    r"(?<![A-Za-z])(" + "|".join(re.escape(n) for n in sorted(
        [v.lower() for v in PLACE_NAMES.values()] + list(_ALIASES), key=len, reverse=True)) + r")(?![A-Za-z])",
    re.I)
_US_STATE_RX = re.compile(
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|DC|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|"
    r"ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)(?:\s*$|\s*[,;·(])")
_BY_NAME = {v.lower(): k for k, v in PLACE_NAMES.items()}


def _wanted(countries: list[str]) -> set[str] | None:
    """The user's ISO codes, or None when "*" is among them (no country filtering)."""
    if any(str(c) == "*" for c in countries):
        return None
    return {str(c).upper() for c in countries}


def _codes_in(*texts: Any, extra: Any = (), states: bool = True) -> set[str]:
    """Every known country the place text names (by name, alias, or a US state suffix), plus ``extra`` codes."""
    found: set[str] = {str(c).upper() for c in extra if str(c).upper() in PLACE_NAMES}
    for t in texts:
        s = str(t or "")
        low = s.lower().replace("northern ireland", "united kingdom")
        for m in _NAME_RX.finditer(low):
            name = m.group(1)
            code = _ALIASES.get(name) or _BY_NAME.get(name)
            if code:
                found.add(code)
        if states and _US_STATE_RX.search(s):
            found.add("US")
    return found


def _country_ok(job: Job, wanted: set[str] | None, *texts: Any, extra: Any = (), states: bool = True) -> bool:
    """False when the place names a country that is not one of the user's; marks an ad that names none.

    ``extra`` holds codes the SOURCE itself states (IBM's country facet or city alpha-2, Google's last location
    element, Apple's countryID). When it holds a known country, those codes alone decide and the place text is not
    read (so an Indian "Bangalore, IN" is never taken for the US state Indiana). ``states=False`` also stops the
    US-state suffix rule for places where ", XX" is a country code, not a state (IBM city facets)."""
    if wanted is None:
        return True
    stated = {str(c).upper() for c in extra if str(c).upper() in PLACE_NAMES}
    if stated:
        return bool(stated & wanted)
    codes = _codes_in(*texts, extra=extra, states=states)
    if not codes:
        job["extra"]["country_unchecked"] = True
        return True
    return bool(codes & wanted)


def _slots(countries: list[str]) -> list[str | None]:
    """The user's countries that have a place name, in order, then None (no location) when "*" is present."""
    out: list[str | None] = []
    for c in countries:
        code = str(c).upper()
        if code in PLACE_NAMES and code not in out:
            out.append(code)
    if any(str(c) == "*" for c in countries):
        out.append(None)
    return out


def _searches(queries: list[str], slots: list[str | None]) -> list[tuple[str, str | None]]:
    """At most MAX_SEARCHES (title, country) pairs, spread over titles AND countries (diagonals of the grid)."""
    qs = _queries(queries)
    grid = sorted(((i + j, i, q, s) for i, q in enumerate(qs) for j, s in enumerate(slots)),
                  key=lambda t: (t[0], t[1]))
    return [(q, s) for _, _, q, s in grid[:MAX_SEARCHES]]


class _Site:
    """One site's run: counted requests, a pause between them, and the stop rule (first non-200 ends the site)."""

    def __init__(self, name: str, http: HttpFn, stats: Stats) -> None:
        self.name, self.http, self.stats = name, http, stats
        self.stopped = False
        self.sent = 0

    def stop(self, why: str) -> None:
        if not self.stopped:
            print(f"warning: {self.name} stopped for this run: {why}", file=sys.stderr)
        self.stopped = True

    def get(self, url: str, *, headers: dict[str, str], as_json: bool = False, data: Any = None) -> Any:
        """The body on HTTP 200, else None and the whole site stops. Never retries."""
        if self.stopped:
            return None
        if self.sent and PAUSE_S > 0:
            time.sleep(PAUSE_S)
        self.sent += 1
        self.stats["tried"] = self.stats.get("tried", 0) + 1
        kw: dict[str, Any] = {"headers": headers, "as_json": as_json, "once": True}
        if data is not None:
            kw.update(method="POST", data=json.dumps(data).encode())
        status, body = self.http(url, **kw)
        if status == 200 and body is not None:
            self.stats["ok"] = self.stats.get("ok", 0) + 1
            return body
        self.stop(f"HTTP {status} (nothing read)")
        return None


def _remote_word(*parts: Any) -> bool | None:
    text = " ".join(str(p or "") for p in parts).lower()
    return True if any(k in text for k in ("remote", "work from home", "wfh")) else None


def _s(value: Any) -> str:
    return _WS.sub(" ", value).strip() if isinstance(value, str) else ""


def _title_match(job: Job, queries: list[str]) -> bool:
    """Plain title keyword match (NOT ranking), for the two sites that have no search: any query whose words all
    occur in the title. No queries at all keeps everything."""
    qs = [q.strip().lower() for q in queries if q and q.strip()]
    title = job["title"].lower()
    return not qs or any(all(w in title for w in q.split()) for q in qs)


# ------------------------------------------------------------------------------------------------ Amazon
# Mirrors freehire internal/ingest/sources/amazon.go:19 (GET https://www.amazon.jobs/en/search.json with
# result_limit, offset, sort=recent), :22 (100 a page), :20 (job URL = origin + job_path), :45-50 (fields
# id_icims, title, job_path, normalized_location, description = the full HTML ad inline, posted_date), :23-24
# ("June 15, 2026"), :61 (an empty jobs list ends the walk), :67 (stop at hits) and career-ops
# providers/amazon.mjs:42-45 (base_query and loc_query always sent; sort and result_limit), :31-38 and the
# comment :30-33 (a facet MUST use the bracket form normalized_country_code[]=DEU: a bare key is ignored),
# :50-55 (posted_date has a padded day: whitespace is collapsed first), :60-79 (location = normalized_location
# or location, then the other cities of the JSON-string array ``locations``, joined " · "), :127 (company =
# company_name), :133 (a short page is the last). The ad text arrives with the list, so there are no detail
# fetches. NOT IN REFERENCE: that base_query matches a job TITLE (amazon.mjs:12-13 calls it a "keyword filter");
# the alpha-3 codes other than DEU (the codes come from _country.mjs:31-62, which freehire/career-ops never send
# to Amazon); the "*" case (no normalized_country_code at all = the global board, as with career-ops' empty
# loc_query).
AMAZON_ORIGIN = "https://www.amazon.jobs"
AMAZON_PAGE = 100


def _amazon_url(q: str, iso: str | None, offset: int) -> str:
    params: list[tuple[str, Any]] = [("base_query", q), ("loc_query", "")]
    if iso:
        params.append(("normalized_country_code[]", ISO3[iso]))
    params += [("sort", "recent"), ("result_limit", AMAZON_PAGE), ("offset", offset)]
    return f"{AMAZON_ORIGIN}/en/search.json?{urlencode(params)}"


def _amazon_place(row: dict[str, Any]) -> str:
    cities: list[str] = []
    primary = _s(row.get("normalized_location")) or _s(row.get("location"))
    if primary:
        cities.append(primary)
    seen = set(cities)
    for entry in row.get("locations") or []:
        try:
            loc = json.loads(entry) if isinstance(entry, str) else entry
        except ValueError:
            continue
        if not isinstance(loc, dict):
            continue
        spelled = _s(loc.get("location"))
        name = _s(loc.get("normalizedLocation")) or spelled
        if name and name not in seen and spelled not in seen:
            cities.append(name)
        seen.update(x for x in (name, spelled) if x)
    return " · ".join(cities)


def _amazon_posted(text: Any) -> str:
    try:
        return datetime.strptime(_s(text), "%B %d, %Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_amazon(payload: Any) -> list[Job]:
    """One amazon.jobs search.json page -> Jobs (the ad text is in the row)."""
    out: list[Job] = []
    for r in (payload.get("jobs") if isinstance(payload, dict) else None) or []:
        if not isinstance(r, dict):
            continue
        path, title = _s(r.get("job_path")), _s(r.get("title"))
        if not (path and title):
            continue
        rel = path if path.startswith("/") else "/" + path
        url = path if re.match(r"https?://", path, re.I) else AMAZON_ORIGIN + rel
        place = _amazon_place(r)
        extra: dict[str, Any] = {}
        if _s(r.get("id_icims")):
            extra["id"] = _s(r.get("id_icims"))
        out.append(_made("amazon", "Amazon", title=title, company=_s(r.get("company_name")) or "Amazon", url=url,
                         place=place, country="", posted=_amazon_posted(r.get("posted_date")),
                         description=r.get("description"), remote=_remote_word(title, place), extra=extra))
    return out


@_feed
def fetch_amazon(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Amazon's careers search, by the user's titles, for the user's countries (global board for "*")."""
    searches = [(q, iso) for q, iso in _searches(queries, _slots(countries)) if iso is None or iso in ISO3]
    if not searches:
        return []
    site = _Site("amazon", http, stats)
    jobs: list[Job] = []
    for q, iso in searches:
        for page in range(PAGES):
            body = site.get(_amazon_url(q, iso, page * AMAZON_PAGE), headers=JSON_HEADERS, as_json=True)
            if body is None:
                break
            if not isinstance(body, dict) or not isinstance(body.get("jobs"), list):
                site.stop("unexpected answer (not a search result)")
                break
            rows = body["jobs"]
            jobs += parse_amazon(body)
            if len(rows) < AMAZON_PAGE:
                break
        if site.stopped:
            break
    return _keep(_unique(jobs), queries, days, search=False)  # amazon.jobs matched base_query itself


# ------------------------------------------------------------------------------------------------ Google
# Mirrors career-ops providers/google.mjs:8 (GET https://www.google.com/about/careers/applications/jobs/results/
# with q, location and sort_by=date), :43 (BASE), :20-35 (FIRST PAGE ONLY: google.com/robots.txt disallows every
# URL carrying ``page=``, so no page parameter is ever sent; career-ops declines to route around it and so does
# this kit), :48-53 (card regexes: the closing anchor jobs/results/<id>-<slug>, <h3> title, <p class="l103df">
# "Employer | place; place; +N more"), :13-18 (the EMPLOYER is not always Google: YouTube, DeepMind ...), :88-111
# (parse: title from h3, else the link's aria-label, else the slug; employer + locations split on the first "|"),
# :130-141 (id/slug/employer/locations -> Job, URL rebuilt as BASE + id-slug, company = employer, else the entry
# name). The same page also embeds the full ad as freehire internal/ingest/sources/google.go:31-37 (the
# AF_initDataCallback({key: 'ds:1', ... data:[...], sideChannel script, regex ``data:(\[.*?\]), sideChannel``),
# :85-98 (data = [records, _, total, pageSize]), :129-144 (record: [0] id, [1] title, [7] hiring brand, [9]
# locations, [10]+[3]+[4] = [null, html] description parts, [12] = [seconds, nanos]), :147-193 (location entries
# are ["City, Region, Country", ..., "CC"], first element = display name), :28 (public URL .../results/<id>); the
# kit reads that blob first and the career-ops cards when the blob is absent. NOT IN REFERENCE: that the q= page
# carries ds:1 (freehire only fetches the unfiltered ?page=N list); the "*" case (no location parameter at all;
# career-ops defaults to "United States", google.mjs:62); using the last element of a location entry as its ISO
# country code (freehire's comment :172-173 says "CC" ends the entry, its fixture shows "IN").
GOOGLE_BASE = "https://www.google.com/about/careers/applications/jobs/results/"
_G_ANCHOR = re.compile(r"""<a\b[^>]*?\bhref="(?:[^"]*/)?jobs/results/(\d+)-([a-z0-9-]+)[^"]*"[^>]*>""")
_G_H3 = re.compile(r"<h3\b[^>]*>([^<]+)</h3>")
_G_LINE = re.compile(r'<p class="l103df">([\s\S]*?)</p>')
_G_ARIA = re.compile(r'\baria-label="Learn more about ([^"]+)"')
_G_MORE = re.compile(r"^\+\d+ more$")
_G_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script\b[^>]*>", re.S | re.I)
_G_DATA = re.compile(r"data:(\[.*?\]), sideChannel", re.S)


def _g_title_from_slug(slug: str) -> str:
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), _s(slug.replace("-", " ")))


def _last(rx: re.Pattern[str], text: str) -> re.Match[str] | None:
    hits = list(rx.finditer(text))
    return hits[-1] if hits else None


def parse_google_cards(page: Any) -> list[Job]:
    """career-ops cards: one Job per jobs/results/<id>-<slug> anchor; no description."""
    html = page if isinstance(page, str) else ""
    out: list[Job] = []
    prev = 0
    for a in _G_ANCHOR.finditer(html):
        card, prev = html[prev:a.start()], a.end()
        h3 = _last(_G_H3, card)
        aria = _G_ARIA.search(a.group(0))
        title = (strip_html(h3.group(1)) if h3 else "") or (strip_html(aria.group(1)) if aria else "") \
            or _g_title_from_slug(a.group(2))
        line = _last(_G_LINE, card)
        employer, places = "", []
        if line and "|" in line.group(1):
            head, _, tail = line.group(1).partition("|")
            employer = _s(strip_html(head))
            places = [p.strip() for p in strip_html(tail).split(";") if p.strip() and not _G_MORE.match(p.strip())]
        out.append(_made("google", "Google Careers", title=title, company=employer or "Google",
                         url=f"{GOOGLE_BASE}{a.group(1)}-{a.group(2)}", place="; ".join(places), country="",
                         remote=_remote_word("; ".join(places)), extra={"id": a.group(1)}))
    return out


def _g_html_part(rec: list[Any], i: int) -> str:
    part = rec[i] if i < len(rec) else None
    return part[1] if isinstance(part, list) and len(part) > 1 and isinstance(part[1], str) else ""


def parse_google_records(page: Any) -> list[Job]:
    """freehire ds:1 records -> Jobs with the full ad text."""
    html = page if isinstance(page, str) else ""
    data: Any = None
    for m in _G_SCRIPT.finditer(html):
        if "key: 'ds:1'" in m.group(1):
            d = _G_DATA.search(m.group(1))
            try:
                data = json.loads(d.group(1)) if d else None
            except ValueError:
                data = None
            break
    records = data[0] if isinstance(data, list) and data and isinstance(data[0], list) else []
    out: list[Job] = []
    for rec in records:
        if not isinstance(rec, list) or not rec or not _s(str(rec[0] if rec[0] is not None else "")):
            continue
        gid = _s(str(rec[0]))
        title = _s(rec[1]) if len(rec) > 1 else ""
        if not title:
            continue
        names: list[str] = []
        codes: list[str] = []
        for loc in (rec[9] if len(rec) > 9 and isinstance(rec[9], list) else []):
            if isinstance(loc, list) and loc and isinstance(loc[0], str) and loc[0].strip():
                names.append(loc[0].strip())
                if isinstance(loc[-1], str) and len(loc[-1]) == 2:
                    codes.append(loc[-1].upper())
        stamp = rec[12] if len(rec) > 12 else None
        posted = _date(stamp[0]) if isinstance(stamp, list) and stamp and isinstance(stamp[0], (int, float)) else ""
        desc = _g_html_part(rec, 10) + _g_html_part(rec, 3) + _g_html_part(rec, 4)
        job = _made("google", "Google Careers", title=title,
                    company=_s(rec[7]) if len(rec) > 7 and isinstance(rec[7], str) and _s(rec[7]) else "Google",
                    url=GOOGLE_BASE + gid, place="; ".join(names), country="", posted=posted, description=desc,
                    remote=_remote_word("; ".join(names)), extra={"id": gid})
        job["extra"]["_codes"] = codes  # consumed (and removed) by fetch_google
        out.append(job)
    return out


@_feed
def fetch_google(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Google Careers' own results page, first page only (robots.txt), by the user's titles and countries."""
    searches = _searches(queries, _slots(countries))
    if not searches:
        return []
    wanted = _wanted(countries)
    site = _Site("google", http, stats)
    jobs: list[Job] = []
    for q, iso in searches:
        params = {"q": q}
        if iso:
            params["location"] = PLACE_NAMES[iso]
        params["sort_by"] = "date"
        body = site.get(f"{GOOGLE_BASE}?{urlencode(params)}", headers=HTML_HEADERS)
        if body is None:
            break
        got = parse_google_records(body) or parse_google_cards(body)
        if not got:
            if _blocked(body):
                site.stop("a consent or security page instead of results")
                break
            continue  # a plain empty result: this search only
        jobs += got
    kept: list[Job] = []
    for job in _unique(jobs):
        codes = job["extra"].pop("_codes", [])
        if _country_ok(job, wanted, job["location"], extra=codes):
            kept.append(job)
    return _keep(kept, queries, days, search=False)  # Google matched q itself


# ------------------------------------------------------------------------------------------------- Apple
# Mirrors freehire internal/ingest/sources/apple.go:35 (POST https://jobs.apple.com/api/v1/search), :110-121 (body
# query, filters = {} ALWAYS sent: omitting it is HTTP 436 :17-19; page, locale "en-us", sort "newest", format
# longDate/mediumDate), :67-74 and :95-101 (answer res.searchResults, res.totalRecords; an empty page ends the
# walk), :58-65 (posting fields positionId, postingTitle, transformedPostingTitle, locations[].name,
# postDateInGMT, homeOffice), :20-22 and :160-171 (one row per (position, location): dedupe by positionId),
# :36 (detail GET /api/v1/jobDetails/<positionId>?locale=en-us), :130-141 and :190-219 (res.jobSummary,
# description, minimumQualifications, preferredQualifications, joined as paragraphs + headed bullet lists),
# :37 (public URL https://jobs.apple.com/en-us/details/<positionId>/<slug>), :149 (company = Apple),
# :152 (homeOffice = remote). No CSRF token or cookie (:19). NOT IN REFERENCE: that ``query`` takes the
# job title (freehire always sends ""); any server-side country filter (``filters`` is {} in the reference, so the
# country is checked here from the location names and, when the row has one, a ``countryID`` such as
# "iso-country-GBR"); a place that names no country ("San Diego") is kept and marked country_unchecked.
APPLE_SEARCH = "https://jobs.apple.com/api/v1/search"
APPLE_DETAIL = "https://jobs.apple.com/api/v1/jobDetails/%s?locale=en-us"
APPLE_JOB = "https://jobs.apple.com/en-us/details/%s/%s"


def _apple_body(q: str, page: int) -> dict[str, Any]:
    return {"query": q, "filters": {}, "page": page, "locale": "en-us", "sort": "newest",
            "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}}


def parse_apple(payload: Any) -> tuple[list[dict[str, Any]], int]:
    """(search rows, totalRecords) of one /api/v1/search answer."""
    res = payload.get("res") if isinstance(payload, dict) else None
    rows = res.get("searchResults") if isinstance(res, dict) else None
    total = res.get("totalRecords") if isinstance(res, dict) else 0
    return [r for r in rows or [] if isinstance(r, dict)], total if isinstance(total, int) else 0


def _apple_codes(row: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for loc in row.get("locations") or []:
        cid = _s(loc.get("countryID")) if isinstance(loc, dict) else ""
        tail = cid.rsplit("-", 1)[-1].upper()
        if tail in _FROM_ISO3:
            out.append(_FROM_ISO3[tail])
        elif len(tail) == 2:
            out.append(tail)
    return out


def apple_description(res: Any) -> str:
    """jobSummary + description as paragraphs, the two qualification lists as headed bullets (apple.go:190-219)."""
    if not isinstance(res, dict):
        return ""
    parts = [_s_block(res.get("jobSummary")), _s_block(res.get("description"))]
    for head, key in (("Minimum Qualifications", "minimumQualifications"),
                      ("Preferred Qualifications", "preferredQualifications")):
        lines = [x.strip() for x in str(res.get(key) or "").split("\n") if x.strip()]
        if lines:
            parts.append(head + "\n" + "\n".join("- " + x for x in lines))
    return "\n\n".join(p for p in parts if p)


def _s_block(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@_feed
def fetch_apple(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Apple's careers search, by the user's titles; the user's countries are checked on the rows."""
    searches = _searches(queries, _slots(countries))
    if not searches:
        return []
    wanted = _wanted(countries)
    site = _Site("apple", http, stats)
    rows: list[dict[str, Any]] = []
    for q in dict.fromkeys(q for q, _ in searches):  # the search has no country: one search per title
        seen = 0
        for page in range(1, PAGES + 1):
            body = site.get(APPLE_SEARCH, headers=JSON_HEADERS, as_json=True, data=_apple_body(q, page))
            if body is None:
                break
            got, total = parse_apple(body)
            if not got:
                break
            rows += got
            seen += len(got)
            if total and seen >= total:
                break
        if site.stopped:
            break
    jobs: list[Job] = []
    ids: dict[str, str] = {}
    for r in rows:
        pid, title = _s(r.get("positionId")), _s(r.get("postingTitle"))
        if not (pid and title) or pid in ids:
            continue
        ids[pid] = pid
        locs = [x for x in r.get("locations") or [] if isinstance(x, dict)]
        names = [_s(loc.get("name")) for loc in locs if _s(loc.get("name"))]
        slug = _s(r.get("transformedPostingTitle"))
        job = _made("apple", "Apple Careers", title=title, company="Apple",
                    url=APPLE_JOB % (pid, slug) if slug else f"https://jobs.apple.com/en-us/details/{pid}",
                    place="; ".join(names), country="", posted=_date(r.get("postDateInGMT")),
                    remote=True if r.get("homeOffice") is True else None, extra={"id": pid})
        if _country_ok(job, wanted, *names, extra=_apple_codes(r)):
            jobs.append(job)
    jobs = _keep(jobs, queries, days, search=False)  # Apple matched the query itself
    for job in jobs[:DETAIL_CAP]:
        if site.stopped:
            break
        body = site.get(APPLE_DETAIL % job["extra"]["id"], headers=JSON_HEADERS, as_json=True)
        if body is None:
            break
        desc = apple_description(body.get("res") if isinstance(body, dict) else None)
        if desc:
            job["description"] = desc
            job["extra"].pop("title_only", None)
    return jobs


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "amazon": fetch_amazon,
    "google": fetch_google,
    "apple": fetch_apple,
}
