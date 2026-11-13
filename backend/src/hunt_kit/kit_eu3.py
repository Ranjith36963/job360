"""Freelance and contract board readers for the hunt kit: freelancermap and Free-Work. Same contract as kit_ats_eu.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import, standard
library only. Nothing here ranks or scores a job. Every job carries ``extra["employment_type"]`` = "freelance" or "contract"
(the kit's existing key for the job type) and ``extra["contract_label"]`` = the board's own label for it.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  freelancermap   ``all``, or a search-word slug such as ``machine-learning`` (hyphens are spaces)
  freework        ``all``, or a search-word slug such as ``machine-learning`` (hyphens are spaces)

Both endpoints were probed on 2026-11-13 with the kit's own User-Agent, and robots.txt was read first. Pages are walked with
``once=True``: the first refusal ends the board and keeps what was already read. A job link that is not https on the board's own host
is dropped. No posting page is opened.

NOT built (written up in README and NOTICE):
  Malt (malt.com): https://www.malt.com/robots.txt and https://www.malt.com/s?q=machine%20learning both answer 403 with a Cloudflare
  "Just a moment..." challenge page. A challenge needs a browser; the kit does not pretend to be one.
"""
# ruff: noqa: E501
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    Http,
    Job,
    _https,
    _job,
    _join,
    _page_st,
    _remote_from,
    _tree,
    _walk,
    _wrap,
)

FREELANCERMAP_MAX = 3_000_000  # a page is ~1.3 MB, over nothing but close to the 2 MB default
FREEWORK_PAGES = 3  # 15 postings a page
_TERM = re.compile(r"all|[a-z0-9][a-z0-9-]{0,60}")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _own(href: Any, base: str, host: str) -> str:
    """Absolute https link on exactly ``host`` (query and fragment dropped), else ''."""
    try:
        u = urlsplit(urljoin(base, str(href or "").strip()))
    except ValueError:
        return ""
    return str(_https(u._replace(query="", fragment="").geturl(), host))


def _term(row: dict[str, Any]) -> str | None:
    """The search words of the row's token ('' for ``all``), or None when the token is not a slug."""
    tok = str(row.get("token") or "").strip().lower()
    if not _TERM.fullmatch(tok):
        return None
    return "" if tok == "all" else tok.replace("-", " ")


def _kind(label: str) -> str:
    """'freelance' for a label that says freelance, 'contract' for the others (contractor, agency contract)."""
    return "freelance" if "freelance" in label.lower() else "contract"


def _day(text: str, fmt: str) -> str:
    try:
        return datetime.strptime(text.strip(), fmt).strftime("%Y-%m-%d")
    except ValueError:
        return ""


# --------------------------------------------------------------------------- freelancermap
# Endpoint: GET https://www.freelancermap.com/projects?query=<words>&sort=1 (robots.txt: everything allowed for general crawlers). The page
# is server-rendered: div.project-card holds span (employer, in div.project-card-company), a[data-id=project-card-title] (link /project/<slug>,
# title), a[data-id=project-card-city] and a[data-id=project-card-country], div[data-testid=remoteInPercent] (On-site, Hybrid, 100% remote),
# div[data-testid=type] (Freelance, Agency contract (e.g. ANUE), Permanent) and div[data-testid=created] (a time "16:28" for today, else
# "12.11.2026"). One page of about 22 cards: ?pagenr= is applied by the site's scripts only, so the server answers page 1 again and the
# reader asks once. A "Permanent" posting is dropped: this reader lists freelance and contract work only.
_FM = "https://www.freelancermap.com"


def _freelancermap(row: dict[str, Any], http: Http) -> list[Job] | None:
    """freelancermap: one page of freelance and contract IT projects."""
    words = _term(row)
    if words is None:
        return None
    url = f"{_FM}/projects?sort=1" + (f"&query={quote(words)}" if words else "")
    st, text = _page_st(http, url, once=True, max_bytes=FREELANCERMAP_MAX)
    if text is None:
        return None
    cards = [n for n in _walk(_tree(text)) if n.tag == "div" and n.has_class("project-card")]
    if not cards:
        return None  # a changed page, not an empty search
    jobs: list[Job] = []
    seen: set[str] = set()
    for c in cards:
        try:
            a = next((n for n in _walk(c) if n.tag == "a" and n.attrs.get("data-id") == "project-card-title"), None)
            link = _own(a.attrs.get("href"), _FM, "www.freelancermap.com") if a is not None else ""
            title = a.text() if a is not None else ""
            if not link or not title or link in seen:
                continue
            by_id = {n.attrs.get("data-testid", ""): n.text() for n in _walk(c) if n.attrs.get("data-testid")}
            label = by_id.get("type", "")
            if label.lower().startswith("permanent"):
                continue
            seen.add(link)
            city = " ".join(n.text() for n in _walk(c) if n.tag == "a" and n.attrs.get("data-id") == "project-card-city").strip(" ,")
            country = " ".join(n.text() for n in _walk(c) if n.tag == "a" and n.attrs.get("data-id") == "project-card-country")
            place = _join(city, country)
            created = by_id.get("created", "")
            posted = _today() if re.fullmatch(r"\d{1,2}:\d{2}", created) else _day(created, "%d.%m.%Y")
            company = next((x.text() for n in _walk(c) if n.tag == "div" and n.has_class("project-card-company") for x in _walk(n) if x.tag == "span"), "")
            mode = by_id.get("remoteInPercent", "")
            job = _job(row, "freelancermap", title=title, url=link, location=place, posted=posted,
                       remote=True if "remote" in mode.lower() and "on-site" not in mode.lower() else _remote_from(place),
                       extra={"employment_type": _kind(label), "contract_label": label, "work_mode": mode})
            job["company"] = company or "freelancermap"
            jobs.append(job)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Free-Work
