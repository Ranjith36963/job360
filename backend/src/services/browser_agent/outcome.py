"""What happened when Job360's browser opened a page - pure, from transport facts only (routes PR A, C3).

An outcome is read off what Job360's server saw on the wire: the main document's HTTP status, a few response
headers the bot-wall vendors set, and the host the browser ended on. Page text never decides it: a page can
say anything ("Just a moment...", a vendor's script tag), so the body-text detectors in ``safety.py`` still
stop the page for safety, but their outcome is always ``page_error``, never ``bot_block``. Nothing here reads
a body, a title or an element.

``transport_of`` builds the ``transport`` field the ``open`` action returns; ``classify`` turns it, plus the
agent's own stop reason, into one of ``OUTCOMES``.
"""
from __future__ import annotations

import re
from typing import Any, Mapping
from urllib.parse import urlsplit

# Pure helper only (stdlib + ipaddress): the one host test.
from src.services.fetch.guard import canonical_host

OUTCOMES = ("ok", "confirmed", "bot_block", "login_wall", "captcha", "rate_limited", "page_error")

# The host a challenge redirect lands on: the browser was sent to the bot wall itself.
CHALLENGE_HOSTS = ("challenges.cloudflare.com",)
# Status codes a bot wall answers with (999 is LinkedIn's).
BOT_STATUSES = (403, 999)

# The closed set of signals ``transport_of`` reads off the response; nothing else ever enters ``signals``.
SIGNALS = ("cf_challenge", "datadome", "akamai_ref", "akamai", "server_cloudflare", "server_akamai", "login_path")
# Of those, the ones that mean a bot wall answered (the Akamai reference is ``akamai-grn``, its "Reference #").
# Cloudflare sends ``cf-mitigated: challenge`` only on a challenge; DataDome and Akamai stamp their headers on
# ordinary pages too, so those two count only on an error status (400 or more).
BOT_SIGNALS = frozenset({"cf_challenge"})
BOT_SIGNALS_ON_ERROR = frozenset({"datadome", "akamai_ref"})

# The agent's stop reasons that mean the page did not load or the browser failed.
ERROR_STOPS = frozenset({"timeout", "load_failed", "browser_error"})
# The stop reason for a safety stop that came from the page's own text (safety._CAPTCHA_TEXT): page-derived,
# so its outcome is ``page_error`` whatever the stop was called.
TEXT_STOP = "page_text"

# A sign-in page's path: the last segments name the sign-in itself.
_LOGIN_PATH = re.compile(
    r"/(?:log[-_]?in|sign[-_]?in|signon|sso|auth(?:enticate)?|authwall|users/sign_in|account/login)(?:/|$|\.)",
    re.IGNORECASE,
)


def _header(headers: Mapping[str, str], name: str) -> str:
    """One header's value by lower-case name ("" when absent); Playwright already lower-cases names."""
    for key, value in headers.items():
        if str(key).lower() == name:
            return str(value or "")
    return ""


def transport_of(status: int, headers: Mapping[str, str], requested_url: str, final_url: str) -> dict[str, Any]:
    """The ``transport`` field for one ``open``: ``{status, final_host, signals}``.

    ``status`` is the main document's HTTP status (0: no response, a same-document load); ``headers`` its
    response headers; ``requested_url`` the address opened; ``final_url`` the address the tab ended on.
    ``final_host`` is the canonical host ("" when it cannot be named). ``signals`` is a sorted list from
    ``SIGNALS``: only header names and fixed values are matched, never a header's free text being echoed."""
    names = {str(k).lower() for k in headers}
    signals: set[str] = set()
    if _header(headers, "cf-mitigated").strip().lower() == "challenge":
        signals.add("cf_challenge")
    if "x-datadome" in names:
        signals.add("datadome")
    if "akamai-grn" in names:
        signals.add("akamai_ref")
    if any(n.startswith("x-akamai-") for n in names):
        signals.add("akamai")
    server = _header(headers, "server").lower()
    if "cloudflare" in server:
        signals.add("server_cloudflare")
    if "akamaighost" in server:
        signals.add("server_akamai")
    final_host = canonical_host(final_url) or ""
    if final_host and final_host == canonical_host(requested_url):
        try:
            path = urlsplit(final_url).path or ""
        except ValueError:
            path = ""
        if _LOGIN_PATH.search(path):
            signals.add("login_path")  # a sign-in path of the SAME host only: another host's is not this site's
    return {"status": int(status or 0), "final_host": final_host, "signals": sorted(signals)}


def classify(transport: Mapping[str, Any], stop_reason: str = "", needs_you: str = "") -> str:
    """One of ``OUTCOMES`` for an ``open``, the rules in order:

    1. status 403 or 999, a Cloudflare challenge, a DataDome header or an Akamai reference on an error status
       (400 or more), or a final host in ``CHALLENGE_HOSTS``: ``bot_block``;
    2. status 429: ``rate_limited``;
    3. a stop from the page's own text (``TEXT_STOP``): ``page_error``, never ``bot_block`` (C3);
    4. ``needs_you == "login_required"``, or a final URL on a sign-in path of the same host: ``login_wall``;
    5. ``needs_you == "captcha"``: ``captcha``;
    6. status 500 or more, a timeout, ``load_failed`` or ``browser_error``: ``page_error``;
    7. otherwise ``ok``.

    Only ``status``, ``final_host`` and ``signals`` are read; any other key is ignored."""
    try:
        status = int(transport.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    signals = {s for s in (transport.get("signals") or ()) if s in SIGNALS}
    final_host = str(transport.get("final_host") or "")
    if (
        status in BOT_STATUSES or signals & BOT_SIGNALS or final_host in CHALLENGE_HOSTS
        or (status >= 400 and signals & BOT_SIGNALS_ON_ERROR)
    ):
        return "bot_block"
    if status == 429:
        return "rate_limited"
    if stop_reason == TEXT_STOP:
        return "page_error"
    if needs_you == "login_required" or "login_path" in signals:
        return "login_wall"
    if needs_you == "captcha":
        return "captcha"
    if status >= 500 or stop_reason in ERROR_STOPS:
        return "page_error"
    return "ok"
