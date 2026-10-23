"""Job-board fetchers for the hunt kit, wave 4 batch B: GeekJob, ApplicantPro, Potok and Huntflow. Same contract as
kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO
at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  geekjob      any word (``geekjob:all``): geekjob.ru is ONE Russian IT board with no per-employer board
  applicantpro tenant subdomain of <tenant>.applicantpro.com (the sibling host of iSolved Hire, which kit_ats_us.py reads)
  potok        tenant subdomain of <tenant>.potok.io (a Russian ATS)
  huntflow     tenant subdomain of <tenant>.huntflow.io (a Russian ATS)

Endpoints and field names are adapted from the MIT-licensed freehire source adapters (see NOTICE.md); the code is original. The
container this was written in could not reach any of these hosts, so every parser is tested on fixtures shaped like the
references' test data only. Posting pages are opened only for cards whose title passes the run's ``title_ok`` test, up to 25
per board, and the first refusal ends a board's detail requests.

NOT built:
  Huntflow community hubs (huntflow.go:52-53, :113-118, :174-190): a hub board nests partner companies and the employer is read
  from each vacancy's division breadcrumb. The kit has no per-row hub flag, so a hub board is listed under the row's own name.
  Nothing else in these four sources needs a login, cookie, CAPTCHA or browser.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    _SITEMAP_MAX,
    Http,
    Job,
    _body,
    _get,
    _iso_date,
    _job,
    _label,
    _ld_places,
    _ld_posting,
    _links,
    _Node,
    _open_details,
    _page_st,
    _remote_from,
    _title_ok,
    _tree,
    _walk,
    _wrap,
)

DETAIL_CAP = 25  # detail requests per board (the references fan out over every posting)
GEEKJOB_PAGES = 30  # geekjob.go:49 allows 100; the public feed is about 13 pages
ID_ONLY_CAP = 40  # ApplicantPro lists ids only (no title in the sitemap), so a posting needs its detail request
_SLUG = re.compile(r"[A-Za-z0-9_-]{1,200}")


def _text(value: Any) -> str:
    """Entity-decoded text on one line (APIs leave entities in plain-text fields)."""
    return " ".join(html.unescape(str(value or "")).replace("�", "").split())


def _path(href: str) -> str:
    """The URL path of an href, '' when it does not parse (a malformed href must cost only its own link)."""
    try:
        return urlsplit(href).path
    except ValueError:
        return ""


def _wanted(row: dict[str, Any], title: str) -> bool:
    try:
        return bool(_title_ok(row, title))
    except Exception:  # noqa: BLE001 - a failing filter must not lose the board
        return True


def _refused(st: int) -> bool:
    """A failed detail request that ends the board's detail run (a 404/410 is one posting gone, not a refusal)."""
    return st not in (404, 410)


# --------------------------------------------------------------------------- GeekJob
# Borrowed from freehire internal/ingest/sources/geekjob.go:11-17 (geekjob.ru, a Russian IT board; the listing is server-rendered HTML
# paged as /vacancies/N, 1-based, each card linking to /vacancy/<hex id>), :43 (base https://geekjob.ru/), :49 (page cap), :60-72
# (walk until a page yields no new link), :147 (id = 24 hex characters after /vacancy/, the plural /vacancies never matches), :76-110
# (each posting is read from its detail page's schema.org JobPosting: title, hiringOrganization.name, description (HTML-unescaped),
# datePosted, jobLocation), :113-124 (company = the ad's hiringOrganization, else the row), :150-166 (the "jobinfo" chip block
# says "удаленная работа" -> remote, "работа в офисе" -> office, neither -> unset). geekjob_test.go:46-51: a card carries several
# anchors to the same vacancy (title, company, date), so the id is the dedup key. Token ignored. NOT IN REFERENCE: the title hint
# for title_ok is the card's ``a.title`` anchor (geekjob_test.go:48), else its longest anchor text; a posting that was not opened is
# not listed (the listing has no fields of its own).
_GJ_ID = re.compile(r"^/vacancy/([0-9a-f]{24})/?$")
_GJ_REMOTE, _GJ_OFFICE = "удаленная работа", "работа в офисе"