# Endpoint: GET https://www.free-work.com/en-gb/tech-it/jobs?contracts=contractor[&query=<words>][&page=<n>] (robots.txt disallows only
# /login, /logout and /fw-deals). contracts=contractor is the site's own "freelancers" filter (the page title becomes "IT Jobs for
# Freelancers"), so every card is a contract job. Server-rendered Nuxt page, 15 cards a page, each printed twice (a desktop and a mobile
# layout): the desktop one is read, h2 > a (link /en-gb/tech-it/job-mission/<category>/<slug>, title), time (dd/mm/yyyy), div.font-bold
# (employer), the contract tag, and side rows "Start date", "Duration", "Rate" and "Location".
_FW = "https://www.free-work.com"
_FW_PATH = re.compile(r"/en-gb/tech-it/job-mission/[A-Za-z0-9._-]{1,200}/[A-Za-z0-9._-]{1,200}")


def _fw_cards(root: Any) -> list[tuple[Any, Any]]:
    """(anchor, card) for every h2 > a job link. A card is the h2's ancestor, at most four levels up, that still holds only that one job
    (the desktop layout: heading, tag, time, employer and the side rows). Counts are built bottom-up once, so the cost stays linear."""
    parent: dict[int, Any] = {}
    order = [root]
    for n in _walk(root):
        order.append(n)
    for n in order:
        for k in n.kids:
            if not isinstance(k, str):
                parent[id(k)] = n
    own: dict[int, Any] = {}
    cnt: dict[int, int] = {}
    for n in order:
        a = next((x for x in n.kids if not isinstance(x, str) and x.tag == "a" and _FW_PATH.fullmatch(x.attrs.get("href", ""))), None) if n.tag == "h2" else None
        if a is not None:
            own[id(n)] = a
            cnt[id(n)] = 1
    for n in reversed(order):
        par = parent.get(id(n))
        if par is not None and cnt.get(id(n)):
            cnt[id(par)] = cnt.get(id(par), 0) + cnt[id(n)]
    out = []
    for n in order:
        if id(n) in own:
            card = n
            for _ in range(4):
                par = parent.get(id(card))
                if par is None or cnt.get(id(par), 0) != 1:
                    break
                card = par
            out.append((own[id(n)], card))
    return out


def _freework(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Free-Work: a few pages of the contractor (freelance) filter."""
    words = _term(row)
    if words is None:
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, FREEWORK_PAGES + 1):
        url = f"{_FW}/en-gb/tech-it/jobs?contracts=contractor" + (f"&query={quote(words)}" if words else "") + (f"&page={page}" if page > 1 else "")
        st, text = _page_st(http, url, once=True, max_bytes=FREELANCERMAP_MAX)
        if text is None:
            return jobs if page > 1 else None
        pairs = _fw_cards(_tree(text))
        if not pairs:
            # the page's own freelancer heading means an alive board with no match; anything else is a page this reader does not know
            return jobs if page > 1 or "IT Jobs for Freelancers" in text else None
        for a, card in pairs:
            link = _own(a.attrs.get("href"), _FW, "www.free-work.com")
            title = a.text()
            if not link or not title or link in seen:
                continue
            seen.add(link)
            try:
                tag = next((n.text() for n in _walk(card) if n.tag == "div" and n.has_class("truncate") and n.text().lower().startswith("contract")), "Contractor")
                side = {}
                for d in _walk(card):
                    lab = next((s for s in d.kids if not isinstance(s, str) and s.tag == "span" and s.has_class("uppercase")), None)
                    if d.tag == "div" and lab is not None:
                        side[lab.text()] = d.text()[len(lab.text()):].strip()
                company = next((n.text() for n in _walk(card) if n.tag == "div" and n.has_class("font-bold")), "")
                when = next((n.text() for n in _walk(card) if n.tag == "time"), "")
                place = side.get("Location", "")
                job = _job(row, "freework", title=title, url=link, location=place, posted=_day(when, "%d/%m/%Y"),
                           description=next((n.text() for n in _walk(card) if n.tag == "div" and n.has_class("line-clamp-4")), ""),
                           remote=_remote_from(place, title),
                           extra={"employment_type": _kind(tag), "contract_label": tag, "rate": side.get("Rate", ""),
                                  "duration": side.get("Duration", ""), "start_date": side.get("Start date", "")})
                job["company"] = company or "Free-Work"
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "freelancermap": _wrap(_freelancermap),
    "freework": _wrap(_freework),
}
