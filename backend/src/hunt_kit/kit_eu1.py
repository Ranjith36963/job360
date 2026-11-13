"""European job-board readers for the hunt kit, part 1: Relocate.me, SwissDevJobs and GermanTechJobs (one platform), IamExpat
Jobs, ictjob (Belgium and Luxembourg) and Berlin Startup Jobs. Same contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``,
never raises, network only through the ``http`` callable, no file IO at import, standard library only. Nothing here ranks or
scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  relocateme      any word (``relocateme:all``): relocate.me lists every job in one paged list
  techjobs        ``<host>/<category>`` with host swissdevjobs.ch or germantechjobs.de and a category page such as ``Machine-Learning``
  iamexpat        any word (``iamexpat:all``): the one paged list at iamexpat.nl/career/jobs-netherlands
  ictjob          ``be`` or ``lu`` (the newest-jobs feed), or ``be/machine-learning`` (that site's keyword search; hyphens are spaces)
  berlinstartup   any word (``berlinstartup:all``): the IT / Software Development list

Every endpoint below was probed on 2026-11-13 with the kit's own User-Agent, and robots.txt was read first. Each board's pages
are walked with ``once=True``: the first refusal (429, 403, a changed page) ends that board and keeps what was already read.
A job link that is not https on the board's own host is dropped. SwissDevJobs/GermanTechJobs open at most TECHJOBS_DETAIL_CAP
posting pages (title test first); a card whose page is not opened is listed from the card alone.

NOT built (written up in README and NOTICE):
  DEVjobs.at: devjobs.at/robots.txt answers 200, but /jobs and /sitemap.xml answer 403 with a Cloudflare "Just a moment" challenge
  page. A challenge needs a browser; the kit does not pretend to be one.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats import _rfc822_date  # type: ignore[import-not-found]  # noqa: E402
from kit_ats_eu import (  # noqa: E402
    Http,
    Job,
    _body,
    _https,
    _iso_date,
    _job,
    _ld_places,
    _ld_posting,
    _open_details,
    _page_st,
    _remote_from,
    _tree,
    _walk,
    _wrap,
)
from kit_xml import safe_fromstring  # type: ignore[import-not-found]  # noqa: E402

RELOCATE_PAGES = 8  # 20 jobs a page
IAMEXPAT_PAGES = 3  # 20 jobs a page, but every page is ~2.7 MB, so stay small
IAMEXPAT_MAX = 6_000_000
ICTJOB_PAGES = 2
BSJ_PAGES = 4
TECHJOBS_DETAIL_CAP = 25
_FEED_MAX = 5_000_000


def _t(value: Any) -> str:
    """A string field, trimmed and on one line; anything else is ''."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _classes(root: Any, tag: str, cls: str) -> list[Any]:
    return [n for n in _walk(root) if n.tag == tag and n.has_class(cls)]


def _own(href: Any, base: str, host: str) -> str:
    """Absolute https link on exactly ``host`` (query and fragment dropped), else ''."""
    try:
        u = urlsplit(urljoin(base, str(href or "").strip()))
    except ValueError:
        return ""
    return str(_https(u._replace(query="", fragment="").geturl(), host))


# --------------------------------------------------------------------------- Relocate.me
# Endpoint: GET https://relocate.me/international-jobs[?page=<n>] (robots.txt disallows only /install/, /manager/, /uploads/). The
# page is server-rendered: div.jobs-list__job holds country and employer (two div.job__company), the posting link and title
# (div.job__title > a > b, the city after "in"), and p.job__preview. 20 jobs a page. Relocate.me is a relocation/visa-sponsorship board.
_RELOCATE = "https://relocate.me"