def _geekjob(row: dict[str, Any], http: Http) -> list[Job] | None:
    """geekjob.ru listing pages + one ld+json vacancy page per wanted posting (token ignored)."""
    host = "geekjob.ru"
    found: dict[str, str] = {}
    hints: dict[str, str] = {}
    for n in range(1, GEEKJOB_PAGES + 1):
        st, listing = _page_st(http, f"https://{host}/vacancies/{n}", once=True)
        if listing is None:
            if n == 1:
                return None
            break  # a refusal or a miss ends the listing; what was gathered is kept
        root = _tree(listing)
        fresh = 0
        for u, label in _links(root, f"https://{host}/", host, lambda h: bool(_GJ_ID.match(_path(h)))).items():
            m = _GJ_ID.match(_path(u))
            if m and m.group(1) not in found:
                found[m.group(1)] = u
                fresh += 1
            if m:
                hints[m.group(1)] = max(hints.get(m.group(1), ""), label, key=len)
        for a in _walk(root):  # the card's own title anchor wins over the longest text
            if a.tag == "a" and a.has_class("title"):
                m = _GJ_ID.match(_path(a.attrs.get("href", "")))
                if m and m.group(1) in found and a.text():
                    hints[m.group(1)] = a.text()
        if not fresh:
            break
    pages = _open_details(row, http, [(u, hints.get(i, "")) for i, u in found.items()], DETAIL_CAP)
    jobs: list[Job] = []
    for jid, u in found.items():
        try:
            if u not in pages:
                continue
            tree = _tree(pages[u])
            p = _ld_posting(tree)
            title = _text(p.get("title")) if p else ""
            if not p or not title:
                continue
            org = p.get("hiringOrganization")
            company = _text(org.get("name")) if isinstance(org, dict) else ""
            info = next((x for x in _walk(tree) if x.has_class("jobinfo")), None)
            chips = info.text().lower() if info is not None else ""
            place = _ld_places(p, region=False)
            remote = True if _GJ_REMOTE in chips else (False if _GJ_OFFICE in chips else _remote_from(place))
            j = _job(row, "geekjob", title=title, url=u, location=place, posted=_iso_date(p.get("datePosted")),
                     description=_body(html.unescape(str(p.get("description") or ""))), remote=remote, extra={"job_id": jid})
            j["company"] = company or j["company"]  # the employer named in the ad, never the board
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- ApplicantPro
# Borrowed from freehire internal/ingest/sources/isolvedhire.go:14-21 and :39-42 (the iSolved Hire and ApplicantPro career sites are one
# platform under two host domains; the board is the tenant subdomain, host <board>.applicantpro.com), :87 (postings are enumerated
# from https://<host>/sitemap.xml), :46-49 and :125-130 (the /jobs/<id> <loc> entries, id = digits, /jobs/<id> and /jobs/<id>.html
# de-duplicated), :51-84 and :98-106 (a sitemap whose first 512 bytes hold "typed the url for this website incorrectly" or "career
# site has been disabled" is a board the platform no longer serves), :144-173 (each posting is read from https://<host>/jobs/<id>
# schema.org JobPosting ld+json: title, description, datePosted "2006-01-02 15:04:05" (date part used), hiringOrganization.name,
# jobLocation.address; company = the configured one first, :168). kit_ats_us.py reads the isolvedhire.com host the same way.
# NOT IN REFERENCE: the sitemap carries no titles, so only the first ID_ONLY_CAP postings get their detail request.
_AP_LOC = re.compile(r"<loc>([^<]*)</loc>", re.I)
_AP_ID = re.compile(r"/jobs/(\d+)")
_AP_GONE = ("typed the url for this website incorrectly", "career site has been disabled")


