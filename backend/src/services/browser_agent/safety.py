"""The browser agent's safety rules — pure decisions, no browser, no network.

docs/product/BROWSER_AGENT.md "Hard lines". Every rule here is a plain
function over plain values so the suite tests it offline (rule #4) without a
Chromium on the machine:

* ``control_kind`` — is this control a password box, a final submit, or an
  ordinary field? A password box is never typed into (hard line 5); a final
  submit is only clicked after ``check_submit`` says ``submit``.
* ``blocker_for`` — does this page show a CAPTCHA, a bot check or a sign-in
  wall? Then the agent stops and the job goes to Needs you. We never solve
  one and never disguise the browser to get past one.
* ``screen_request`` — may the browser load this address at all? Only the
  public internet: the same SSRF guard the URL fetch uses
  (``services/fetch/guard.py``), applied to EVERY request the page makes.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Optional

from src.services.fetch.guard import ResolveFn, screen_host, screen_url

# "Submit" in a button's name always means the end of a form.
_STRONG_SUBMIT = re.compile(r"\bsubmit", re.IGNORECASE)
# These mean the end on a button in a form, or on any page where answers have
# been typed; a job ad's "Apply for this job" (nothing typed yet) just opens the form.
_SUBMIT_WORDS = re.compile(r"\b(apply|send|finish|complete|confirm)\b", re.IGNORECASE)
# Words that make a submit-shaped button a step, not the end: multi-page forms
# (Workday, SmartRecruiters) use <button type=submit> for "Next".
_STEP_WORDS = re.compile(
    r"\b(next|continue|back|previous|save|search|sign in|log in|login|upload|add|edit|remove|cancel|close)\b",
    re.IGNORECASE,
)
_PASSWORD_NAME = re.compile(r"pass(word|code|phrase)|\bpwd\b", re.IGNORECASE)


def control_kind(
    tag: str,
    input_type: str,
    name: str,
    autocomplete: str = "",
    in_form: bool = False,
    role: str = "",
    page_has_answers: bool = False,
) -> str:
    """``password`` | ``submit`` | ``field`` for one control on the page.

    ``tag`` is the lower-case element name, ``input_type`` its ``type``
    attribute (lower case, may be empty), ``name`` its visible label or text,
    ``autocomplete`` its autocomplete attribute, ``in_form`` whether it sits
    inside a ``<form>``, ``role`` its ARIA role (a ``<div role=button>`` is a
    button too) and ``page_has_answers`` whether any field on the page already
    holds a typed answer or a chosen file. "Apply" / "Send" / "Finish" on a
    button counts as the final submit inside a form OR on a page with answers
    (React forms often have no ``<form>``); on a page with nothing typed it is
    the ad's "Apply for this job" that opens the form. When unsure, a button counts as the final submit: the
    gate then asks, which is the safe side.
    """
    tag = (tag or "").lower()
    input_type = (input_type or "").lower()
    autocomplete = (autocomplete or "").lower()
    name = name or ""
    role = (role or "").lower()
    if input_type == "password" or "password" in autocomplete:
        return "password"
    is_button = (
        tag == "button" or role == "button" or (tag == "input" and input_type in ("submit", "button", "image"))
    )
    if tag in ("input", "textarea") and not is_button:
        return "password" if _PASSWORD_NAME.search(name) else "field"
    if not is_button and tag != "a":
        return "field"
    if _STEP_WORDS.search(name) and not _STRONG_SUBMIT.search(name):
        return "field"
    if _STRONG_SUBMIT.search(name):
        return "submit"
    if tag == "a" and role != "button":
        return "field"
    if (in_form or page_has_answers) and _SUBMIT_WORDS.search(name):
        return "submit"
    # A real submit button of a form with no clear name: the safe side.
    if in_form and input_type in ("submit", "image", "") and not name.strip():
        return "submit"
    return "field"


# Frame addresses of the CAPTCHA and bot-check vendors we know. A page that
# loads one of these is asking a human to prove they are human.
_CAPTCHA_FRAME = re.compile(
    r"recaptcha|hcaptcha\.com|challenges\.cloudflare\.com|turnstile|arkoselabs|funcaptcha|"
    r"captcha-delivery\.com|geo\.captcha|perimeterx|px-captcha|kasada|datadome",
    re.IGNORECASE,
)
_CAPTCHA_TEXT = re.compile(
    r"verify (that )?you are (a )?human|are you a robot|i'?m not a robot|press (&|and) hold|"
    r"checking your browser|unusual traffic|complete the security check|prove you'?re not a robot",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Blocker:
    reason: str  # captcha | login_required
    detail: str


def blocker_for(frame_urls: list[str], page_text: str, has_password_box: bool) -> Optional[Blocker]:
    """The first reason this page needs the user, or None.

    A CAPTCHA wins over a sign-in wall: both need the user, the CAPTCHA is the
    one they cannot skip.
    """
    if any(_CAPTCHA_FRAME.search(u or "") for u in frame_urls) or _CAPTCHA_TEXT.search(page_text or ""):
        return Blocker(
            "captcha",
            "This page asks for a human check (CAPTCHA). Job360's browser never solves one: the user finishes "
            "this job with their own browser.",
        )
    if has_password_box:
        return Blocker(
            "login_required",
            "This page asks for a sign-in. Job360's browser never types a password: the user signs in once "
            "themselves on the Job360 website (Browser page, 'Keep me signed in'), then try again.",
        )
    return None


@dataclass(frozen=True)
class RequestVerdict:
    ok: bool
    reason: str = ""


# Schemes a page loads from itself; they never leave the browser.
_LOCAL_SCHEMES = ("data", "blob", "about")


class RequestScreen:
    """Screens every address the browser asks for, caching each host's answer.

    The cache is short (``ttl_s``) because a host's addresses can change; a
    denied host stays denied for the same time. ``resolve`` is injected, so
    tests run with a fake resolver and no DNS.
    """

    def __init__(self, resolve: ResolveFn, ttl_s: float = 60.0) -> None:
        self._resolve = resolve
        self._ttl = ttl_s
        self._cache: dict[tuple[str, int], tuple[float, RequestVerdict]] = {}

    async def screen(self, url: str) -> RequestVerdict:
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme in _LOCAL_SCHEMES:
            return RequestVerdict(True)
        verdict = screen_url(url)
        if not verdict.ok:
            return RequestVerdict(False, verdict.reason)
        if not verdict.host:
            return RequestVerdict(False, "no host")
        key = (verdict.host, verdict.port)
        now = time.monotonic()
        hit = self._cache.get(key)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        try:
            host_verdict = await screen_host(verdict.host, verdict.port, resolve=self._resolve)
        except OSError:
            result = RequestVerdict(False, "the address could not be resolved")
        else:
            result = RequestVerdict(host_verdict.ok, host_verdict.reason)
        self._cache[key] = (now, result)
        return result
