"""Which browser a task uses - pure: stdlib plus ``fetch.guard``'s host helpers (owner, 2026-10-25 and 10-26).

Every page on linkedin.com (job search, Easy Apply, messages, connection notes) runs ONLY in the user's OWN
browser (the one their assistant drives on their computer), by the user's choice (owner, 2026-10-26):
Job360's cloud browser never opens it, and the task waits while that computer is off (:func:`is_linkedin`
is the one host test). Everything else goes to Job360's own cloud browser FIRST, and moves to the user's
own browser only when Job360's browser actually failed on it: a sign-in wall, a CAPTCHA or bot check, or a
safety stop. ``blocked`` events written with ``by: browser`` are the log of
that failure. Passwords, one-time codes (MFA) and a CAPTCHA the user must solve are the user's alone
("human"): no browser types a password or solves a CAPTCHA. A CAPTCHA is always solved by the user: on
Needs you while Job360's browser holds the page (Finish it here), otherwise in their own browser.
:func:`strict` holds the rules no learning can change (S0-S9 of the route rule book); :func:`browser_for`
applies them first. Assistant-neutral on purpose: no name of any assistant or browser appears in any text here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

# Pure helpers only (stdlib + ipaddress; the module's aiohttp import is for its resolver type): one host test.
from src.services.fetch.guard import canonical_host, is_linkedin_host

# Owner decision (2026-10-26, PR D): Job360's server reads a public LinkedIn job page, signed out
# (services/browser_agent/linkedin_guest.py); the user's signed-in LinkedIn stays in their own browser.
LINKEDIN_GUEST_READS = True

BrowserKind = Literal["job360", "own_browser", "human"]

ROUTE_REASONS = (
    "default", "job360_login_required", "job360_bot_check", "job360_safety_stop", "password", "mfa", "only_you",
    "linkedin_own_browser",
    "learned_blocked", "learned_login", "probe", "at_cap", "linkedin_paused", "account_step", "linkedin_guest_blocked",
    "linkedin_guest_read", "link_unclear",
)

# "unnamed" is never stored by the route learner (its CHECK allows only the four named kinds): a link whose host
# cannot be named is not a site.
SITE_KINDS = ("linkedin", "job_board", "ats", "company")
UNNAMED = "unnamed"
LEARN_TASKS = ("read", "fill")
# The signed-in LinkedIn tasks: notes and messages, Easy Apply and form fill, search, profile reads.
QUEUE_KINDS = ("linkedin_outreach", "application", "linkedin_search", "profile_read")

# Job boards by explicit domain (a host matches when it IS one or ends with "." + one; never a substring).
JOB_BOARDS: dict[str, tuple[str, ...]] = {
    "indeed": (
        "indeed.com", "indeed.co.uk", "indeed.ca", "indeed.com.au", "indeed.de", "indeed.fr", "indeed.es",
        "indeed.it", "indeed.nl", "indeed.ie", "indeed.in", "indeed.co.in", "indeed.ae", "indeed.sg",
        "indeed.com.sg", "indeed.co.za", "indeed.com.br", "indeed.mx", "indeed.jp", "indeed.ch", "indeed.at",
        "indeed.be", "indeed.pl", "indeed.se", "indeed.pt", "indeed.com.hk", "indeed.co.nz",
    ),
    "glassdoor": (
        "glassdoor.com", "glassdoor.co.uk", "glassdoor.ca", "glassdoor.com.au", "glassdoor.de", "glassdoor.fr",
        "glassdoor.es", "glassdoor.ie", "glassdoor.co.in", "glassdoor.nl", "glassdoor.sg",
    ),
    "reed": ("reed.co.uk",),
    "totaljobs": ("totaljobs.com",),
    "cvlibrary": ("cv-library.co.uk",),
    "bayt": ("bayt.com",),
    "naukri": ("naukri.com",),
    "wellfound": ("wellfound.com",),
}
_ORACLE_HOST = "oraclecloud.com"
_ORACLE_PATH = "/hcmui/candidateexperience"
# A public LinkedIn job page: /jobs/view/<digits>, optionally behind a slug that ends in the id.
_LINKEDIN_JOB_READ = re.compile(r"^/jobs/view/(?:[^/]*-)?\d+/?$")

# What Job360's browser reported (a stop reason or a blocked reason) -> why the task leaves it.
JOB360_FAILURES: dict[str, str] = {
    "login_required": "job360_login_required", "login_needed": "job360_login_required",
    "captcha": "job360_bot_check", "bot_check": "job360_bot_check",
    "manual_only": "job360_safety_stop", "safety_block": "job360_safety_stop",
}
# What a page asks for that no browser may give: the user's alone.
HUMAN_STOPS: dict[str, str] = {"password": "password", "mfa": "mfa", "otp": "mfa", "otp_needed": "mfa"}

LABELS: dict[str, str] = {
    "job360": "I'll use Job360's browser",
    "own_browser": "Needs your own browser: turn your PC on",
    "human": "Only you can do this",
}
# The chip for linkedin.com: the user's own browser by their choice, not because anything failed.
LINKEDIN_LABEL = "Your own browser: runs when your PC is on"

# One chip per route reason (and the "waiting" chip). {site} {date} {until} {n} {cap} are filled from closed
# values by :func:`label`; the frontend mirrors this block in ``browser-labels.ts``.
ROUTE_LABELS: dict[str, str] = {
    "default": "I'll use Job360's browser",
    "linkedin_own_browser": "Your own browser: runs when your PC is on",
    "learned_blocked": "Job360's browser was blocked on {site} on {date}: your own browser until {until}",
    "learned_login": "{site} asks you to sign in: your own browser, or sign in once in Job360's browser",
    "probe": "Trying Job360's browser on {site} again",
    "at_cap": "Today's LinkedIn limit is used ({n} of {cap}): waits until tomorrow",
    "linkedin_paused": "LinkedIn showed a limit warning: LinkedIn tasks paused until {date}",
    "account_step": "This site wants a new account: your own browser, or sign in once in Job360's browser",
    "linkedin_guest_blocked": "LinkedIn blocked a signed-out read: LinkedIn job pages wait until {date}",
    "link_unclear": "This link looks broken or disguised: your own browser checks it",
    "waiting": "Waiting for your PC",
}
# Shown for a route Job360's server reads itself (reason "linkedin_guest_read").
SERVER_READ_LABEL = "Job360's server reads it, signed out"
_SITE_NAMES: dict[str, str] = {
    "linkedin": "LinkedIn", "indeed": "Indeed", "glassdoor": "Glassdoor", "reed": "Reed", "totaljobs": "Totaljobs",
    "cvlibrary": "CV-Library", "bayt": "Bayt", "naukri": "Naukri", "wellfound": "Wellfound",
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
    "smartrecruiters": "SmartRecruiters", "icims": "iCIMS", "teamtailor": "Teamtailor", "oracle_hcm": "Oracle",
}
_FMT_DEFAULTS = {"site": "this site", "date": "soon", "until": "soon", "n": "?", "cap": "?"}
_HOST_SAFE = re.compile(r"^[a-z0-9.-]{1,253}$")


def _web_url(url: str) -> str | None:
    """The link to screen: ``https://`` added to a scheme-less one; None when it has no web host (empty,
    ``mailto:``, ``tel:``); NUL when it cannot even be split (it then fails closed in the callers)."""
    text = (url or "").strip()
    if not text:
        return None
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return "\0"
    if scheme not in ("http", "https"):
        if scheme and "." not in scheme:
            return None  # mailto:, tel:, about: - no web host to open
        text = "https://" + text.removeprefix("//")  # "www.linkedin.com/x" or "host:443/x": scheme-less
        try:
            urlsplit(text)  # "foo.com[/x" splits bare but not once prefixed: fail closed, never raise
        except ValueError:
            return "\0"
    return text


def is_linkedin(url: str) -> bool:
    """True for a link on a LinkedIn domain (``guard.LINKEDIN_DOMAINS``) or any subdomain of one, compared on
    ``guard.canonical_host``: the exact host, never a substring. Fails closed: a non-empty web link whose host
    cannot be named (unparseable, userinfo, a host IDNA would remap like ``www.ⓛinkedin.com``) counts as
    LinkedIn, so it waits for the user's own browser. "" is no link (False); a link with no web host at all
    (``mailto:``) is not LinkedIn either. A link written without a scheme is read as ``https://``."""
    text = _web_url(url)
    if text is None:
        return False
    host = canonical_host(text)
    return host is None or is_linkedin_host(host)