def _applicantpro(row: dict[str, Any], http: Http) -> list[Job] | None:
    """ApplicantPro career site (<board>.applicantpro.com): ids from sitemap.xml, schema.org JobPosting per posting."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board):
        return None
    host = f"{board}.applicantpro.com"
    _st, sm = _page_st(http, f"https://{host}/sitemap.xml", max_bytes=_SITEMAP_MAX)
    if sm is None or any(n in sm[:512].lower() for n in _AP_GONE):
        return None
    ids: list[str] = []
    for loc in _AP_LOC.findall(sm):
        m = _AP_ID.search(_path(html.unescape(loc.strip())))
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    jobs: list[Job] = []
    for jid in ids[:ID_ONLY_CAP]:
        url = f"https://{host}/jobs/{jid}"
        st, page = _page_st(http, url, once=True)
        if page is None:
            if _refused(st):
                break
            continue
        try:
            ld = _ld_posting(_tree(page))
            title = _text(ld.get("title")) if ld else ""
            if not ld or not title:
                continue
            org = ld.get("hiringOrganization")
            place = _ld_places(ld, region=True)
            j = _job(row, "applicantpro", title=title, url=url, location=place, posted=_iso_date(ld.get("datePosted")),
                     description=_body(html.unescape(str(ld.get("description") or ""))), remote=_remote_from(place),
                     extra={"job_id": jid})
            if not str(row.get("name") or "").strip() and isinstance(org, dict):
                j["company"] = _text(org.get("name")) or j["company"]
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Potok
# Borrowed from freehire internal/ingest/sources/potok.go:27-30 (a Russian ATS; the tenant's whole open set is one server-rendered
# page https://<board>.potok.io/open/jobs/, no pagination), :31 (a posting id is the number at the end of /open/jobs/<id>), :105-111
# (a 200 without the div.postings-wrapper is not the career page: the board fails rather than reading as empty), :113-130 (rows are
# div.posting inside div.postings-group; the group's .posting-category-title is its team), :134-150 (a row: a.posting-title with the
# id in its href, the title in its h5 and the city in .sort-by-location; a row without id or title is skipped), :154-172 (the posting
# page https://<board>.potok.io/open/jobs/<id> holds the body in .plain-list), :180-189 (the company is the configured one), :195-205
# (the posting headline's second .sort-by-time category is the schedule; "Удаленная работа" means remote), :144 (neither page states a
# date). Opened only for a title that passes title_ok (cap 25); the others keep their listing fields and no description.
_PK_ID = re.compile(r"/open/jobs/(\d+)$")
_PK_REMOTE = "удаленная работа"


def _potok_rows(node: _Node, out: list[tuple[str, str, str]]) -> None:
    """Collect (id, title, location) of every div.posting under ``node``; linear in the page, depth bounded by the tree cap."""
    for k in node.kids:
        if not isinstance(k, _Node):
            continue
        if k.has_class("posting"):
            link = next((a for a in _walk(k) if a.tag == "a" and a.has_class("posting-title")), None)
            m = _PK_ID.search(_path(link.attrs.get("href", ""))) if link is not None else None
            h5 = next((h for h in _walk(link) if h.tag == "h5"), None) if link is not None else None
            if m and h5 is not None and h5.text():
                loc = next((x for x in _walk(link) if x.has_class("sort-by-location")), None) if link is not None else None
                out.append((m.group(1), h5.text(), loc.text() if loc is not None else ""))
            continue
        _potok_rows(k, out)


def _potok(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Potok career page (<tenant>.potok.io/open/jobs/): one listing page, one posting page per wanted title."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board):
        return None
    host = f"{board}.potok.io"
    _st, listing = _page_st(http, f"https://{host}/open/jobs/", once=True)
    if listing is None:
        return None
    wrapper = next((n for n in _walk(_tree(listing)) if n.has_class("postings-wrapper")), None)
    if wrapper is None:
        return None
    rows: list[tuple[str, str, str]] = []
    _potok_rows(wrapper, rows)
    seen: set[str] = set()
    uniq: list[tuple[str, str, str]] = []
    for r in rows:
        if r[0] not in seen:
            seen.add(r[0])
            uniq.append(r)
    rows = uniq
    urls = {r[0]: f"https://{host}/open/jobs/{r[0]}" for r in rows}
    pages = _open_details(row, http, [(urls[r[0]], r[1]) for r in rows], DETAIL_CAP)
    jobs: list[Job] = []
    for jid, title, place in rows:
        try:
            desc, remote = "", _remote_from(place)
            if urls[jid] in pages:
                tree = _tree(pages[urls[jid]])
                body = next((n for n in _walk(tree) if n.has_class("plain-list")), None)
                desc = _body(body.inner_html()) if body is not None else ""
                head = next((n for n in _walk(tree) if n.has_class("posting-headline")), None)
                times = [n.text() for n in _walk(head) if n.has_class("posting-category") and n.has_class("sort-by-time")] if head is not None else []
                if len(times) > 1 and times[1].lower() == _PK_REMOTE:
                    remote = True
            jobs.append(_job(row, "potok", title=title, url=urls[jid], location=place, description=desc, remote=remote,
                             extra={"job_id": jid}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Huntflow
# Borrowed from freehire internal/ingest/sources/huntflow.go:11-16 and :26-30 (a Russian ATS; <board>.huntflow.io is a Nuxt app whose
# data is a devalue-encoded _payload.json), :41-49 (list item: id, slug, position, archived_at), :60-75 (list = data.vacancies.items of
# https://<board>.huntflow.io/_payload.json; a non-null archived_at is a closed vacancy and is skipped), :100-143 (detail =
# https://<board>.huntflow.io/vacancy/<slug>/_payload.json; the vacancy is the map that holds both ``body`` and ``position``; city,
# money, intro, body, requirements, conditions; description = money in a <p>, then intro+body+requirements+conditions; url
# https://<board>.huntflow.io/vacancy/<slug>; no publish date), :194-247 (devalue: a flat array, an object's field values and an array's
# elements are indices into it, a string-headed array is a type wrapper resolving to its second element), :250-265 (findVacancy).
# huntflow_test.go:25-30 shows the list item also carries city. NOT IN REFERENCE: the cycle and depth guard in _unflatten (the
# reference recurses without one) and the visited set in _find_vacancy; a hostile payload costs only its own posting.
_HF_DEPTH = 64


def _unflatten(raw: list[Any]) -> Any:
    """Resolve a devalue array (Nuxt _payload.json) into plain dict/list/scalar values; a cycle or a too-deep chain is None."""
    memo: dict[int, Any] = {}
    active: set[int] = set()

    def ref(v: Any, depth: int) -> Any:
        if isinstance(v, int) and not isinstance(v, bool):
            return node(v, depth + 1)
        return v

    def node(i: int, depth: int) -> Any:
        if i < 0 or i >= len(raw) or depth > _HF_DEPTH or i in active:
            return None
        if i in memo:
            return memo[i]
        active.add(i)
        tok = raw[i]
        if isinstance(tok, dict):
            out: Any = {str(k): ref(v, depth) for k, v in tok.items()}
        elif isinstance(tok, list):
            if tok and isinstance(tok[0], str):  # a type wrapper (ShallowReactive ...) resolves to the wrapped value
                out = ref(tok[1], depth) if len(tok) > 1 else None
            else:
                out = [ref(v, depth) for v in tok]
        else:
            out = tok
        active.discard(i)
        memo[i] = out
        return out

    return node(0, 0) if raw else None


def _find_vacancy(root: Any) -> dict[str, Any] | None:
    """The first map holding both ``body`` and ``position``, found without revisiting a shared node."""
    stack, seen = [root], set()
    while stack:
        n = stack.pop()
        if isinstance(n, (dict, list)):
            if id(n) in seen:
                continue
            seen.add(id(n))
        if isinstance(n, dict):
            if "body" in n and "position" in n:
                return n
            stack.extend(reversed(list(n.values())))
        elif isinstance(n, list):
            stack.extend(reversed(n))
    return None


def _fields(vac: dict[str, Any], keys: tuple[str, ...]) -> dict[str, str]:
    """The string fields of a vacancy map ('' for a missing or non-string one)."""
    return {k: vac[k] if isinstance(vac.get(k), str) else "" for k in keys}


def _payload(http: Http, url: str, *, once: bool = False) -> tuple[int, Any]:
    st, raw = _get(http, url, as_json=True, **({"once": True} if once else {}))
    return st, (_unflatten(raw) if st == 200 and isinstance(raw, list) else None)


def _huntflow(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Huntflow career site (<tenant>.huntflow.io): _payload.json list + one detail payload per wanted title."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board):
        return None
    host = f"{board}.huntflow.io"
    _st, tree = _payload(http, f"https://{host}/_payload.json")
    items = ((tree or {}).get("data") or {}).get("vacancies", {}) if isinstance(tree, dict) else {}
    items = items.get("items") if isinstance(items, dict) else None
    if not isinstance(items, list):
        return None
    vacancies: list[tuple[str, str, str, str]] = []  # (id, slug, title, city)
    seen: set[str] = set()
    for it in items:
        if not isinstance(it, dict) or it.get("archived_at") is not None:
            continue
        slug, title, jid = str(it.get("slug") or ""), _text(it.get("position")), str(it.get("id") or "")
        if not _SLUG.fullmatch(slug) or not title or not jid or jid in seen:
            continue
        seen.add(jid)
        city = it.get("city")
        vacancies.append((jid, slug, title, _text(city) if isinstance(city, str) else ""))
    jobs: list[Job] = []
    opened = 0
    stopped = False
    for jid, slug, title, city in vacancies:
        place, desc = city, ""
        if not stopped and opened < DETAIL_CAP and _wanted(row, title):
            opened += 1
            st, tree = _payload(http, f"https://{host}/vacancy/{slug}/_payload.json", once=True)
            if tree is None:
                stopped = st != 200 and _refused(st)  # a 200 that is not a payload is one bad posting
            else:
                try:
                    vac = _find_vacancy(tree)
                    if vac is not None:
                        f = _fields(vac, ("city", "money", "intro", "body", "requirements", "conditions"))
                        place = _text(f["city"]) or city
                        money = f"<p>{html.escape(f['money'], quote=False)}</p>" if f["money"] else ""
                        desc = _body(money + f["intro"] + f["body"] + f["requirements"] + f["conditions"])
                except Exception:  # noqa: BLE001 - one bad detail payload keeps the listing fields
                    pass
        try:
            jobs.append(_job(row, "huntflow", title=title, url=f"https://{host}/vacancy/{slug}", location=place,
                             description=desc, remote=_remote_from(place), extra={"job_id": jid}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "geekjob": _wrap(_geekjob),
    "applicantpro": _wrap(_applicantpro),
    "potok": _wrap(_potok),
    "huntflow": _wrap(_huntflow),
}
