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
Assistant-neutral on purpose: no name of any assistant or browser appears in any text here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

# Pure helpers only (stdlib + ipaddress; the module's aiohttp import is for its resolver type): one host test.
from src.services.fetch.guard import canonical_host, is_linkedin_host

BrowserKind = Literal["job360", "own_browser", "human"]

ROUTE_REASONS = (
    "default", "job360_login_required", "job360_bot_check", "job360_safety_stop", "password", "mfa", "only_you",
    "linkedin_own_browser",
)

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


def is_linkedin(url: str) -> bool:
    """True for a link on a LinkedIn domain (``guard.LINKEDIN_DOMAINS``) or any subdomain of one, compared on
    ``guard.canonical_host``: the exact host, never a substring. Fails closed: a non-empty web link whose host
    cannot be named (unparseable, userinfo, a host IDNA would remap like ``www.ⓛinkedin.com``) counts as
    LinkedIn, so it waits for the user's own browser. "" is no link (False); a link with no web host at all
    (``mailto:``) is not LinkedIn either. A link written without a scheme is read as ``https://``."""
    text = (url or "").strip()
    if not text:
        return False
    try:
        scheme = urlsplit(text).scheme.lower()
    except ValueError:
        return True
    if scheme not in ("http", "https"):
        if scheme and "." not in scheme:
            return False  # mailto:, tel:, about: - no web host to open
        text = "https://" + text.removeprefix("//")  # "www.linkedin.com/x" or "host:443/x": scheme-less
    host = canonical_host(text)
    return host is None or is_linkedin_host(host)


@dataclass(frozen=True)
class Route:
    kind: BrowserKind
    reason: str


def browser_for(*, page_stop: str = "", job360_failed: str = "", url: str = "", linkedin: bool = False) -> Route:
    """The route for a task. ``page_stop`` is what the page asks for (a password, a code);
    ``job360_failed`` is why Job360's browser already stopped on it ("" = it has not tried or has not failed);
    ``url`` is the task's page (an application's apply link); ``linkedin`` marks a LinkedIn task that has no
    link here (a message or a connection note)."""
    if page_stop in HUMAN_STOPS:
        return Route("human", HUMAN_STOPS[page_stop])
    if linkedin or is_linkedin(url):
        return Route("own_browser", "linkedin_own_browser")
    if job360_failed in JOB360_FAILURES:
        return Route("own_browser", JOB360_FAILURES[job360_failed])
    return Route("job360", "default")


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
    if is_linkedin(url):
        return Route("own_browser", "linkedin_own_browser")
    return Route("job360", "default")


def label(route: Route) -> dict[str, str]:
    """The chip a card shows: ``{"kind", "label", "reason"}``."""
    text = LINKEDIN_LABEL if route.reason == "linkedin_own_browser" else LABELS[route.kind]
    return {"kind": route.kind, "label": text, "reason": route.reason}