def names_linkedin(url: str) -> bool:
    """True only when the link NAMES a LinkedIn host (``canonical_host`` + ``is_linkedin_host``). Unlike
    :func:`is_linkedin` it does not fail closed: a link whose host cannot be named is False. Use it to COUNT
    LinkedIn work (the Easy Apply cap); use :func:`is_linkedin` as a safety test."""
    text = _web_url(url)
    host = canonical_host(text) if text else None
    return host is not None and is_linkedin_host(host)


def _matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def site_of(url: str) -> tuple[str, str, str]:
    """``(site_kind, site_key, tenant_host)`` of a page, from ``guard.canonical_host`` only (S0: no page text).
    LinkedIn is answered first, before any task is inferred. A non-empty web link whose host cannot be named
    (unparseable, userinfo, a host IDNA would remap) is ``("unnamed", "unnamed", "")``: :func:`strict` sends
    it to the own browser as ``link_unclear``, and it is never called LinkedIn. ATS keys come from
    ``sites.SITES`` (one list), a job board from ``JOB_BOARDS``, Oracle HCM from its host plus its
    candidate-experience path; anything else is ``("company", "host:" + host, host)``. A link with no web
    host at all is ``("company", "host:", "")``."""
    from src.services.browser_agent.sites import SITES  # noqa: PLC0415 - sites imports nothing back

    text = _web_url(url)
    if text is None:
        return "company", "host:", ""
    host = canonical_host(text)
    if host is None:
        return UNNAMED, UNNAMED, ""
    if is_linkedin_host(host):
        return "linkedin", "linkedin", host
    for key, domains in JOB_BOARDS.items():
        if _matches(host, domains):
            return "job_board", key, host
    if _matches(host, (_ORACLE_HOST,)):
        if urlsplit(text).path.lower().startswith(_ORACLE_PATH):
            return "ats", "oracle_hcm", host
        return "company", "host:" + host, host
    for site in SITES:
        if host not in site.deny_hosts and _matches(host, site.hosts):
            return "ats", site.name, host
    return "company", "host:" + host, host


