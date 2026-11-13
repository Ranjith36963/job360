"""European job-board readers for the hunt kit, part 2: StepStone Germany, jobs.ch and jobup.ch (one platform), itjobs.pt and
Magnet.me. Same contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http``
callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  stepstone     a keyword slug of www.stepstone.de/jobs/<slug>, such as ``machine-learning-engineer``
  swissjobs     ``<host>/<keywords>`` with host jobs.ch or jobup.ch, hyphens are spaces: ``jobs.ch/machine-learning``
  itjobspt      any word (``itjobspt:all``): the one paged list at itjobs.pt/emprego
  magnetme      any word (``magnetme:all``): the newest postings of the site's own sitemap

Every endpoint below was probed on 2026-11-13 with the kit's own User-Agent, and robots.txt was read first. Each board's pages are
walked with ``once=True``: the first refusal (429, 403, a changed page) ends that board and keeps what was already read. A job link
that is not https on the board's own host is dropped.

NOT built (written up in README and NOTICE):
  Make it in Germany: every page of www.make-it-in-germany.com (tested: /en/ and /en/working-in-germany/job-listings) answers 200
  with a Radware bot-manager page (title "Radware Page"), not the content. Only robots.txt and the sitemap index are served.
  gradireland.com: the pages are a Gatsby shell; the jobs come from POST https://gradireland.com/ext/svc/inferno-search-service-1-0/search,
  which answered 400 "Bad Request Exception" to every request body tried. Its body format is only in the site's minified scripts.
  itjobs.pt API (api.itjobs.pt/job/list.json): answers 200 with {"error":{"message":"API key required."}}; the public list is read instead.
"""
# ruff: noqa: E501
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    _SITEMAP_MAX,
    _XML,
    Http,
    Job,
    _body,
    _get,
    _https,
    _iso_date,
    _job,
    _ld_places,
    _ld_posting,
    _open_details,
    _page_st,
    _remote_from,
    _sitemap_locs,
    _tree,
    _walk,
    _words,
    _wrap,
)
from kit_xml import safe_fromstring  # type: ignore[import-not-found]  # noqa: E402

SWISS_PAGES = 3  # 20 postings a page
ITJOBS_PAGES = 6
MAGNET_DETAIL_CAP = 25


