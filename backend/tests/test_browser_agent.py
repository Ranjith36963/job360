"""The Job360 browser agent (docs/product/BROWSER_AGENT.md, VISION decision 29).

Three layers, each pinned here:

1. The hard lines as pure rules (``safety.py``) — no browser, no network.
2. The switch: OFF by default, the route and the MCP tool both answer ``off``.
3. A real Chromium run against a local test site (skipped when Playwright or
   its Chromium is not installed, as on CI). The site is served on loopback.
   Chromium reaches it only through the egress proxy: a fake resolver gives
   ``jobs.example`` a public address and the proxy's injected connector maps
   that address to the loopback site, so nothing leaves the machine (rule #4).
"""
from __future__ import annotations

import socket

import pytest

from src.services.browser_agent.safety import RequestScreen, blocker_for, control_kind

# ── 1. The hard lines, as pure rules ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("tag", "input_type", "name", "autocomplete", "in_form", "role", "answers", "kind"),
    [
        ("input", "password", "Password", "", True, "", False, "password"),
        ("input", "text", "Your password", "", True, "", False, "password"),
        ("input", "text", "Email", "current-password", True, "", False, "password"),
        ("input", "text", "First name", "", True, "", False, "field"),
        ("textarea", "", "Why do you want this job?", "", True, "", False, "field"),
        ("button", "submit", "Submit application", "", True, "", False, "submit"),
        ("button", "button", "Submit", "", False, "", False, "submit"),
        ("input", "submit", "Send", "", True, "", False, "submit"),
        ("button", "submit", "Apply", "", True, "", False, "submit"),
        ("button", "submit", "", "", True, "", False, "submit"),
        ("div", "", "Submit application", "", False, "button", False, "submit"),
        # React forms without a <form>: once answers are typed, "Send" / "Apply" is the end.
        ("div", "", "Send application", "", False, "button", True, "submit"),
        ("button", "button", "Complete application", "", False, "", True, "submit"),
        # Steps of a multi-page form are not the final submit, even as type=submit.
        ("button", "submit", "Next", "", True, "", True, "field"),
        ("button", "submit", "Save and continue", "", True, "", True, "field"),
        # A job ad's "Apply" (nothing typed yet) only opens the form.
        ("a", "", "Apply for this job", "", False, "", False, "field"),
        ("a", "", "Apply for this job", "", False, "", True, "field"),
        ("button", "button", "Apply for this job", "", False, "", False, "field"),
        ("a", "", "Submit application", "", False, "", False, "submit"),
    ],
)
def test_control_kind(tag, input_type, name, autocomplete, in_form, role, answers, kind):
    assert control_kind(tag, input_type, name, autocomplete, in_form, role, answers) == kind


def test_a_captcha_frame_or_text_needs_the_user():
    b = blocker_for(["https://jobs.example/apply", "https://www.google.com/recaptcha/api2/anchor?k=x"], "", False)
    assert b is not None and b.reason == "captcha"
    b = blocker_for(["https://jobs.example/"], "Please verify you are human to continue", False)
    assert b is not None and b.reason == "captcha"
    b = blocker_for(["https://jobs.example/"], "", has_password_box=True)
    assert b is not None and b.reason == "login_required"
    assert blocker_for(["https://jobs.example/"], "Apply for Senior Engineer", False) is None


def _resolver(table: dict[str, str]):
    calls: list[str] = []

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        calls.append(host)
        if host not in table:
            raise OSError("no such host")
        return [{"host": table[host], "port": port, "family": socket.AF_INET}]

    return resolve, calls


@pytest.mark.asyncio
async def test_the_request_screen_lets_only_the_public_internet_through():
    resolve, calls = _resolver({"jobs.example": "93.184.216.34", "inside.example": "10.0.0.5",
                                "railway.example": "fd12::1"})
    screen = RequestScreen(resolve)
    assert (await screen.screen("https://jobs.example/apply")).ok
    assert not (await screen.screen("http://inside.example/")).ok
    assert not (await screen.screen("http://railway.example/")).ok
    assert not (await screen.screen("http://127.0.0.1:8000/api/health")).ok
    assert not (await screen.screen("file:///etc/passwd")).ok
    assert not (await screen.screen("http://user@jobs.example/")).ok
    assert not (await screen.screen("https://nowhere.example/")).ok
    assert (await screen.screen("data:text/plain,hi")).ok
    # The answer for a host is cached: a second request costs no lookup.
    before = len(calls)
    assert (await screen.screen("https://jobs.example/other")).ok
    assert len(calls) == before
