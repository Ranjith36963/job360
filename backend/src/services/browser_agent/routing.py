"""Which browser a task uses - pure, stdlib only (owner decisions 2026-10-25).

Job360's own cloud browser goes FIRST for everything, job forms and LinkedIn alike; it refuses nothing
up front, so no URL or host list lives here. A task moves to the user's OWN browser (the one their
assistant drives on their computer) only when Job360's browser actually failed on it: a sign-in wall, a
CAPTCHA or bot check, or a safety stop. ``blocked`` events written with ``by: browser`` are the log of
that failure. Passwords, one-time codes (MFA) and a CAPTCHA the user must solve are the user's alone
("human"): no browser types a password or solves a CAPTCHA. A CAPTCHA is always solved by the user: on
Needs you while Job360's browser holds the page (Finish it here), otherwise in their own browser.
Assistant-neutral on purpose: no name of any assistant or browser appears in any text here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BrowserKind = Literal["job360", "own_browser", "human"]

ROUTE_REASONS = (
    "default", "job360_login_required", "job360_bot_check", "job360_safety_stop", "password", "mfa", "only_you",
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


@dataclass(frozen=True)
class Route:
    kind: BrowserKind
    reason: str


def browser_for(*, page_stop: str = "", job360_failed: str = "") -> Route:
    """The route for a task. ``page_stop`` is what the page asks for (a password, a code);
    ``job360_failed`` is why Job360's browser already stopped on it ("" = it has not tried or has not failed)."""
    if page_stop in HUMAN_STOPS:
        return Route("human", HUMAN_STOPS[page_stop])
    if job360_failed in JOB360_FAILURES:
        return Route("own_browser", JOB360_FAILURES[job360_failed])
    return Route("job360", "default")


def ask_route(
    kind: str, *, block_reason: str = "", block_by_browser: bool = False, finish_here: bool = False
) -> Route:
    """The route for one open Needs-you ask: its ``kind``, the reason of the open block it came from and
    whether Job360's own browser wrote that block (``by: browser``). ``finish_here``: the CAPTCHA can be
    solved right now on Needs you, in the page Job360's browser still holds - the user's alone."""
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
    return Route("job360", "default")


def label(route: Route) -> dict[str, str]:
    """The chip a card shows: ``{"kind", "label", "reason"}``."""
    return {"kind": route.kind, "label": LABELS[route.kind], "reason": route.reason}
