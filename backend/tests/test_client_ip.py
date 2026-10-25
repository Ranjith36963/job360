"""``src.api.client_ip.client_ip`` — the one reader of X-Forwarded-For (gap-audit fix #6).

Before this, five call sites took the LEFTMOST X-Forwarded-For entry when
``JOB360_TRUST_PROXY=1``. The caller writes that entry, so every per-IP
throttle could be dodged by sending a new forged value each time. The helper
takes the entry ``TRUSTED_PROXY_HOPS`` places from the RIGHT (the ones our own
proxies wrote) and falls back to the socket peer otherwise.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.requests import Request

from src.api import client_ip as client_ip_mod
from src.api.client_ip import UNKNOWN, client_ip, throttle_client_ip, throttle_ip
from src.core import settings

PEER = "10.250.0.7"


def _request(*xff: str, peer: str | None = PEER) -> Request:
    """A bare ASGI request with the given X-Forwarded-For header lines."""
    headers = [(b"x-forwarded-for", v.encode("latin-1")) for v in xff]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "client": (peer, 40000) if peer is not None else None,
    }
    return Request(scope)


@pytest.fixture
def hops(monkeypatch):
    def _set(n: int) -> None:
        monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", n)

    return _set


# ─── forwarding off ──────────────────────────────────────────────────────────


def test_hops_zero_ignores_forwarded_for(hops):
    hops(0)
    assert client_ip(_request("203.0.113.9")) == PEER


def test_no_client_is_unknown(hops):
    hops(0)
    assert client_ip(_request(peer=None)) == UNKNOWN
    hops(1)
    assert client_ip(_request(peer=None)) == UNKNOWN


# ─── rightmost-hop selection ─────────────────────────────────────────────────


def test_hops_one_takes_rightmost_entry(hops):
    hops(1)
    assert client_ip(_request("198.51.100.1, 203.0.113.9")) == "203.0.113.9"


def test_hops_two_takes_second_from_right(hops):
    hops(2)
    # caller-forged, real client (written by the outer proxy), inner proxy
    assert client_ip(_request("1.1.1.1, 203.0.113.9, 10.0.0.2")) == "203.0.113.9"


def test_spoofed_leftmost_entry_does_not_change_the_address(hops):
    """The bug: rotating a forged leftmost value bought a fresh bucket every time."""
    hops(1)
    seen = {
        client_ip(_request(f"6.6.6.{i}, 203.0.113.9")) for i in range(20)
    }
    assert seen == {"203.0.113.9"}


def test_repeated_header_lines_are_one_list(hops):
    """RFC 9110: two header lines equal one comma-joined line, in order, so a
    forged first line cannot push the proxy's entry out of position."""
    hops(1)
    assert client_ip(_request("6.6.6.6", "203.0.113.9")) == "203.0.113.9"
    hops(2)
    assert client_ip(_request("6.6.6.6, 203.0.113.9", "10.0.0.2")) == "203.0.113.9"


def test_ipv6_entry_is_accepted_and_canonical(hops):
    hops(1)
    assert client_ip(_request("2001:DB8::0001")) == "2001:db8::1"


# ─── fallbacks ───────────────────────────────────────────────────────────────


def test_chain_shorter_than_hops_falls_back_to_peer(hops):
    hops(2)
    assert client_ip(_request("203.0.113.9")) == PEER


def test_missing_header_falls_back_to_peer(hops):
    hops(1)
    assert client_ip(_request()) == PEER


@pytest.mark.parametrize(
    "garbage",
    ["not-an-ip", "203.0.113.9:443", "unknown", "<script>", "999.1.1.1", "203.0.113.9 x"],
)
def test_garbage_entry_falls_back_to_peer(hops, garbage):
    hops(1)
    assert client_ip(_request(f"198.51.100.1, {garbage}")) == PEER


def test_blank_entries_do_not_count_as_hops(hops):
    hops(1)
    assert client_ip(_request(", ,")) == PEER
    assert client_ip(_request("203.0.113.9, ")) == "203.0.113.9"


# ─── throttle keys: IPv6 counted per /64 ─────────────────────────────────────


def test_throttle_ip_collapses_ipv6_to_its_slash64():
    keys = {throttle_ip(f"2001:db8:1:2:{i:x}::{i + 1:x}") for i in range(10)}
    assert keys == {"2001:db8:1:2::/64"}
    assert throttle_ip("2001:db8:1:3::1") == "2001:db8:1:3::/64"


@pytest.mark.parametrize(
    ("ip", "key"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("::ffff:203.0.113.9", "203.0.113.9"),  # IPv4-mapped: the IPv4 address
        (UNKNOWN, UNKNOWN),
        ("testclient", "testclient"),
    ],
)
def test_throttle_ip_leaves_other_addresses_alone(ip, key):
    assert throttle_ip(ip) == key


def test_throttle_client_ip_vs_client_ip(hops):
    """Throttle keys collapse IPv6; the audit value keeps the full address."""
    hops(1)
    req = _request("2001:db8:1:2:aaaa:bbbb:cccc:dddd")
    assert client_ip(req) == "2001:db8:1:2:aaaa:bbbb:cccc:dddd"
    assert throttle_client_ip(req) == "2001:db8:1:2::/64"


# ─── settings: how JOB360_TRUST_PROXY and JOB360_TRUSTED_PROXY_HOPS combine ──


@pytest.mark.parametrize(
    ("trust", "hops_raw", "expected"),
    [
        ("", "", 0),       # both unset: never trust the header
        ("1", "", 1),      # old flag alone: rightmost entry, never the leftmost
        ("0", "", 0),
        ("true", "", 0),   # unchanged: only the exact "1" ever enabled the flag
        ("1", "2", 2),     # HOPS wins
        ("", "2", 2),
        ("1", "0", 0),     # HOPS=0 switches forwarding off even with the flag
        ("", "-3", 0),     # a negative count is 0, never "from the left"
        ("1", " 1 ", 1),
    ],
)
def test_resolve_trusted_proxy_hops(trust, hops_raw, expected):
    assert settings.resolve_trusted_proxy_hops(trust, hops_raw) == expected


def test_resolve_trusted_proxy_hops_rejects_garbage():
    with pytest.raises(ValueError):
        settings.resolve_trusted_proxy_hops("1", "two")


# ─── the guard: nothing else reads the header ────────────────────────────────


def test_no_other_module_reads_forwarded_for():
    src = Path(client_ip_mod.__file__).resolve().parents[1]
    helper = Path(client_ip_mod.__file__).resolve()
    literal = re.compile(r"""["']x-forwarded-for["']""", re.IGNORECASE)
    flag = re.compile(r"""getenv\(\s*["']JOB360_TRUST(ED)?_PROXY""")
    offenders = []
    for path in src.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if path.resolve() != helper and literal.search(text):
            offenders.append(str(path))
        if path.name != "settings.py" and flag.search(text):
            offenders.append(str(path))
    assert not offenders, f"read client address via src.api.client_ip only: {offenders}"


def test_throttle_ip_drops_an_ipv6_scope_id():
    """A link-local address with a zone (``fe80::1%eth0``) still collapses to its /64."""
    assert throttle_ip("fe80::1%eth0") == "fe80::/64"


def test_railway_chain_one_hop_reads_the_edge_entry_not_the_relay(hops):
    """Railway 2026-10-25: the relay is the socket peer, the edge appends the
    caller; one hop takes the caller and ignores what the caller wrote."""
    hops(1)
    req = _request("6.6.6.6, 84.17.44.226", peer="100.64.0.12")
    assert client_ip(req) == "84.17.44.226"
