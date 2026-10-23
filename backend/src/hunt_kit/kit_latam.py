"""Latin-American ATS board fetchers for the hunt kit: Gupy, HiringRoom, Recrutei, InHire, Solides and Vagas.com.br. Same
contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http``
callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  gupy        the career-page SUBDOMAIN of <subdomain>.gupy.io (not the numeric companyId)
  hiringroom  tenant label of <tenant>.hiringroom.com
  recrutei    tenant path segment of jobs.recrutei.com.br/<tenant>/
  inhire      tenant slug of <tenant>.inhire.app
  solides     tenant slug of <slug>.vagas.solides.com.br
  vagas       any word (``vagas:all``): vagas.com.br is ONE national board, the three tech area listings are read

Endpoints, params and field names are adapted from the MIT-licensed freehire and career-ops source adapters (see NOTICE.md);
the code is original. The container this was written in could not reach any of these hosts, so every parser is tested on
fixtures shaped like the references' test data only. Posting pages / detail calls are made only for cards whose title passes
the run's ``title_ok`` test, up to 25 per board, and the first refusal ends the board's detail requests. Gupy and InHire
list without a body, so a posting that was not opened is listed with no description; Vagas.com.br has no title on its
listing, so only a posting that was opened is listed (as Jobylon does).

NOT built: career-ops gupy.mjs is a keyword SEARCH of the whole Gupy portal (portal.gupy.io), not a per-employer board; the
kit reads the per-subdomain board of freehire gupy.go only. HiringRoom's schema.org layout (hiringroom.mjs:132-186) is not
read: the freehire pager (hiringroom.go:84-121) lists both layouts completely, the career-ops page only the first 20.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    MAX_PAGES,
    Http,
    Job,
    _body,
    _get,
    _iso_date,
    _job,
    _join,
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
    _words,
    _wrap,
)

_JSON = {"Accept": "application/json"}
_POST = {"Content-Type": "application/json", "Accept": "application/json"}
_FORM = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}

DETAIL_CAP = 25  # detail requests per board (the references fan out over every posting)
HIRINGROOM_PAGES = 60  # 20 cards a page; hiringroom.go:36 allows 200, the largest portal measured holds 13 pages
SOLIDES_PAGES = 40  # 25 postings a page; solides.go caps at 1000 pages
VAGAS_PAGES = MAX_PAGES  # listing pages per area; vagas.go:43 allows 60


def _text(value: Any) -> str:
    """Entity-decoded text on one line (APIs leave entities in plain-text fields)."""
    return " ".join(html.unescape(str(value or "")).replace("�", "").split())


def _pos_id(value: Any) -> int:
    """A positive integer id, else 0 (bools and strings are not ids)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _mode(value: Any) -> str:
    """gupy.go/inhire.go workplaceTypeMode: remote | hybrid | on-site (or onsite/office) -> remote | hybrid | onsite, else ''."""
    return {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite", "on site": "onsite",
            "office": "onsite"}.get(str(value or "").strip().lower(), "")


