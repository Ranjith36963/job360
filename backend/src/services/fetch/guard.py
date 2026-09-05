"""The SSRF guard for URL fetch — the pure decision core.

docs/plans/2026-09-04-url-fetch/spec.md R4/R5/R6, security guardrails S3-S6,
S9. This module makes no HTTP request and does no DNS lookup of its own: the
resolver is always INJECTED (``ResolveFn``), so every function here — and the
drill (``scripts/ssrf_drill.py``) that breaks it on purpose — runs offline,
with the network cable pulled out, and still means something (rule #4).

Design, in one paragraph: ``screen_ip`` classifies one already-resolved
address (deny-by-default, S3). ``screen_url`` classifies a URL's shape alone
— scheme, userinfo, host normalisation, deceptive numeric literals — with NO
resolver parameter, so "the resolver was called zero times" for a decimal/
octal/hex IP literal is true by construction, not convention (frozen test
11). ``screen_host`` resolves once and screens every returned address (both
families); if any is denied, the WHOLE host is denied (S4) — never filtered
down to the good ones, because a host answering with one public and one
private address is a rebinding attempt, not a multi-homed server to help.
``GuardedResolver`` IS the aiohttp resolver the fetcher's connector uses, so
there is no second lookup between "checked" and "connected" (R5 — no TOCTOU
by construction). ``verify_peername`` is R5's belt: after connect, the real
socket peer must be one of the addresses THIS hop's resolver actually
approved.

Every anchored ``# SSRF-ANCHOR:*`` comment below is load-bearing: it is where
``scripts/ssrf_drill.py`` neutralises exactly one guarded statement per case
to prove the guard can still go RED. Do not move, rename, or remove one
without updating the drill's docstring and cases.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import socket
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

import aiohttp.abc

logger = logging.getLogger("job360.services.fetch.guard")

# A resolver: async, host/port/family in, aiohttp AbstractResolver-shaped
# records out (each carrying at least {"host": "<ip literal>"}).
ResolveFn = Callable[..., Awaitable[list[aiohttp.abc.ResolveResult]]]

# ---------------------------------------------------------------------------
# Deny nets (spec security guardrails A2) — both families, hand-typed list
# PLUS the stdlib predicates (belt and braces: the predicates catch what a
# list forgets; the list catches what the predicates call "global" — CGNAT
# 100.64/10, NAT64, 6to4).
# ---------------------------------------------------------------------------

_DENY_V4_CIDRS: tuple[str, ...] = (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.0.0.0/24", "192.0.2.0/24", "192.168.0.0/16", "198.18.0.0/15",
    "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4", "255.255.255.255/32",
)
DENY_NETS_V4: tuple[ipaddress.IPv4Network, ...] = tuple(
    ipaddress.IPv4Network(c) for c in _DENY_V4_CIDRS
)

# The v6 deny list needs its OWN anchor: the drill's case 1 (docs/plans/
# 2026-09-04-url-fetch/spec.md #377) must be able to neutralise it with ONE
# statement. A multi-line tuple literal can't be — commenting out just its
# opening line leaves the remaining lines as orphaned syntax. So the real
# name is pre-declared empty, then overwritten by ONE self-contained line
# right after the anchor; disabling that line leaves DENY_NETS_V6 == () with
# no syntax error, which is exactly the attack the drill's case 1 demands
# (::1 and fd00::1 both land).
_DENY_V6_CIDRS: tuple[str, ...] = (
    "::1/128", "::/128", "fc00::/7", "fe80::/10", "ff00::/8",
    "2001:db8::/32", "64:ff9b::/96", "2002::/16", "100::/64",
    # Added by the adversarial review (measured 2026-09-04): fec0::1,
    # 64:ff9b:1::7f00:1, 2001:10::1, ::a9fe:a9fe, ::7f00:1, 5f00::1 all passed
    # the guard because the v6 arm below had no stdlib-predicate belt at all
    # (v4 always had one). The predicate belt closes those six on THIS
    # CPython; these three CIDRs are named explicitly too — belt AND braces,
    # same reasoning as the v4 list's CGNAT/NAT64/6to4 comment above, so the
    # deny stands even on a CPython whose private/reserved classification
    # changes (it already has, across versions, for 100.64/10 and NAT64).
    "64:ff9b:1::/48", "fec0::/10", "5f00::/16",
)
DENY_NETS_V6: tuple[ipaddress.IPv6Network, ...] = ()
# SSRF-ANCHOR:DENY_NETS_V6
DENY_NETS_V6 = tuple(ipaddress.IPv6Network(c) for c in _DENY_V6_CIDRS)

# Named explicitly (spec A1) so a regression reports "cloud metadata", not
# "some private IP" — and so the drill/tests have a stable, human-readable
# target. The deny NETS above already cover these (169.254/16, fc00::/7)
# whatever hostname points at them; naming them is for the message only.
_METADATA_V4: frozenset[ipaddress.IPv4Address] = frozenset(
    ipaddress.IPv4Address(a) for a in ("169.254.169.254", "100.100.100.200", "192.0.0.192")
)
_METADATA_V6: frozenset[ipaddress.IPv6Address] = frozenset(
    ipaddress.IPv6Address(a) for a in ("fd00:ec2::254",)
)

_DENY_REASON = "private/reserved network"


def _base_reason(ip: ipaddress._BaseAddress) -> Optional[str]:
    """Deny-by-default classification for an address ALREADY of its native
    family (v4-mapped v6 must be unwrapped before reaching here — see
    :func:`screen_ip`). No live settings read here; that is :func:`screen_ip`'s job.
    """
    if isinstance(ip, ipaddress.IPv4Address):
        if ip in _METADATA_V4:
            return "cloud metadata (AWS/GCP/Azure IMDS)"
        if any(ip in net for net in DENY_NETS_V4):
            return _DENY_REASON
        if ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return _DENY_REASON
        return None
    # Narrow for mypy: everything below is the v6 arm (the only other subclass).
    assert isinstance(ip, ipaddress.IPv6Address)
    if ip in _METADATA_V6:
        return "cloud metadata (AWS IMDSv6)"
    if any(ip in net for net in DENY_NETS_V6):
        return _DENY_REASON
    # The v6 twin of the v4 predicate belt above (measured gap, 2026-09-04
    # review): fec0::1, 64:ff9b:1::7f00:1, 2001:10::1, ::a9fe:a9fe, ::7f00:1
    # and 5f00::1 all passed the guard with no predicate check here at all.
    #
    # ``ip.ipv4_mapped is None`` scopes this to GENUINE v6 addresses on
    # purpose: several of these very properties (``is_private``, ``is_reserved``,
    # ``is_multicast``, ``is_unspecified``, ``is_loopback``, ``is_link_local``)
    # silently re-unwrap a ``::ffff:a.b.c.d`` address to its v4 form inside
    # modern CPython itself (verified 3.10/3.12/3.13) — without this guard a
    # v4-mapped address would be screened HERE too, making the explicit
    # unwrap-then-recurse-into-the-v4-arm ``screen_ip`` does before calling
    # this function provably redundant and untestable by the drill. The
    # unwrap stays the ONE place that decision is made.
    if ip.ipv4_mapped is None and (
        ip.is_private or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        or ip.is_site_local or ip.is_loopback or ip.is_link_local
    ):
        # SSRF-ANCHOR:V6_PREDICATE_BELT
        return _DENY_REASON
    return None


def _extra_deny_reason(ip: ipaddress._BaseAddress) -> Optional[str]:
    """URL_FETCH_EXTRA_DENY_NETS — read LIVE (house style: rate_limit.py's
    ``_redis_client()`` reads its flag live for exactly this reason), so a
    test can monkeypatch ``settings`` and see the effect on the next call.
    """
    from src.core import settings  # noqa: PLC0415 — live read, not bound at import

    for cidr in getattr(settings, "URL_FETCH_EXTRA_DENY_NETS", ()):
        try:
            net = ipaddress.ip_network(cidr)
        except ValueError:
            continue
        if net.version == ip.version and ip in net:
            return f"denied by URL_FETCH_EXTRA_DENY_NETS ({cidr})"
    return None


def _allow_net_override(ip: ipaddress._BaseAddress) -> bool:
    """S9 — the loud escape hatch. Checked ONLY after a net has matched the
    deny list (base or extra); empty by default, changes nothing. Every time
    it lets an address through it logs a WARNING naming the net, live-read
    the same way as the deny lists above.
    """
    from src.core import settings  # noqa: PLC0415 — live read, not bound at import

    for cidr in getattr(settings, "URL_FETCH_ALLOW_NETS", ()):
        try:
            net = ipaddress.ip_network(cidr)
        except ValueError:
            continue
        if net.version == ip.version and ip in net:
            logger.warning(
                "URL_FETCH_ALLOW_NETS let %s through via explicit net %s — this is a "
                "documented hole with an alarm on it (spec S9)", ip, cidr,
            )
            return True
    return False


def screen_ip(ip: ipaddress._BaseAddress) -> Optional[str]:
    """Classify one already-resolved address. ``None`` = allowed.

    Unwraps a v4-mapped v6 address (``::ffff:a.b.c.d``) to its v4 form BEFORE
    any check — the ONLY place that unwrap happens, which is what makes the
    drill's case 2 (dropping it) mean something.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        # SSRF-ANCHOR:V4_MAPPED_UNWRAP
        ip = ip.ipv4_mapped

    reason = _base_reason(ip)
    if reason is None:
        reason = _extra_deny_reason(ip)
    if reason is None:
        return None
    if _allow_net_override(ip):
        return None
    return reason


