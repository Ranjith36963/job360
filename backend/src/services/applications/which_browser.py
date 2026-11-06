"""Which browser did a fill, sweep, submit, block or application (owner 2026-11-03).

An optional ``browser`` key on the payload of :data:`EVENTS` and on ``record_application`` (stored on the
receipt's ``applied`` event), so per-site success can be counted per browser (``stats(view="sites")``):

* ``job360`` - Job360's own cloud browser (``services/browser_agent``);
* ``own_browser`` - the user's own browser that their assistant drives (Claude in Chrome, the own-browser queue);
* ``manual`` - the user, by hand;
* ``unknown`` - not said. A row without the key reads ``unknown`` (old rows are never rewritten, M3), except
  rows Job360's browser wrote before this key existed (``by``: browser / refill / restore), which read ``job360``.

Where the server knows, it decides (never the caller's word alone): what Job360's browser writes itself (its
submit click, its blocks, a refill) carries ``job360``; a caller's ``job360`` is refused unless Job360's browser
has a page open for this user; no ``browser`` while this job's own Job360 browser is open reads ``job360``.
Nothing knows the user's own browser server-side (an own-browser application's hold is the ordinary kit hold),
so ``own_browser`` and ``manual`` are the caller's word. ``unblocked`` {resolution: own_browser} (``blocked``) is
stamped ``own_browser`` by the server: it clears only a block Job360's browser wrote.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Optional

from src.core import settings
from src.services.applications.spine import SpineError

KEY = "browser"
JOB360, OWN, MANUAL, UNKNOWN = "job360", "own_browser", "manual", "unknown"
BROWSERS = (JOB360, OWN, MANUAL, UNKNOWN)
EVENTS = frozenset({"form_checked", "form_filled", "form_swept", "submit_attempted", "blocked"})
# ``by`` values only Job360's browser writes (blocked.BY_BROWSER, browser.replay_and_hold's refill / restore).
_JOB360_BY = frozenset({"browser", "refill", "restore"})
BAD_VALUE = "browser must be one of: " + ", ".join(BROWSERS)
# Set only by server code around its own write (Job360's browser recording its submit click): never a caller's.
SERVER_SAYS: ContextVar[Optional[str]] = ContextVar("which_browser_server_says", default=None)


def read(payload: Any) -> str:
    """The browser a stored payload names; ``unknown`` when it names none."""
    if not isinstance(payload, dict):
        return UNKNOWN
    value = payload.get(KEY)
    if value in BROWSERS:
        return str(value)
    return JOB360 if payload.get("by") in _JOB360_BY else UNKNOWN


def _open(user_id: str, application_id: int) -> tuple[bool, bool]:
    """(this job's Job360 browser is open, any Job360 browser of this user is open). No pool when it is off."""
    if not settings.BROWSER_AGENT_ENABLED:
        return False, False
    from src.services.browser_agent import agent as browser_agent  # noqa: PLC0415 - lazy (playwright stack)

    keys = browser_agent.get_pool().user_keys(user_id)
    return browser_agent.browser_key(user_id, application_id) in keys, bool(keys)


def resolve(user_id: str, application_id: int, given: Any) -> Optional[str]:
    """The browser to store for a caller's value (``None`` = store none, it reads ``unknown``). 422 for a
    value outside :data:`BROWSERS`, or ``job360`` while Job360's browser has no page open for this user (a
    scratch browser counts: a form may be filled before its job is named). :data:`SERVER_SAYS` wins."""
    if SERVER_SAYS.get() is not None:
        return SERVER_SAYS.get()
    if given is not None and given not in BROWSERS:
        raise SpineError(422, BAD_VALUE)
    job_open, any_open = _open(user_id, application_id) if given in (None, JOB360) else (False, False)
    if given == JOB360 and not any_open:
        raise SpineError(
            422, "browser job360: Job360's browser has no page open for you; name the browser you used "
            "(own_browser or manual)",
        )
    if given is None:
        return JOB360 if job_open else None
    return None if given == UNKNOWN else str(given)


def split(user_id: str, application_id: int, event_type: str, payload: dict[str, Any]) -> tuple[
    dict[str, Any], Optional[str]
]:
    """``payload`` without ``browser`` (each event's own validator never sees it) and the browser to store."""
    if event_type not in EVENTS:
        return payload, None
    rest = {k: v for k, v in payload.items() if k != KEY}
    return rest, resolve(user_id, application_id, payload.get(KEY))


def stamp(stored: dict[str, Any], browser: Optional[str]) -> dict[str, Any]:
    return {**stored, KEY: browser} if browser else stored