def _t(value: Any) -> str:
    """A string field, trimmed and on one line; anything else is ''."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _own(href: Any, base: str, host: str) -> str:
    """Absolute https link on exactly ``host`` (query and fragment dropped), else ''."""
    try:
        u = urlsplit(urljoin(base, str(href or "").strip()))
    except ValueError:
        return ""
    return str(_https(u._replace(query="", fragment="").geturl(), host))


def _txt(root: Any, tag: str, at: str) -> str:
    return next((n.text() for n in _walk(root) if n.tag == tag and n.attrs.get("data-at") == at), "")


# --------------------------------------------------------------------------- StepStone Germany
# Endpoint: GET https://www.stepstone.de/jobs/<keyword-slug> (robots.txt allows /jobs/<slug>; it disallows /jobs/*?* so no query is sent
# and there is one page, 25 postings, per keyword). Server-rendered: article[data-at=job-item] holds a[data-at=job-item-title] (link,
# title), span[data-at=job-item-company-name], span[data-at=job-item-location], span[data-at=job-item-work-from-home] and a teaser
# (div[data-at=jobcard-content]). The age is a German "vor 3 Tagen" text, so no date is read. Other StepStone countries are not read.
_STEP_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")


def _stepstone(row: dict[str, Any], http: Http) -> list[Job] | None:
    """StepStone Germany: one keyword list page, cards only."""
    slug = str(row.get("token") or "").strip().lower()
    if not _STEP_SLUG.fullmatch(slug):
        return None
    st, text = _page_st(http, f"https://www.stepstone.de/jobs/{slug}", once=True, max_bytes=3_000_000)
    if text is None:
        return None
    cards = [n for n in _walk(_tree(text)) if n.tag == "article" and n.attrs.get("data-at") == "job-item"]
    if not cards:
        return None  # a changed page, not an empty search
    jobs: list[Job] = []
    seen: set[str] = set()
    for c in cards:
        try:
            a = next((n for n in _walk(c) if n.tag == "a" and n.attrs.get("data-at") == "job-item-title"), None)
            url = _own(a.attrs.get("href"), "https://www.stepstone.de", "www.stepstone.de") if a is not None else ""
            title = a.text() if a is not None else ""
            if not url or not title or url in seen:
                continue
            seen.add(url)
            place, mode = _txt(c, "span", "job-item-location"), _txt(c, "span", "job-item-work-from-home")
            job = _job(row, "stepstone", title=title, url=url, location=place, description=_txt(c, "div", "jobcard-content"),
                       remote=True if mode.lower().startswith("100%") or "remote" in mode.lower() else _remote_from(place),
                       extra={"work_mode": mode})
            job["company"] = _txt(c, "span", "job-item-company-name") or "StepStone"
            jobs.append(job)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- jobs.ch and jobup.ch
# Endpoint: GET https://www.jobs.ch/en/vacancies/?term=<kw>[&page=<n>] and https://www.jobup.ch/en/jobs/?term=<kw>[&page=<n>]. The
# page carries a schema.org ItemList of 20 JobPosting objects in ld+json (title, hiringOrganization.name, datePosted, jobLocation,
# employmentType, a one-line description, url). The public JSON API under /api/ is NOT used: robots.txt disallows /api/.
_SWISS = {"jobs.ch": ("www.jobs.ch", "https://www.jobs.ch/en/vacancies/"), "jobup.ch": ("www.jobup.ch", "https://www.jobup.ch/en/jobs/")}
_SWISS_TERM = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")


def _ld_items(text: str) -> list[dict[str, Any]]:
    """The JobPosting objects of every ItemList in the page's ld+json scripts."""
    out: list[dict[str, Any]] = []
    for s in _walk(_tree(text)):
        if s.tag != "script" or s.attrs.get("type", "").lower() != "application/ld+json":
            continue
        try:
            data = json.loads(s.raw_text(), strict=False)
        except (ValueError, RecursionError):
            continue
        for d in data if isinstance(data, list) else [data]:
            els = d.get("itemListElement") if isinstance(d, dict) and d.get("@type") == "ItemList" else None
            for el in els if isinstance(els, list) else []:
                it = el.get("item") if isinstance(el, dict) else None
                if isinstance(it, dict) and it.get("@type") == "JobPosting":
                    out.append(it)
    return out


def _swissjobs(row: dict[str, Any], http: Http) -> list[Job] | None:
    """jobs.ch / jobup.ch: a few pages of one keyword search, read from the page's ld+json."""
    site, _, term = str(row.get("token") or "").strip().lower().partition("/")
    if site not in _SWISS or not _SWISS_TERM.fullmatch(term):
        return None
    host, base = _SWISS[site]
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, SWISS_PAGES + 1):
        st, text = _page_st(http, f"{base}?term={quote(term.replace('-', ' '))}" + (f"&page={page}" if page > 1 else ""), once=True, max_bytes=3_000_000)
        items = _ld_items(text) if text else []
        if not items:
            return jobs if page > 1 else None
        for p in items:
            try:
                url = _own(p.get("url"), base, host)
                title = _t(p.get("title"))
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                org = p.get("hiringOrganization")
                place = _ld_places(p, region=False)
                job = _job(row, "swissjobs", title=title, url=url, location=place, posted=_iso_date(p.get("datePosted")),
                           description=_body(p.get("description")), remote=_remote_from(place, title),
                           extra={"employment_type": _t(p.get("employmentType"))})
                job["company"] = (_t(org.get("name")) if isinstance(org, dict) else "") or site
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
    return jobs


# --------------------------------------------------------------------------- itjobs.pt
# Endpoint: GET https://www.itjobs.pt/emprego[?page=<n>] (robots.txt: Crawl-delay 1; free-text ?q= and the filter parameters are
# disallowed, ?page= is not). Server-rendered ul.listing: div.list-title > a.title (link /oferta/<id>/<slug>, title), div.list-name > a
# (employer), div.list-details (city, then the work model: Presencial, Hibrido or Remoto). The API at api.itjobs.pt needs a key.
_ITJOBS = "https://www.itjobs.pt"
_ITJOBS_PATH = re.compile(r"/oferta/\d{1,9}/[a-z0-9-]{1,200}")