# ---------------------------------------------------------------------------
# screen_url — scheme/host/shape ONLY. No resolver parameter at all (spec
# R4 / frozen test 11): a deceptive IP literal is refused HERE, before it
# could ever be handed to any resolver as a bare hostname.
# ---------------------------------------------------------------------------


@dataclass
class UrlVerdict:
    ok: bool
    scheme: str = ""
    host: str = ""
    port: int = 0
    reason: str = ""


# inet_aton-style decimal/octal/hex literals: 1-4 dot-separated segments,
# each a bare decimal run or a 0x-prefixed hex run. ``ipaddress.ip_address``
# rejects all of these outright (verified: 2130706433, 0177.0.0.1,
# 0x7f000001 all raise ValueError) — this regex is what stops such a string
# from being handed to a resolver as an ordinary hostname instead.
_DECEPTIVE_HOST_RE = re.compile(
    r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+)){0,3}$", re.IGNORECASE,
)


def _normalize_host(raw_host: str) -> tuple[str, str]:
    """Lowercase, strip ONE trailing dot, refuse deceptive/malformed shapes,
    IDNA-encode. Returns ``(host, "")`` on success or ``("", reason)``.

    ``raw_host`` is assumed non-empty and already lowercased by
    :func:`urllib.parse.SplitResult.hostname`.
    """
    host = raw_host[:-1] if raw_host.endswith(".") else raw_host

    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        # A genuine IP literal — no label/IDNA validation needed; screen_ip
        # (called later, on the RESOLVED address) does the real work.
        return host, ""

    if _DECEPTIVE_HOST_RE.match(host):
        # SSRF-ANCHOR:DECEPTIVE_IP_REFUSAL
        return "", "deceptive numeric host literal"

    labels = host.split(".")
    if any(not label for label in labels):
        return "", "host has an empty label"
    if any(label.startswith("-") or label.endswith("-") for label in labels):
        return "", "host label starts or ends with a hyphen"

    try:
        encoded = host.encode("idna").decode("ascii")
    except UnicodeError:
        return "", "host could not be IDNA-encoded"
    if not encoded.isascii():
        return "", "host is not ASCII after IDNA encoding"
    return encoded, ""