def _relocate(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Relocate.me: the paged international-jobs list, no posting page opened."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, RELOCATE_PAGES + 1):
        st, text = _page_st(http, f"{_RELOCATE}/international-jobs" + (f"?page={page}" if page > 1 else ""), once=True)
        if text is None:
            return jobs if page > 1 else None
        cards = _classes(_tree(text), "div", "jobs-list__job")
        if not cards:
            return jobs if page > 1 else None
        for c in cards:
            try:
                info = [n.text() for n in _classes(c, "div", "job__company")]
                a = next((x for n in _classes(c, "div", "job__title") for x in _walk(n) if x.tag == "a"), None)
                b = next((x for x in _walk(a) if x.tag == "b"), None) if a is not None else None
                if a is None or b is None or len(info) < 2:
                    continue
                url, title = _own(a.attrs.get("href"), _RELOCATE, "relocate.me"), b.text()
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                city = a.text()[len(title):].strip()
                city = city[3:].strip() if city.lower().startswith("in ") else city
                place = ", ".join(x for x in (city, info[0]) if x)
                prev = next((n.text() for n in _classes(c, "p", "job__preview")), "")
                job = _job(row, "relocateme", title=title, url=url, location=place, description=prev, remote=_remote_from(place),
                           extra={"relocation": True})
                job["company"] = info[1]
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
    return jobs


# --------------------------------------------------------------------------- SwissDevJobs and GermanTechJobs
# Endpoint: GET https://<host>/jobs/<Category>/all (server-rendered; a card is <a title="<Title> job in <City>" href="/jobs/<slug>">,
# about 25 per category), then GET https://<host>/jobs/<slug>, which carries a schema.org JobPosting in ld+json (title,
# hiringOrganization, datePosted, jobLocation, baseSalary). The sites' /rss answers a year-old prerendered copy in an XML-viewer
# HTML wrapper to the kit's User-Agent (lastBuildDate 2025-11-06), so it is NOT used. robots.txt: /api/ is disallowed only for
# Meta-ExternalAgent, and the job pages are allowed. Both sites run the same platform and the same page markup.
_TECHJOBS_HOSTS = ("swissdevjobs.ch", "germantechjobs.de")
_TECH_CAT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
_TECH_SLUG = re.compile(r"/jobs/([A-Za-z0-9][A-Za-z0-9._~-]{2,250})")


def _techjobs(row: dict[str, Any], http: Http) -> list[Job] | None:
    """SwissDevJobs / GermanTechJobs: one category list, then the posting page of each wanted job (capped)."""
    host, _, cat = str(row.get("token") or "").strip().partition("/")
    host = host.lower()
    if host not in _TECHJOBS_HOSTS or not _TECH_CAT.fullmatch(cat) or cat.lower() == "all":
        return None
    base = f"https://{host}"
    st, text = _page_st(http, f"{base}/jobs/{quote(cat, safe='')}/all", once=True)
    if text is None:
        return None
    cards: dict[str, tuple[str, str]] = {}
    for a in _walk(_tree(text)):
        label = a.attrs.get("title", "") if a.tag == "a" else ""
        m = _TECH_SLUG.fullmatch(a.attrs.get("href", "")) if " job in " in label else None
        if m:
            title, _, city = _t(label).rpartition(" job in ")
            cards.setdefault(f"{base}/jobs/{m.group(1)}", (title, city))
    if not cards:
        return None  # a changed page, not an empty category
    pages = _open_details(row, http, [(u, t) for u, (t, _c) in cards.items()], TECHJOBS_DETAIL_CAP)
    jobs: list[Job] = []
    for url, (title, city) in cards.items():
        company, desc, posted, place, extra = "", "", "", city, {}
        posting = _ld_posting(_tree(pages[url])) if url in pages else None
        if posting:
            org = posting.get("hiringOrganization")
            company = _t(org.get("name")) if isinstance(org, dict) else ""
            title = _t(posting.get("title")) or title
            desc, posted = _body(posting.get("description")), _iso_date(posting.get("datePosted"))
            place = _ld_places(posting, region=False) or city
            sal = posting.get("baseSalary")
            val = sal.get("value") if isinstance(sal, dict) else None
            if isinstance(val, dict):
                extra = {"salary": {k: val[k] for k in ("minValue", "maxValue") if isinstance(val.get(k), (int, float)) and not isinstance(val.get(k), bool)}
                         | {"currency": _t(sal.get("currency")).upper()}}
        if title:
            job = _job(row, "techjobs", title=title, url=url, location=place, posted=posted, description=desc,
                       remote=_remote_from(place, title), extra=extra)
            job["company"] = company or ("SwissDevJobs" if host == "swissdevjobs.ch" else "GermanTechJobs")
            jobs.append(job)
    return jobs


# --------------------------------------------------------------------------- IamExpat Jobs
# Endpoint: GET https://www.iamexpat.nl/career/jobs-netherlands[?page=<n>] (robots.txt: Crawl-delay 1; /job/ and /jobProvider/ are
# disallowed, /career/ is not). Server-rendered Next.js list, 20 cards a page, about 2.7 MB a page. A card is an <a> to
# /career/jobs-netherlands/<category>/<slug>/<id> holding the title (span.title-7), the category (div.body-small), then
# div.body-medium rows: the city and "Posted date <Month d, yyyy>". The employer is on the posting page (another 2.4 MB, no ld+json),
# which the kit does not open: only a featured card's logo alt carries it, else the job is listed under "IamExpat".
_IAMEXPAT = "https://www.iamexpat.nl"
_IAMEXPAT_PATH = re.compile(r"/career/jobs-netherlands/[a-z0-9-]{1,80}/[a-z0-9-]{1,200}/[A-Za-z0-9]{16,30}")


def _iamexpat(row: dict[str, Any], http: Http) -> list[Job] | None:
    """IamExpat Jobs: the paged Netherlands list, cards only."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, IAMEXPAT_PAGES + 1):
        st, text = _page_st(http, f"{_IAMEXPAT}/career/jobs-netherlands" + (f"?page={page}" if page > 1 else ""), once=True, max_bytes=IAMEXPAT_MAX)
        if text is None:
            return jobs if page > 1 else None
        found = 0
        for a in _walk(_tree(text)):
            if a.tag != "a" or not _IAMEXPAT_PATH.fullmatch(a.attrs.get("href", "")):
                continue
            found += 1
            try:
                url = _own(a.attrs.get("href"), _IAMEXPAT, "www.iamexpat.nl")
                title = next((n.text() for n in _walk(a) if n.tag == "span" and n.has_class("title-7")), "")
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                rows = [n.text() for n in _walk(a) if n.tag == "div" and n.has_class("body-medium")]
                place = next((r for r in rows if not r.startswith("Posted date")), "")
                posted = ""
                for r in rows:
                    if r.startswith("Posted date "):
                        try:
                            posted = datetime.strptime(r[len("Posted date "):].strip(), "%B %d, %Y").strftime("%Y-%m-%d")
                        except ValueError:
                            posted = ""
                cat = next((n.text() for n in _walk(a) if n.tag == "div" and n.has_class("body-small")), "")
                logo = next((n.attrs.get("alt", "") for n in _walk(a) if n.tag == "img" and n.attrs.get("alt")), "")
                job = _job(row, "iamexpat", title=title, url=url, location=place, posted=posted, remote=_remote_from(place, title),
                           extra={"category": cat})
                job["company"] = _t(logo) or "IamExpat"
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        if not found:
            return jobs if page > 1 else None
    return jobs


# --------------------------------------------------------------------------- ictjob.be / ictjob.lu
# Endpoints: GET https://www.ictjob.<be|lu>/en/rss (the 50 newest jobs; item title is "<Title> - <Company> - <City>", link, pubDate, a
# description teaser) and, for a keyword token, GET https://www.ictjob.<tld>/en/search-it-jobs?keywords=<kw>[&page=<n>] (server-rendered
# li.search-item with schema.org microdata: a.job-title href, h2 title, span.job-company, span.job-location; 20 a page; the date is a
# bare "12/11" with no year, so it is not read). robots.txt disallows only legal pages and /*/ajax/. Same platform on both domains.
_ICT_TOKEN = re.compile(r"(be|lu)(?:/([a-z0-9][a-z0-9-]{0,39}))?")


def _ictjob(row: dict[str, Any], http: Http) -> list[Job] | None:
    """ictjob: the newest-jobs feed, or a keyword search, for Belgium or Luxembourg."""
    m = _ICT_TOKEN.fullmatch(str(row.get("token") or "").strip().lower())
    if not m:
        return None
    host = f"www.ictjob.{m.group(1)}"
    base = f"https://{host}"
    jobs: list[Job] = []
    seen: set[str] = set()
    if not m.group(2):
        st, text = _page_st(http, f"{base}/en/rss", once=True, max_bytes=_FEED_MAX)
        root = safe_fromstring(text) if text else None
        if root is None:
            return None
        for it in root.iter("item"):
            try:
                kid = {c.tag: (c.text or "") for c in it}
                url = _own(kid.get("link"), base, host)
                parts = [p.strip() for p in html.unescape(kid.get("title", "")).split(" - ")]
                if not url or url in seen or len(parts) < 3:
                    continue
                seen.add(url)
                job = _job(row, "ictjob", title=" - ".join(parts[:-2]), url=url, location=parts[-1], posted=_rfc822_date(kid.get("pubDate")),
                           description=_body(html.unescape(kid.get("description", ""))), remote=_remote_from(parts[-1]))
                job["company"] = parts[-2]
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        return jobs
    for page in range(1, ICTJOB_PAGES + 1):
        st, text = _page_st(http, f"{base}/en/search-it-jobs?keywords={quote(m.group(2).replace('-', ' '))}" + (f"&page={page}" if page > 1 else ""), once=True)
        if text is None:
            return jobs if page > 1 else None
        items = [n for n in _walk(_tree(text)) if n.tag == "li" and n.has_class("search-item")]
        if not items:
            return jobs if page > 1 else None
        for li in items:
            try:
                a = next((n for n in _walk(li) if n.tag == "a" and n.has_class("job-title")), None)
                url = _own(a.attrs.get("href"), base, host) if a is not None else ""
                title = next((n.text() for n in _walk(li) if n.tag == "h2" and n.has_class("job-title")), "")
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                place = next((n.text() for n in _walk(li) if n.tag == "span" and n.has_class("job-location")), "")
                desc = next((n.text() for n in _walk(li) if n.tag == "span" and n.has_class("job-keywords")), "")
                job = _job(row, "ictjob", title=title, url=url, location=place, description=desc, remote=_remote_from(place))
                job["company"] = next((n.text() for n in _walk(li) if n.tag == "span" and n.has_class("job-company")), "") or "ictjob"
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
    return jobs


# --------------------------------------------------------------------------- Berlin Startup Jobs
# Endpoint: GET https://berlinstartupjobs.com/engineering/[page/<n>/] (robots.txt disallows only /wp-admin/). Server-rendered WordPress
# list: li.bjs-jlid holds h4.bjs-jlid__h > a (posting link, title), a.bjs-jlid__b (employer) and div.bjs-jlid__description (a teaser).
# Every job is in Berlin. The /feed/ RSS is not used: it carries 12 jobs of all departments and page 2 repeats the feed's title.
_BSJ = "https://berlinstartupjobs.com"


def _berlinstartup(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Berlin Startup Jobs: the IT / Software Development list, a few pages."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, BSJ_PAGES + 1):
        st, text = _page_st(http, f"{_BSJ}/engineering/" + (f"page/{page}/" if page > 1 else ""), once=True)
        if text is None:
            return jobs if page > 1 else None
        items = _classes(_tree(text), "li", "bjs-jlid")
        if not items:
            return jobs if page > 1 else None
        for li in items:
            try:
                a = next((x for n in _classes(li, "h4", "bjs-jlid__h") for x in _walk(n) if x.tag == "a"), None)
                url = _own(a.attrs.get("href"), _BSJ, "berlinstartupjobs.com") if a is not None else ""
                title = a.text() if a is not None else ""
                if not url or not title or url in seen:
                    continue
                seen.add(url)
                job = _job(row, "berlinstartup", title=title, url=url, location="Berlin, Germany",
                           description=next((n.text() for n in _classes(li, "div", "bjs-jlid__description")), ""))
                job["company"] = next((n.text() for n in _classes(li, "a", "bjs-jlid__b")), "") or "Berlin Startup Jobs"
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "relocateme": _wrap(_relocate),
    "techjobs": _wrap(_techjobs),
    "iamexpat": _wrap(_iamexpat),
    "ictjob": _wrap(_ictjob),
    "berlinstartup": _wrap(_berlinstartup),
}