def learn_task(action: str, application_id: int | None, looks_like_application: bool) -> str:
    """The task the learner books an attempt under: "fill" when the call carries an ``application_id`` (the
    server's own fact), else "read". ``looks_like_application`` is page-derived and ``action`` caller-derived
    (S0): both are accepted for the log only and never decide a route."""
    del action, looks_like_application
    return "fill" if application_id is not None else "read"


@dataclass(frozen=True)
class Route:
    kind: BrowserKind
    reason: str


def strict(
    *, site_kind: str, site_key: str, path: str = "", queue_kind: str = "", page_stop: str = "",
    account_step: bool = False,
) -> Route | None:
    """The rules no learning can change, in order. S0: every input is a closed value or the task's own link,
    never page or email text. S1: a password or a code is the user's alone. S3: a signed-in LinkedIn task
    (``QUEUE_KINDS``) is the own browser. S3b: a link whose host cannot be named is the own browser, reason
    ``link_unclear`` (fail closed, not LinkedIn). S6: a public LinkedIn job page is read by Job360's server when
    ``LINKEDIN_GUEST_READS``, else the own browser. S7: every other LinkedIn page is the own browser. S8: a
    create-account step is the own browser. S9: None means the learner may decide. (S2, the submit gate, and
    S4/S5, the caps and the pause, are enforced in their own modules.)"""
    del site_key
    if page_stop in HUMAN_STOPS:
        return Route("human", HUMAN_STOPS[page_stop])
    if site_kind == "linkedin":
        if queue_kind not in QUEUE_KINDS and LINKEDIN_GUEST_READS and _LINKEDIN_JOB_READ.match(path or ""):
            return Route("job360", "linkedin_guest_read")
        return Route("own_browser", "linkedin_own_browser")
    if site_kind == UNNAMED:
        return Route("own_browser", "link_unclear")
    if account_step:
        return Route("own_browser", "account_step")
    return None