def screen_url(raw: str) -> UrlVerdict:
    """Scheme/host/shape only. No DNS, no resolver — see module docstring."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return UrlVerdict(ok=False, reason="could not parse the URL")

    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        # SSRF-ANCHOR:SCHEME_CHECK
        return UrlVerdict(ok=False, scheme=scheme, reason="only http/https URLs are allowed")

    # "which side of the second @ is the host" is a question no two parsers
    # answer identically — refuse ANY userinfo outright rather than pick.
    if "@" in parts.netloc:
        return UrlVerdict(ok=False, scheme=scheme, reason="userinfo in a URL is not allowed")

    try:
        raw_host = parts.hostname  # already lowercased by urlsplit
        port = parts.port
    except ValueError:
        return UrlVerdict(ok=False, scheme=scheme, reason="malformed host or port")

    if not raw_host:
        # No host at all — e.g. a non-web scheme that slipped past a
        # weakened check above. Nothing left to screen; the fetcher's own
        # request/DNS step will fail this on its own.
        return UrlVerdict(ok=True, scheme=scheme, host="", port=port or 0)

    host, reason = _normalize_host(raw_host)
    if reason:
        return UrlVerdict(ok=False, scheme=scheme, reason=reason)
    return UrlVerdict(ok=True, scheme=scheme, host=host, port=port or (443 if scheme == "https" else 80))


# ---------------------------------------------------------------------------
# screen_host — resolves ONCE, screens every returned address, denies the
# WHOLE host on any bad one (S4). This IS the anti-rebinding device at the
# per-hop level (R6); GuardedResolver below is the SAME logic at the
# connector level (R5), used for the real socket the request actually opens.
# ---------------------------------------------------------------------------


@dataclass
class HostVerdict:
    ok: bool
    approved: frozenset[str] = field(default_factory=frozenset)
    denied: bool = False
    reason: str = ""


def _screen_addresses(addresses: set[str]) -> tuple[frozenset[str], Optional[str]]:
    """Screen every address; return (approved-if-all-good, first bad reason)."""
    reasons = {addr: screen_ip(ipaddress.ip_address(addr)) for addr in addresses}
    bad = next((r for r in reasons.values() if r is not None), None)
    approved = frozenset(addr for addr, r in reasons.items() if r is None)
    return approved, bad


async def screen_host(host: str, port: int, *, resolve: ResolveFn) -> HostVerdict:
    """Resolve ``host`` exactly once and screen every returned address."""
    records = await resolve(host, port, socket.AF_UNSPEC)
    addresses = {str(r["host"]) for r in records if r.get("host")}
    if not addresses:
        return HostVerdict(ok=False, denied=False, reason="no address returned for host")

    approved, bad_reason = _screen_addresses(addresses)
    if bad_reason is not None:
        # SSRF-ANCHOR:DENY_WHOLE_HOST
        return HostVerdict(ok=False, denied=True, approved=frozenset(), reason=bad_reason)
    return HostVerdict(ok=True, approved=approved)


# ---------------------------------------------------------------------------
# GuardedResolver — the guard IS the resolver (R5). aiohttp's connector calls
# .resolve() as the ONLY name resolution of the hop; we screen what it
# returns and hand back only the approved records, so there is no window
# between "checked" and "connected" to rebind in.
# ---------------------------------------------------------------------------


def _resolver_key(host: str) -> str:
    """The SAME cheap normalisation ``screen_url``'s ``_normalize_host``
    applies (lowercase, one trailing dot stripped) — used ONLY as the
    dict key GuardedResolver stores/reads its approved-set under, so a raw
    host aiohttp hands the resolver (which need not byte-match the fetcher's
    own ``url_verdict.host``) still finds its entry (B6).
    """
    host = host.lower()
    return host[:-1] if host.endswith(".") else host


class GuardedResolver(aiohttp.abc.AbstractResolver):
    """An aiohttp resolver that screens every address it resolves.

    ``family=socket.AF_UNSPEC`` on the connector means BOTH A and AAAA
    records reach ``resolve()`` here and are BOTH screened — screening only
    the family a caller happens to prefer is how a v6 record smuggles past.
    """

    def __init__(self, *, resolve: ResolveFn) -> None:
        self._resolve = resolve
        self._approved: dict[str, frozenset[str]] = {}

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[aiohttp.abc.ResolveResult]:
        records = await self._resolve(host, port, family)
        addresses = {str(r["host"]) for r in records if r.get("host")}
        approved, bad_reason = _screen_addresses(addresses) if addresses else (frozenset(), "empty resolve")

        # B6 — keyed on a NORMALIZED host (lowercased, one trailing dot
        # stripped): aiohttp hands this resolver whatever string it parsed
        # off the wire, which need not byte-match the caller's OWN normalised
        # ``url_verdict.host`` (screen_url's ``_normalize_host``) — a
        # ``evil.test.`` URL is the measured mismatch.
        key = _resolver_key(host)
        if bad_reason is not None:
            # S4 — deny the whole host, never filter to the good ones. Return
            # empty rather than raise: the docstring in test_url_fetch_guard.py
            # explicitly allows either; the fetcher decides ssrf_denied from
            # an empty/erroring resolve.
            self._approved[key] = frozenset()
            return []

        self._approved[key] = approved
        return [r for r in records if str(r.get("host")) in approved]

    async def close(self) -> None:
        return None

    def approved_for(self, host: str) -> frozenset[str]:
        """The address set THIS instance last approved for ``host`` — read by
        the post-connect peername re-check (R5's belt).
        """
        return self._approved.get(_resolver_key(host), frozenset())


def verify_peername(approved: frozenset[str], peername_ip: str) -> Optional[str]:
    """R5's belt: the real socket peer must be one the resolver approved for
    THIS hop. ``None`` = accepted.
    """
    if peername_ip not in approved:
        # SSRF-ANCHOR:PEERNAME_RECHECK
        return f"peername {peername_ip!r} was never approved by the resolver"
    return None