def _json_details(row: dict[str, Any], http: Http, items: list[tuple[str, str]], cap: int,
                  headers: dict[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """JSON twin of ``_open_details``: (url, title-to-test) items, only titles passing ``title_ok``, at most ``cap``
    requests, each sent once; a 404/410 skips that posting, any other failure ends the run. Returns url -> object."""
    out: dict[str, dict[str, Any]] = {}
    tried = 0
    for url, test in items:
        if tried >= cap:
            break
        if not _title_ok(row, test):
            continue
        tried += 1
        st, data = _get(http, url, headers=headers or _JSON, once=True)
        if not isinstance(data, dict):
            if st in (404, 410):
                continue
            break
        out[url] = data
    return out


def _outer(root: _Node, pred: Callable[[_Node], bool]) -> list[tuple[_Node, _Node | None]]:
    """(node, previous element sibling) for every node matching ``pred`` in document order, not looking inside a match
    (cards do not nest, and unclosed ones in a hostile page would otherwise make each card re-walk everything below it)."""
    out: list[tuple[_Node, _Node | None]] = []
    stack = [iter([root])]
    prev: list[_Node | None] = [None]
    while stack:
        for k in stack[-1]:
            if isinstance(k, _Node):
                if k is not root and pred(k):
                    out.append((k, prev[-1]))
                    prev[-1] = k
                else:
                    prev[-1] = k
                    stack.append(iter(k.kids))
                    prev.append(None)
                    break
        else:
            stack.pop()
            prev.pop()
    return out


# --------------------------------------------------------------------------- Gupy
# Borrowed from freehire internal/ingest/sources/gupy.go:26 and :60 (GET https://private-api.gupy.io/job-publication/public/jobs
# ?subdomain=<board>: one unpaginated answer carries every published posting), :12-25 (the board is the career-page SUBDOMAIN, not
# the numeric companyId), :63-69 (id, name, addressCity, addressStateShortName, addressCountry, remoteWorking, workplaceType), :71-77
# and :83 (detail: /<id> with description, responsibilities, prerequisites, relevantExperiences, publishedAt), :85 (the posting page is
# https://<board>.gupy.io/jobs/<id>), :88-97 (a 404 is Gupy's own "posting gone"), :114-123 (sections joined in board order; a section
# with no letter or digit, e.g. "<p>.</p>", is empty), and gupy_test.go (country alone when city and state are blank, hybrid/remote
# from workplaceType). The listing has no body; a failed or unopened detail keeps the listing row.
def _gupy_sections(d: dict[str, Any]) -> str:
    parts = [_body(d.get(k)) for k in ("description", "responsibilities", "prerequisites", "relevantExperiences")]
    return "\n\n".join(p for p in parts if re.search(r"[^\W_]", p))


def _gupy(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Gupy career page (private-api.gupy.io listing by subdomain, detail JSON for wanted postings)."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board):
        return None
    st, data = _get(http, f"https://private-api.gupy.io/job-publication/public/jobs?subdomain={quote(board)}", headers=_JSON)
    rows = data.get("data") if isinstance(data, dict) else None
    if st != 200 or not isinstance(rows, list):
        return None  # an unknown subdomain is a 404: a failed board, never an empty one
    cards: dict[int, dict[str, Any]] = {}
    for p in rows:
        pid = _pos_id(p.get("id")) if isinstance(p, dict) else 0
        if pid and pid not in cards and _text(p.get("name")):
            cards[pid] = p
    url_of = {pid: f"https://{board}.gupy.io/jobs/{pid}" for pid in cards}
    detail_url = {pid: f"https://private-api.gupy.io/job-publication/public/jobs/{pid}" for pid in cards}
    got = _json_details(row, http, [(detail_url[pid], _text(p.get("name"))) for pid, p in cards.items()], DETAIL_CAP)
    jobs: list[Job] = []
    for pid, p in cards.items():
        try:
            d = got.get(detail_url[pid]) or {}
            mode = _mode(p.get("workplaceType"))
            place = _join(_text(p.get("addressCity")), _text(p.get("addressStateShortName")), _text(p.get("addressCountry")))
            jobs.append(_job(row, "gupy", title=_text(p.get("name")), url=url_of[pid], location=place,
                             posted=_iso_date(d.get("publishedAt")), description=_gupy_sections(d),
                             remote=True if p.get("remoteWorking") is True or mode == "remote" else None,
                             extra={"job_id": str(pid), "work_mode": mode}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- HiringRoom
# Borrowed from freehire internal/ingest/sources/hiringroom.go:64 (board = tenant label), :24-26 (the portal page renders only its
# first 20 vacancies; the pager does the rest), :90-91 (POST https://<board>.hiringroom.com/jobs/getVacanciesForPortal/<page> with
# the form selectedPage=<page>&typePortal=external), :46-52 and :95-98 (answer {result:"success", data:{htmlContent, total_vacancies}};
# no total is a refusal), :104-122 (distinct ids; stop at the stated total; a page that adds nothing ends the walk), :123 and
# :129-170 (cards are <a href="/jobs/get_vacancy/<24 hex>"> with h4.name__vacancy and icon-tagged facts hr-Location-pin, hr-Work-area,
# hr-Clock, hr-Company/hr-Remote, hr-Campus; the first copy of a repeated tag is read), :201-232 (vacancy page: each
# div.job-description-content under its preceding h6 heading), :238-280 (schedule, modality and level picklists), and career-ops
# providers/hiringroom.mjs:79-111 (the vacancy path /jobs/get_vacancy/<hex>, the tenant host only). The cards carry no date.
_HIRINGROOM_PATH = re.compile(r"^/jobs/get_vacancy/([0-9a-f]{24})$")
_HIRINGROOM_ICONS = (("hr-Location-pin", "location"), ("hr-Work-area", "area"), ("hr-Clock", "schedule"),
                     ("hr-Company", "modality"), ("hr-Remote", "modality"), ("hr-Campus", "level"))
_HIRINGROOM_SCHEDULE = {"Full-time": "full_time", "Part time": "part_time", "Freelance": "contract", "Eventual": "contract",
                        "Pasantía": "internship"}


def _hiringroom_cards(fragment: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for a, _prev in _outer(_tree(fragment), lambda n: n.tag == "a" and bool(_HIRINGROOM_PATH.match(n.attrs.get("href", "")))):
        try:
            m = _HIRINGROOM_PATH.match(a.attrs.get("href", ""))
            title_node = next((n for n in _walk(a) if n.has_class("name__vacancy")), None)
            if m is None or title_node is None or not title_node.text():
                continue
            card = {"id": m.group(1), "title": title_node.text(), "location": "", "area": "", "schedule": "", "modality": "", "level": ""}
            for parent in (a, *_walk(a)):
                for k in parent.kids:
                    if isinstance(k, _Node) and k.tag == "i":
                        for icon, field in _HIRINGROOM_ICONS:
                            if k.has_class(icon) and not card[field]:
                                card[field] = parent.text()
            out.append(card)
        except Exception:  # noqa: BLE001, S112 - one bad card must not lose the page
            continue
    return out


def _hiringroom_body(page: str) -> str:
    """The vacancy page's sections ("heading\\nbody", blank line between), '' when it has none."""
    parts: list[str] = []
    for div, prev in _outer(_tree(page), lambda n: n.tag == "div" and n.has_class("job-description-content")):
        body = _body(div.inner_html())
        if not body:
            continue
        head = prev.text() if prev is not None and prev.tag == "h6" else ""
        parts.append(f"{head}\n{body}" if head else body)
    return "\n\n".join(parts)


def _hiringroom(row: dict[str, Any], http: Http) -> list[Job] | None:
    """HiringRoom portal (getVacanciesForPortal pager, vacancy page for wanted cards)."""
    board = str(row.get("token") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", board):
        return None
    host = f"{board}.hiringroom.com"
    cards: dict[str, dict[str, str]] = {}
    for page in range(1, HIRINGROOM_PAGES + 1):
        body = urlencode({"selectedPage": page, "typePortal": "external"}).encode()
        st, data = _get(http, f"https://{host}/jobs/getVacanciesForPortal/{page}", method="POST", data=body, headers=_FORM)
        inner = data.get("data") if isinstance(data, dict) else None
        total = inner.get("total_vacancies") if isinstance(inner, dict) else None
        if st != 200 or not isinstance(inner, dict) or data.get("result") != "success" \
                or not isinstance(total, int) or isinstance(total, bool) or not isinstance(inner.get("htmlContent"), str):
            if page > 1:
                break  # a hiccup after page 1 keeps what we have
            return None
        fresh = 0
        for c in _hiringroom_cards(inner["htmlContent"]):
            if c["id"] not in cards:
                cards[c["id"]] = c
                fresh += 1
        if not fresh or len(cards) >= total:
            break
    url_of = {cid: f"https://{host}/jobs/get_vacancy/{cid}" for cid in cards}
    pages = _open_details(row, http, [(url_of[cid], c["title"]) for cid, c in cards.items()], DETAIL_CAP)
    jobs: list[Job] = []
    for cid, c in cards.items():
        try:
            desc = _hiringroom_body(pages[url_of[cid]]) if url_of[cid] in pages else ""
            jobs.append(_job(row, "hiringroom", title=c["title"], url=url_of[cid], location=c["location"], description=desc,
                             remote=True if c["modality"] == "Remoto" else None,
                             extra={"job_id": cid, "area": c["area"], "level": c["level"], "modality": c["modality"],
                                    "employment_type": _HIRINGROOM_SCHEDULE.get(c["schedule"], "")}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Recrutei
# Borrowed from freehire internal/ingest/sources/recrutei.go:112 (POST https://api.recrutei.com.br/api/v2/vacancies/per-departments/
# <board> with the JSON body {"search":""}: every open posting grouped by department), :55-63 (envelope data.total and
# data.vacancies[].items), :65-78 (id, title, regime, company_name, location, public_link), :80-104 (location is a list of strings or
# the bare placeholder "Não informado", which is no location), :108-125 (the declared total must equal the summed item count, else the
# board failed), :139-165 (the posting page's schema.org JobPosting gives description and datePosted, written dd/mm/yyyy hh:mm:ss;
# its location and employmentType are unreliable and not used), :168-182 (CLT = full time, Pessoa Jurídica = contract, else unstated)
# and recrutei_test.go (public links are on jobs.recrutei.com.br/<board>/vacancy/<id>-<slug>).
_RECRUTEI_HOST = "jobs.recrutei.com.br"
_RECRUTEI_REGIME = {"CLT": "full_time", "Pessoa Jurídica": "contract"}


def _recrutei_date(value: Any) -> str:
    try:
        return datetime.strptime(str(value or "").strip(), "%d/%m/%Y %H:%M:%S").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _recrutei(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Recrutei tenant (one POST lists every posting, ld+json posting pages for wanted ones)."""
    board = str(row.get("token") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", board):
        return None
    st, data = _get(http, f"https://api.recrutei.com.br/api/v2/vacancies/per-departments/{quote(board)}", method="POST",
                    data=b'{"search":""}', headers=_POST)
    inner = data.get("data") if isinstance(data, dict) else None
    depts = inner.get("vacancies") if isinstance(inner, dict) else None
    if st != 200 or not isinstance(inner, dict) or not isinstance(depts, list) or not isinstance(inner.get("total"), int):
        return None
    items: list[Any] = []
    for dept in depts:
        got = dept.get("items") if isinstance(dept, dict) else None
        items.extend(got if isinstance(got, list) else [])
    if len(items) != inner["total"]:
        return None  # the declared total is the only completeness signal this endpoint gives
    cards: dict[int, dict[str, Any]] = {}
    for it in items:
        iid = _pos_id(it.get("id")) if isinstance(it, dict) else 0
        link = str(it.get("public_link") or "").strip() if isinstance(it, dict) else ""
        try:
            u = urlsplit(link)
            ok = u.scheme == "https" and (u.hostname or "").lower() == _RECRUTEI_HOST
        except ValueError:
            ok = False
        if iid and iid not in cards and ok and _text(it.get("title")):
            cards[iid] = {**it, "public_link": u._replace(fragment="").geturl()}
    pages = _open_details(row, http, [(c["public_link"], _text(c.get("title"))) for c in cards.values()], DETAIL_CAP)
    jobs: list[Job] = []
    for iid, c in cards.items():
        try:
            ld = _ld_posting(_tree(pages[c["public_link"]])) if c["public_link"] in pages else None
            loc = c.get("location")
            parts = [str(x) for x in loc] if isinstance(loc, list) else [str(loc or "")]
            place = _join(*[_text(x) for x in parts if _text(x) != "Não informado"])
            j = _job(row, "recrutei", title=_text(c.get("title")), url=c["public_link"], location=place,
                     posted=_recrutei_date(ld.get("datePosted")) if ld else "",
                     description=_body(html.unescape(str(ld.get("description") or ""))) if ld else "",
                     remote=_remote_from(c.get("title"), place),
                     extra={"job_id": str(iid), "employment_type": _RECRUTEI_REGIME.get(str(c.get("regime") or "").strip(), "")})
            if _text(c.get("company_name")):
                j["company"] = _text(c.get("company_name"))
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- InHire
# Borrowed from freehire internal/ingest/sources/inhire.go:22-23 (GET https://api.inhire.app/job-posts/public/pages and
# .../pages/<jobId>), :68-70 (the board is the tenant slug, sent as the X-Tenant header on every call), :40-46 and :72-81 (list answer
# {jobsPage:[{jobId, displayName, workplaceType, location}]}, no pagination, no body), :85-110 (detail: description with HTML
# entities left in it, publishedAt else createdAt), :24-28 and :56-65 (the public page is https://<board>.inhire.app/vagas/<jobId>/
# <slug>; the SPA renders blank without a slug segment, so a title slug, "vaga" when empty, is added) and inhire_test.go:88-90
# ("Consultor(a) Sênior" -> consultor-a-senior).
def _inhire_slug(title: str) -> str:
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80].strip("-") or "vaga"


def _inhire(row: dict[str, Any], http: Http) -> list[Job] | None:
    """InHire tenant (X-Tenant listing, detail JSON for wanted postings)."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board):
        return None
    hdr = {**_JSON, "X-Tenant": board}
    st, data = _get(http, "https://api.inhire.app/job-posts/public/pages", headers=hdr)
    rows = data.get("jobsPage") if isinstance(data, dict) else None
    if st != 200 or not isinstance(rows, list):
        return None
    cards: dict[str, dict[str, Any]] = {}
    for p in rows:
        jid = str(p.get("jobId") or "") if isinstance(p, dict) else ""
        if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", jid) and jid not in cards and _text(p.get("displayName")):
            cards[jid] = p
    detail_url = {jid: f"https://api.inhire.app/job-posts/public/pages/{jid}" for jid in cards}
    got = _json_details(row, http, [(detail_url[jid], _text(p.get("displayName"))) for jid, p in cards.items()], DETAIL_CAP, hdr)
    jobs: list[Job] = []
    for jid, p in cards.items():
        try:
            d = got.get(detail_url[jid]) or {}
            title, mode = _text(p.get("displayName")), _mode(p.get("workplaceType"))
            jobs.append(_job(row, "inhire", title=title, url=f"https://{board}.inhire.app/vagas/{jid}/{_inhire_slug(title)}",
                             location=_text(p.get("location")), posted=_iso_date(d.get("publishedAt") or d.get("createdAt")),
                             description=_body(html.unescape(str(d.get("description") or ""))),
                             remote=True if mode == "remote" else None, extra={"job_id": jid, "work_mode": mode}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Solides
# Borrowed from freehire internal/ingest/sources/solides.go:13 and :93 (GET https://apigw.solides.com.br/jobs/v3/home/vacancy?slug=
# <board>&take=25&page=<n>; the board is the tenant slug), :17 (the server caps take at 25), :25 and :60-64 (currentState
# "em_andamento" is open; another present value, e.g. "encerrada", is closed and skipped), :42-57 (id, title, description HTML inline,
# city.name, state.code, homeOffice, jobType, createdAt), :73 (the page is https://<board>.vagas.solides.com.br/vaga/<id>), :90-112
# (the walk ends at data.totalPages; a failed first page fails the board, a later one keeps the earlier pages), :116-128 (jobType
# remoto / hibrido / presencial) and solides_test.go:19-37. The description is inline, so no detail request is made.
def _solides(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Solides tenant (paged JSON list with the description inline)."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board):
        return None
    cards: dict[int, dict[str, Any]] = {}
    for page in range(1, SOLIDES_PAGES + 1):
        st, data = _get(http, "https://apigw.solides.com.br/jobs/v3/home/vacancy?" + urlencode({"slug": board, "take": 25, "page": page}), headers=_JSON)
        inner = data.get("data") if isinstance(data, dict) else None
        batch = inner.get("data") if isinstance(inner, dict) else None
        if st != 200 or not isinstance(batch, list):
            if page > 1:
                break
            return None
        for p in batch:
            pid = _pos_id(p.get("id")) if isinstance(p, dict) else 0
            if pid and pid not in cards:
                cards[pid] = p
        pages = inner.get("totalPages") if isinstance(inner, dict) else 0
        if not isinstance(pages, int) or page >= pages:
            break
    jobs: list[Job] = []
    for pid, p in cards.items():
        try:
            state = str(p.get("currentState") or "")
            if (state and state != "em_andamento") or not _text(p.get("title")):
                continue
            city, st_ = p.get("city"), p.get("state")
            kind = str(p.get("jobType") or "").strip().lower()
            jobs.append(_job(row, "solides", title=_text(p.get("title")), url=f"https://{board}.vagas.solides.com.br/vaga/{pid}",
                             location=_join(_text(city.get("name")) if isinstance(city, dict) else "",
                                            _text(st_.get("code")) if isinstance(st_, dict) else ""),
                             posted=_iso_date(p.get("createdAt")), description=_body(html.unescape(str(p.get("description") or ""))),
                             remote=True if p.get("homeOffice") is True or kind == "remoto" else None,
                             extra={"job_id": str(pid), "work_mode": {"remoto": "remote", "hibrido": "hybrid", "presencial": "onsite"}.get(kind, "")}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Vagas.com.br
# Borrowed from freehire internal/ingest/sources/vagas.go:36-40 (no per-employer board: the listings /vagas-de-tecnologia,
# /vagas-de-programador and /vagas-de-desenvolvedor of https://www.vagas.com.br are read), :43 and :98-103 (paged by ?pagina=N,
# a page that adds no new link ends the area), :147-153 (a job link is /vagas/v<id>/<slug>; the id is the dedup key across areas),
# :78-95 (the first area failing fails the board, a later one keeps what was gathered), :108-131 (the job page's schema.org JobPosting
# gives title, hiringOrganization.name, jobLocation and datePosted; the company is the one named in the ad), :134-144 (the
# div.job-description__text block is the body, the flattened ld+json description only a fallback). The listing has no title, so only
# a posting that was opened (slug passes title_ok, at most 25) is listed. Token ignored.
_VAGAS_AREAS = ("vagas-de-tecnologia", "vagas-de-programador", "vagas-de-desenvolvedor")
_VAGAS_ID = re.compile(r"^/vagas/v(\d+)")


def _vagas(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Vagas.com.br tech area listings + one ld+json job page per wanted posting (token ignored)."""
    found: dict[str, str] = {}
    stopped = False
    for i, area in enumerate(_VAGAS_AREAS):
        for page in range(1, VAGAS_PAGES + 1):
            if stopped:
                break
            st, listing = _page_st(http, f"https://www.vagas.com.br/{area}?pagina={page}")
            if listing is None:
                if i == 0 and page == 1:
                    return None
                stopped = st in (403, 429)  # a refusal ends the listing; any other miss ends this area
                break
            fresh = 0
            for u in _links(_tree(listing), "https://www.vagas.com.br/", "www.vagas.com.br", lambda h: bool(_VAGAS_ID.search(urlsplit(h).path))):
                m = _VAGAS_ID.search(urlsplit(u).path)
                if m and m.group(1) not in found:
                    found[m.group(1)] = u
                    fresh += 1
            if not fresh:
                break
    items = [(u, _words(u.rstrip("/").rsplit("/", 1)[-1]) if urlsplit(u).path.count("/") > 2 else "") for u in found.values()]
    pages = _open_details(row, http, items, DETAIL_CAP)
    jobs: list[Job] = []
    for jid, u in found.items():
        try:
            if u not in pages:
                continue
            tree = _tree(pages[u])
            p = _ld_posting(tree)
            org = p.get("hiringOrganization") if p else None
            title, company = _text(p.get("title")) if p else "", _text(org.get("name")) if isinstance(org, dict) else ""
            if not title or not company:
                continue
            block = next((n for n in _walk(tree) if n.has_class("job-description__text")), None)
            place = _ld_places(p, region=True) if p else ""
            j = _job(row, "vagas", title=title, url=u, location=place, posted=_iso_date(p.get("datePosted")) if p else "",
                     description=_body(block.inner_html()) if block is not None else _body(html.unescape(str(p.get("description") or ""))),
                     remote=_remote_from(place), extra={"job_id": jid})
            j["company"] = company  # the employer named in the ad, never the board
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "gupy": _wrap(_gupy),
    "hiringroom": _wrap(_hiringroom),
    "inhire": _wrap(_inhire),
    "recrutei": _wrap(_recrutei),
    "solides": _wrap(_solides),
    "vagas": _wrap(_vagas),
}