def browser_for(
    *, page_stop: str = "", job360_failed: str = "", url: str = "", linkedin: bool = False,
    route: Route | None = None, queue_kind: str = "", account_step: bool = False,
) -> Route:
    """The route for a task: ``strict`` first, then what Job360's browser just reported, then the learned
    ``route``, then Job360's browser. ``page_stop`` is what the page asks for (a password, a code);
    ``job360_failed`` is why Job360's browser already stopped on it ("" = it has not tried or has not failed);
    ``url`` is the task's page (an application's apply link); ``linkedin`` marks a LinkedIn task that has no
    link here (a message or a connection note); ``route`` is the learner's answer for the site and can never
    override ``strict``."""
    if linkedin:
        kind, key, path = "linkedin", "linkedin", ""
    else:
        kind, key, _ = site_of(url)
        text = _web_url(url)
        path = urlsplit(text).path if text and text != "\0" else ""
    fixed = strict(
        site_kind=kind, site_key=key, path=path, queue_kind=queue_kind, page_stop=page_stop,
        account_step=account_step,
    )
    if fixed is not None:
        return fixed
    if job360_failed in JOB360_FAILURES:
        return Route("own_browser", JOB360_FAILURES[job360_failed])
    return route if route is not None else Route("job360", "default")


def ask_route(
    kind: str, *, block_reason: str = "", block_by_browser: bool = False, finish_here: bool = False, url: str = ""
) -> Route:
    """The route for one open Needs-you ask: its ``kind``, the reason of the open block it came from and
    whether Job360's own browser wrote that block (``by: browser``). ``finish_here``: the CAPTCHA can be
    solved right now on Needs you, in the page Job360's browser still holds - the user's alone. ``url``: the
    application's apply link; linkedin.com is the user's own browser for whatever is not the user's alone."""
    if finish_here:
        return Route("human", "only_you")
    if kind == "login" and block_reason == "otp_needed":
        return Route("human", "mfa")
    if block_by_browser and block_reason in JOB360_FAILURES:
        return Route("own_browser", JOB360_FAILURES[block_reason])
    if kind == "login":
        return Route("human", "password")
    if kind in ("upload", "safety", "captcha"):
        return Route("human", "only_you")
    if is_linkedin(url):  # fails closed; the reason names what the link is
        return Route("own_browser", "linkedin_own_browser" if names_linkedin(url) else "link_unclear")
    return Route("job360", "default")


def _site_name(site_key: object) -> str:
    key = str(site_key or "")
    if key in _SITE_NAMES:
        return _SITE_NAMES[key]
    host = key.removeprefix("host:")
    return host if key.startswith("host:") and _HOST_SAFE.match(host) else _FMT_DEFAULTS["site"]


def _day(value: object) -> str:
    """A date (or an ISO date string) as "3 Nov"; anything else is the default."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return _FMT_DEFAULTS["date"]
    if isinstance(value, (date, datetime)):
        return f"{value.day} {value:%b}"
    return _FMT_DEFAULTS["date"]


def _fmt(fmt: dict[str, object]) -> dict[str, str]:
    """Closed values only: ``site`` is a ``site_key`` (shown as its fixed display name, or the host of a
    ``host:`` key), ``date``/``until`` a date, ``n``/``cap`` whole numbers. Anything else falls back to a default."""
    out = dict(_FMT_DEFAULTS)
    if "site" in fmt:
        out["site"] = _site_name(fmt["site"])
    for key in ("date", "until"):
        if key in fmt:
            out[key] = _day(fmt[key])
    for key in ("n", "cap"):
        value = fmt.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            out[key] = str(value)
    return out


def label(route: Route, **fmt: object) -> dict[str, str]:
    """The chip a card shows: ``{"kind", "label", "reason"}``. ``fmt`` fills the placeholders of
    ``ROUTE_LABELS`` (``site``, ``date``, ``until``, ``n``, ``cap``) from closed values only."""
    if route.reason == "linkedin_guest_read":
        text = SERVER_READ_LABEL
    elif route.reason in ROUTE_LABELS and route.reason != "default":
        text = ROUTE_LABELS[route.reason].format_map(_fmt(fmt))
    else:
        text = LABELS[route.kind]
    return {"kind": route.kind, "label": text, "reason": route.reason}