def _itjobspt(row: dict[str, Any], http: Http) -> list[Job] | None:
    """itjobs.pt: the paged plain list, cards only."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, ITJOBS_PAGES + 1):
        st, text = _page_st(http, f"{_ITJOBS}/emprego" + (f"?page={page}" if page > 1 else ""), once=True)
        if text is None:
            return jobs if page > 1 else None
        items = [n for n in _walk(_tree(text)) if n.tag == "li" and any(x.tag == "div" and x.has_class("list-title") for x in _walk(n))]
        if not items:
            return jobs if page > 1 else None
        for li in items:
            try:
                a = next((n for n in _walk(li) if n.tag == "a" and n.has_class("title")), None)
                path = a.attrs.get("href", "") if a is not None else ""
                url = _own(path, _ITJOBS, "www.itjobs.pt") if _ITJOBS_PATH.fullmatch(path) else ""
                title = a.text() if a is not None else ""
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                company = next((x.text() for n in _walk(li) if n.tag == "div" and n.has_class("list-name") for x in _walk(n) if x.tag == "a"), "")
                details = next((n.text() for n in _walk(li) if n.tag == "div" and n.has_class("list-details")), "")
                parts = details.split()  # "Setubal Presencial": the last word is the work model
                mode = parts[-1] if parts and parts[-1].lower() in ("presencial", "híbrido", "hibrido", "remoto") else ""
                place = details[:len(details) - len(mode)].strip() if mode else details
                job = _job(row, "itjobspt", title=title, url=url, location=place, remote=True if mode.lower() == "remoto" else None,
                           extra={"work_mode": mode})
                job["company"] = company or "itjobs.pt"
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
    return jobs


# --------------------------------------------------------------------------- Magnet.me
# Endpoint: GET https://magnet.me/sitemaps/en-fresh.xml (robots.txt allows it and lists it; ~550 /en/opportunity/<id>/<slug> links, the
# recently changed ones), then GET each wanted /en/opportunity/<id>/<slug>, which carries a schema.org JobPosting in ld+json (title,
# hiringOrganization, datePosted, jobLocation, employmentType, baseSalary). Newest first (highest id); the slug is tested with
# ``title_ok`` before a page is opened, at most MAGNET_DETAIL_CAP pages. Only opened postings are listed: a sitemap slug has no employer.
_MAGNET_PATH = re.compile(r"/en/opportunity/(\d{1,9})/([a-z0-9-]{1,200})")


def _magnetme(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Magnet.me: the fresh-postings sitemap, then one ld+json posting page per wanted job."""
    # the sitemap answers 406 to the page helper's "Accept: text/html", so ask for XML
    st, text = _get(http, "https://magnet.me/sitemaps/en-fresh.xml", as_json=False, headers=_XML, once=True, max_bytes=_SITEMAP_MAX)
    root = safe_fromstring(text) if st == 200 and isinstance(text, str) else None
    if root is None:
        return None
    found: dict[int, tuple[str, str]] = {}
    for loc in _sitemap_locs(root, "url"):
        try:
            u = urlsplit(loc)
        except ValueError:
            continue
        m = _MAGNET_PATH.fullmatch(u.path)
        if m and u.scheme == "https" and u.hostname == "magnet.me" and u.query == "":
            found[int(m.group(1))] = (f"https://magnet.me{u.path}", _words(m.group(2)))
    pages = _open_details(row, http, [found[i] for i in sorted(found, reverse=True)], MAGNET_DETAIL_CAP)
    jobs: list[Job] = []
    for url, _w in (found[i] for i in sorted(found, reverse=True)):
        posting = _ld_posting(_tree(pages[url])) if url in pages else None
        title = _t(posting.get("title")) if posting else ""
        if not posting or not title:
            continue
        org = posting.get("hiringOrganization")
        place = _ld_places(posting, region=False)
        job = _job(row, "magnetme", title=title, url=url, location=place, posted=_iso_date(posting.get("datePosted")),
                   description=_body(posting.get("description")), remote=_remote_from(place, title),
                   extra={"employment_type": _t(posting.get("employmentType"))})
        job["company"] = (_t(org.get("name")) if isinstance(org, dict) else "") or "Magnet.me"
        jobs.append(job)
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "stepstone": _wrap(_stepstone),
    "swissjobs": _wrap(_swissjobs),
    "itjobspt": _wrap(_itjobspt),
    "magnetme": _wrap(_magnetme),
}
