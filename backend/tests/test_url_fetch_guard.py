"""Frozen tests for the SSRF guard — the pure decision core of URL fetch
(docs/plans/2026-09-04-url-fetch/spec.md, items 1-15).

These tests pin ``src/services/fetch/guard.py``'s decision contract: the deny
nets (both families), the metadata/private/reserved predicates, v4-mapped
unwrapping, deny-whole-host on any bad address, DNS-rebinding resistance, the
post-connect peername re-check, and the URL-shape screen (scheme/userinfo/
IDN/deceptive literals) — the module docstring below is the assumed contract
these freeze in place.

WHY THIS FILE IS PURE — NO DNS, NO SOCKET, NO NETWORK
------------------------------------------------------
Rule #4 (the whole suite runs offline) plus spec R4: the guard's decision
functions take no I/O. Every resolver used here is a plain injected async
callable — never ``aiohttp.resolver.DefaultResolver`` — so this file can run
with the network cable pulled out and still mean something.

THE ASSUMED CONTRACT (frozen by these tests — build ``guard.py`` to satisfy it)
--------------------------------------------------------------------------
Spec R4 names ``DENY_NETS_V4``, ``DENY_NETS_V6``, ``screen_ip``, ``screen_url``,
``screen_host`` and ``GuardedResolver`` explicitly. Two more names are ADDED
here because R5's "belt" (the post-connect peername re-check) and R6's
"every hop re-enters the full screen" both need a callable shape a unit test
can drive without a socket:

    DENY_NETS_V4: tuple[ipaddress.IPv4Network, ...]
    DENY_NETS_V6: tuple[ipaddress.IPv6Network, ...]

    def screen_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None
        # None = allowed. Otherwise a short reason ("cloud metadata (AWS/GCP/
        # Azure IMDS)", "private/reserved network", ...). Unwraps a v4-mapped
        # v6 address (``::ffff:a.b.c.d``) to its v4 form BEFORE checking.
        # Reads settings.URL_FETCH_EXTRA_DENY_NETS / settings.URL_FETCH_ALLOW_NETS
        # LIVE on every call (house style: src/services/auth/rate_limit.py's
        # ``_redis_client()`` reads its flag live for exactly this reason — so a
        # test can monkeypatch ``settings`` and see the effect immediately).
        # ALLOW_NETS is checked only AFTER a net has matched the deny list, and
        # logs a WARNING (module logger, __name__) naming the net every time it
        # lets something through (S9).

    @dataclass
    class UrlVerdict:
        ok: bool
        scheme: str = ""
        host: str = ""     # normalised: lowercased, trailing dot stripped, IDNA A-label
        port: int = 0
        reason: str = ""

    def screen_url(raw: str) -> UrlVerdict
        # Scheme/host/shape ONLY. No DNS, no resolver parameter — that is what
        # makes "the resolver was called zero times" provable for a deceptive
        # literal: this function structurally cannot call one.

    @dataclass
    class HostVerdict:
        ok: bool
        approved: frozenset[str] = frozenset()   # resolved+screened addresses, textual
        denied: bool = False    # True => ssrf_denied; False + not ok => DNS/unreachable
        reason: str = ""

    async def screen_host(host: str, port: int, *, resolve: ResolveFn) -> HostVerdict
        # Calls `resolve` EXACTLY ONCE per invocation (no retry-driven second
        # lookup — that second lookup is the rebinding window). Screens EVERY
        # returned address (v4 and v6 both); if any is denied, denies the WHOLE
        # host (S4) — never filters down to the good ones.

    def verify_peername(approved: frozenset[str], peername_ip: str) -> str | None
        # R5's belt. None = the socket landed somewhere the resolver actually
        # approved FOR THIS HOP. Otherwise a reason ("peername ... was never
        # approved by the resolver").

    class GuardedResolver(aiohttp.abc.AbstractResolver):
        def __init__(self, *, resolve: ResolveFn) -> None
        async def resolve(self, host: str, port: int = 0, family: int = socket.AF_UNSPEC)
            -> list[dict]      # aiohttp's AbstractResolver record shape
            # Calls `resolve` exactly once, screens, denies-whole-on-any-bad,
            # and returns ONLY the approved records (or raises/empties on deny —
            # the fetcher decides ssrf_denied from an empty/erroring resolve).
        async def close(self) -> None
        def approved_for(self, host: str) -> frozenset[str]
            # The address set THIS instance last approved for `host` — read by
            # the peername re-check after connect (R5).

``ResolveFn`` shape: ``async def resolve(host: str, port: int = 0, family: int = 0)
-> list[dict]`` where each dict carries at least ``{"host": "<ip literal>"}``
(mirrors ``aiohttp.abc.AbstractResolver.resolve``'s return shape).
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Any

import pytest

from src.services.fetch.guard import (
    DENY_NETS_V4,
    DENY_NETS_V6,
    GuardedResolver,
    screen_host,
    screen_ip,
    screen_url,
    verify_peername,
)


def _ip(s: str):
    return ipaddress.ip_address(s)


# ── 1-2: named cloud metadata addresses ─────────────────────────────────────


def test_aws_metadata_v4_is_denied():
    reason = screen_ip(_ip("169.254.169.254"))
    assert reason is not None
    assert "metadata" in reason.lower()


def test_aws_metadata_v6_is_denied():
    reason = screen_ip(_ip("fd00:ec2::254"))
    assert reason is not None


# ── 3-4: every private/reserved net, both families ──────────────────────────

_PRIVATE_V4 = [
    "10.0.0.1", "172.16.0.1", "192.168.1.1", "127.0.0.1", "0.0.0.0",  # noqa: S104 — deny-list fixture data, not a bind address
    "169.254.0.1", "100.64.0.1", "192.0.0.1", "198.18.0.1", "224.0.0.1",
    "240.0.0.1", "255.255.255.255",
]


@pytest.mark.parametrize("addr", _PRIVATE_V4)
def test_every_private_v4_net_is_denied(addr):
    assert screen_ip(_ip(addr)) is not None, f"{addr} should be denied"


_RESERVED_V6 = [
    "::1", "::", "fc00::1", "fe80::1", "ff02::1", "2001:db8::1",
    "64:ff9b::7f00:1", "2002:7f00:1::",
]


@pytest.mark.parametrize("addr", _RESERVED_V6)
def test_every_reserved_v6_net_is_denied(addr):
    assert screen_ip(_ip(addr)) is not None, f"{addr} should be denied"


def test_deny_nets_are_declared_for_both_families():
    """Sanity: the module actually exports non-empty net tuples of the right type."""
    assert DENY_NETS_V4
    assert DENY_NETS_V6
    assert all(isinstance(n, ipaddress.IPv4Network) for n in DENY_NETS_V4)
    assert all(isinstance(n, ipaddress.IPv6Network) for n in DENY_NETS_V6)


# ── 5: v4-mapped v6 is unwrapped, then denied ───────────────────────────────


@pytest.mark.parametrize("addr", ["::ffff:169.254.169.254", "::ffff:10.0.0.1"])
def test_v4_mapped_v6_is_unwrapped_then_denied(addr):
    reason = screen_ip(_ip(addr))
    assert reason is not None, f"{addr} (v4-mapped) should be denied"


# ── 6: NEGATIVE CONTROL — a guard that denies everything is not a guard ────


def test_a_public_v4_and_v6_are_allowed():
    assert screen_ip(_ip("93.184.216.34")) is None
    assert screen_ip(_ip("2606:2800:220:1:248:1893:25c8:1946")) is None


# ── 7: one public + one private address denies the WHOLE host (S4) ─────────


async def test_a_host_with_one_public_and_one_private_address_is_denied_whole():
    async def resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        return [
            {"hostname": host, "host": "93.184.216.34", "port": port, "family": socket.AF_INET},
            {"hostname": host, "host": "10.0.0.1", "port": port, "family": socket.AF_INET},
        ]

    verdict = await screen_host("mixed.example.test", 443, resolve=resolve)
    assert verdict.ok is False
    assert verdict.denied is True
    # S4: NOT filtered down to the good one — the whole host is refused.
    assert "93.184.216.34" not in verdict.approved


# ── 8: DNS rebinding cannot win — the resolver is called exactly once ──────


async def test_dns_rebinding_cannot_win():
    calls: list[str] = []

    async def rebinding_resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        # A real rebinding attacker would answer "10.0.0.1" on a LATER lookup.
        # This fake never gets a later call to answer with, which is the proof:
        # nothing in the guard performs a second resolution to rebind.
        calls.append(host)
        return [{"hostname": host, "host": "93.184.216.34", "port": port, "family": socket.AF_INET}]

    resolver = GuardedResolver(resolve=rebinding_resolve)
    try:
        records = await resolver.resolve("evil.example.test", 443, socket.AF_UNSPEC)
    finally:
        await resolver.close()

    assert len(calls) == 1, "the resolver must be consulted exactly once per hop"
    assert {r["host"] for r in records} == {"93.184.216.34"}
    assert resolver.approved_for("evil.example.test") == frozenset({"93.184.216.34"})


# ── 9: a peername the resolver never approved is denied (R5 belt) ──────────


async def test_a_peername_the_resolver_never_approved_is_ssrf_denied():
    async def resolve(host: str, port: int = 0, family: int = 0) -> list[dict[str, Any]]:
        return [{"hostname": host, "host": "93.184.216.34", "port": port, "family": socket.AF_INET}]

    resolver = GuardedResolver(resolve=resolve)
    try:
        await resolver.resolve("good.example.test", 443, socket.AF_UNSPEC)
    finally:
        await resolver.close()

    approved = resolver.approved_for("good.example.test")
    # The socket claims it landed somewhere the resolver never approved.
    reason = verify_peername(approved, "127.0.0.1")
    assert reason is not None
    # And the address it DID approve is accepted.
    assert verify_peername(approved, "93.184.216.34") is None


# ── 10: non-web schemes ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "file:///etc/passwd",
        "ftp://example.test/x",
        "gopher://example.test/x",
        "javascript:alert(1)",
        "data:text/html,<script>1</script>",
        "//no-scheme.example.test/x",
    ],
)
def test_non_web_schemes_are_invalid_url(raw):
    verdict = screen_url(raw)
    assert verdict.ok is False


# ── 11: deceptive IP literals never reach a resolver ────────────────────────


@pytest.mark.parametrize("raw", ["http://2130706433/", "http://0177.0.0.1/", "http://0x7f000001/"])
def test_deceptive_ip_literals_never_reach_the_resolver(raw):
    verdict = screen_url(raw)
    assert verdict.ok is False, (
        f"{raw!r} must be refused as invalid_url by screen_url alone — it must "
        "never survive far enough to be handed to any resolver as a bare "
        "hostname (inet_aton accepts decimal/octal/hex forms ipaddress rejects)"
    )
    # screen_url is synchronous and takes no resolver argument at all — this is
    # what makes "the resolver was called zero times" true BY CONSTRUCTION,
    # not by convention: there is no parameter through which one could be called.
    import inspect

    assert "resolve" not in inspect.signature(screen_url).parameters


# ── 12: userinfo in the URL ──────────────────────────────────────────────────


def test_userinfo_in_the_url_is_invalid_url():
    verdict = screen_url("http://user:p@evil.com@10.0.0.1/")
    assert verdict.ok is False


# ── 13: IDN is screened on the A-label; malformed hosts are invalid_url ─────


def test_idn_is_screened_on_the_a_label():
    # xn--e1aybc.test is the A-label for a Cyrillic-looking label; screen_url
    # must normalise to it (or an equivalent ASCII form) rather than reject
    # non-ASCII outright, since IDN hosts are a legitimate case.
    verdict = screen_url("http://привет.test/")
    assert verdict.ok is True
    assert verdict.host.isascii()
    assert verdict.host.startswith("xn--")


@pytest.mark.parametrize(
    "raw",
    [
        "http://./",  # empty label
        "http://evil.test./",  # trailing dot is fine actually — kept only as a shape check below
        "http://-evil.test/",  # leading hyphen
        "http://evil-.test/",  # trailing hyphen
    ],
)
def test_malformed_hosts_are_invalid_url_or_normalised(raw):
    verdict = screen_url(raw)
    if raw == "http://evil.test./":
        # A single trailing dot is a normal DNS root marker — spec says it is
        # STRIPPED, not refused.
        assert verdict.ok is True
        assert verdict.host == "evil.test"
    else:
        assert verdict.ok is False


# ── 14: URL_FETCH_EXTRA_DENY_NETS ───────────────────────────────────────────


def test_extra_deny_nets_parameter_adds_a_net(monkeypatch):
    """A genuinely PUBLIC address (never in the base deny list) is denied only
    once its /24 is named in ``URL_FETCH_EXTRA_DENY_NETS`` — and the default
    (empty) adds nothing.
    """
    from src.core import settings

    public_target = _ip("93.184.216.34")

    monkeypatch.setattr(settings, "URL_FETCH_EXTRA_DENY_NETS", (), raising=False)
    assert screen_ip(public_target) is None, "the default must add nothing"

    monkeypatch.setattr(settings, "URL_FETCH_EXTRA_DENY_NETS", ("93.184.216.0/24",), raising=False)
    assert screen_ip(public_target) is not None, (
        "URL_FETCH_EXTRA_DENY_NETS must add a net without a code change"
    )


# ── 15: URL_FETCH_ALLOW_NETS — loud, empty by default (S9) ──────────────────


def test_allow_nets_is_empty_by_default_and_changes_nothing(monkeypatch):
    from src.core import settings

    monkeypatch.setattr(settings, "URL_FETCH_ALLOW_NETS", (), raising=False)
    assert settings.URL_FETCH_ALLOW_NETS == ()
    assert screen_ip(_ip("10.0.0.1")) is not None


def test_allow_nets_set_lets_a_net_through_and_warns(monkeypatch, caplog):
    from src.core import settings

    monkeypatch.setattr(settings, "URL_FETCH_ALLOW_NETS", ("10.0.0.0/8",), raising=False)
    with caplog.at_level(logging.WARNING):
        reason = screen_ip(_ip("10.0.0.1"))
    assert reason is None, "an address inside an explicit ALLOW net must be let through"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "letting an address through ALLOW_NETS must log a WARNING every time"
    assert any("10.0.0.0/8" in r.getMessage() for r in warnings)
