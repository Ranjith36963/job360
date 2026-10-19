"""The Job360 browser agent's safety layer (docs/product/BROWSER_AGENT.md).

Pure rules and the egress proxy, no browser:

1. The hard lines (``safety.py``): which control is a password box or the
   final submit, and when a page needs the user (CAPTCHA, sign-in wall).
2. The request screen and the egress proxy (``egress.py``): only the public
   internet gets through, and the proxy tries every approved address (a fake
   resolver and an injected connector to a loopback site, so nothing leaves
   the machine — rule #4).
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


@pytest.mark.asyncio
@pytest.mark.parametrize("v6_works,v4_works,tried,status", [
    (False, True, ["93.184.216.34"], b"200"),                       # IPv4 first; IPv6 never needed
    (True, False, ["93.184.216.34", "2606:2800:220:1::248"], b"200"),  # IPv4 down: next approved address
    (False, False, ["93.184.216.34", "2606:2800:220:1::248"], b"403"),  # all down: refused
])
async def test_the_proxy_tries_ipv4_first_then_every_approved_address(v6_works, v4_works, tried, status):
    import asyncio

    from src.services.browser_agent.egress import EgressProxy

    async def site(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(site, "127.0.0.1", 0)
    site_port = server.sockets[0].getsockname()[1]
    attempts: list[str] = []

    async def open_connection(address, port):
        attempts.append(address)
        if (":" in address and v6_works) or (":" not in address and v4_works):
            return await asyncio.open_connection("127.0.0.1", site_port)
        raise OSError("no route")

    async def resolve(host, port=0, family=socket.AF_UNSPEC):
        return [{"host": "2606:2800:220:1::248", "port": port, "family": socket.AF_INET6},
                {"host": "93.184.216.34", "port": port, "family": socket.AF_INET}]

    proxy = EgressProxy(resolve, open_connection)
    await proxy.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        writer.write(b"GET http://jobs.example/ HTTP/1.1\r\nHost: jobs.example\r\n\r\n")
        await writer.drain()
        first = await asyncio.wait_for(reader.readline(), 10)
        writer.close()
    finally:
        await proxy.close()
        server.close()
    assert status in first, first
    assert attempts == tried
