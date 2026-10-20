"""The ONE place the backend decides a caller's address (gap-audit fix #6).

Audit fields call :func:`client_ip`; per-address throttle keys call
:func:`throttle_client_ip` (the same address, IPv6 collapsed to its /64).
Nothing else in ``src/`` reads ``X-Forwarded-For`` (``tests/test_client_ip.py``
greps for that).

The rule. Each trusted proxy in front of the backend appends (or writes) one
X-Forwarded-For entry, so the trustworthy entries are the RIGHTMOST ones; the
caller can put anything to the left of them. With
``settings.TRUSTED_PROXY_HOPS = N`` the client is the entry ``N`` positions
from the right (``N = 1`` is the rightmost, written by the nearest proxy).
The leftmost entry is never used unless it is exactly that position. When the
header is missing, has fewer than ``N`` entries, or the chosen entry is not an
IP address, the socket peer (``request.client.host``) is used instead, the
same answer as with forwarding switched off. ``N = 0`` (the default) ignores
the header completely. How ``JOB360_TRUST_PROXY`` and
``JOB360_TRUSTED_PROXY_HOPS`` combine is written beside the settings in
``core/settings.py``.

Settings are read at call time, never bound at import, so a test can
monkeypatch ``settings.TRUSTED_PROXY_HOPS``.
"""
from __future__ import annotations

import ipaddress

from starlette.requests import HTTPConnection

from src.core import settings

UNKNOWN = "unknown"


def _forwarded_entries(request: HTTPConnection) -> list[str]:
    """Every X-Forwarded-For entry, in order. Repeated header lines are one
    list (RFC 9110 5.3), joined in arrival order; blank entries are dropped."""
    entries: list[str] = []
    for value in request.headers.getlist("x-forwarded-for"):
        entries.extend(part.strip() for part in value.split(","))
    return [e for e in entries if e]


def _as_ip(value: str) -> str | None:
    """The canonical text of ``value`` when it is an IPv4/IPv6 address, else None.
    Canonical form, so ``::FFFF:1.2.3.4`` and ``::ffff:1.2.3.4`` share a bucket."""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def client_ip(request: HTTPConnection) -> str:
    """The caller's address, as audit logs record it. See the module docstring
    for the rule; never raises, never returns an empty string. Rate-limit keys
    use :func:`throttle_client_ip` instead."""
    peer = request.client.host if request.client and request.client.host else UNKNOWN
    hops = settings.TRUSTED_PROXY_HOPS
    if hops <= 0:
        return peer
    entries = _forwarded_entries(request)
    if len(entries) < hops:
        return peer
    return _as_ip(entries[-hops]) or peer


# One IPv6 host is normally handed a whole /64 (SLAAC, privacy addresses), so it
# can rotate through ~2^64 addresses for free. Throttle keys count the /64.
IPV6_THROTTLE_PREFIX = 64


def throttle_ip(ip: str) -> str:
    """The rate-limit identity of ``ip``: an IPv6 address becomes its /64
    (``2001:db8:1:2::/64``), an IPv4-mapped IPv6 address becomes the IPv4
    address, and anything else (IPv4, ``"unknown"``) is returned unchanged."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        try:
            return str(ipaddress.IPv6Network((addr, IPV6_THROTTLE_PREFIX), strict=False))
        except ValueError:  # pragma: no cover — defensive (e.g. a scoped address)
            return str(addr)
    return str(addr)


def throttle_client_ip(request: HTTPConnection) -> str:
    """The caller's address for rate-limit keys: :func:`client_ip`, with IPv6
    collapsed to its /64 by :func:`throttle_ip`. Never use it for audit logs,
    which keep the full address."""
    return throttle_ip(client_ip(request))
