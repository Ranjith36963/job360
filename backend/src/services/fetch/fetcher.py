"""The URL-fetch HTTP layer: manual redirect loop, budgets, size cap,
content-type check (spec R5-R7).

DELIBERATELY DOES NOT IMPORT ``extract.py`` OR ``outcomes.py``
----------------------------------------------------------------
``scripts/ssrf_drill.py`` copies ONLY ``guard.py`` and ``fetcher.py`` into its
throwaway per-case package (see its ``_prepare_fetcher``) — ``extract.py``
and ``outcomes.py`` are never copied. If this module imported either of them
via the ``src.services.fetch....`` path, the drill's blanket text-rewrite
(``_rewrite_internal_imports``) would repoint that import at a module that
does not exist in the copy, and every mutated case would fail with an
``ImportError`` instead of exercising the intended bypass. So:
  * outcomes are plain string literals here (kept in lockstep with
    ``outcomes.py`` by the ROUTE-level frozen tests, which import both and
    would go red on day one of any drift);
  * the raw decoded HTML is returned on ``FetchResult.html`` and extraction
    (``extract.extract_job_fields``) is the ROUTE's job
    (``src/api/routes/bring.py``), which the drill never touches.

``DEFAULT_RESOLVE`` / ``DEFAULT_CLOCK`` / ``VERIFY_PEERNAME_AFTER_CONNECT``
are plain module globals, read by NAME inside :func:`fetch_url` (never bound
as a default argument value) — a global name reference is looked up in this
module's ``__dict__`` at call time, so ``monkeypatch.setattr(fetcher,
"DEFAULT_RESOLVE", fake)`` (or a direct ``fetcher_mod.DEFAULT_RESOLVE = fake``
from the drill) takes effect on the very next call, exactly like
``rate_limit.py``'s ``_redis_client()`` reads its flag live.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

import aiohttp.abc

from src.services.fetch.guard import GuardedResolver, HostVerdict, ResolveFn, screen_host, screen_url, verify_peername

logger = logging.getLogger("job360.services.fetch.fetcher")

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CHUNK_SIZE = 65536


async def DEFAULT_RESOLVE(  # noqa: N802 — module-global contract name, spec R4
    host: str, port: int = 0, family: socket.AddressFamily = socket.AF_UNSPEC,
) -> list[aiohttp.abc.ResolveResult]:
    """Real DNS resolution — aiohttp's own default resolver. Lazy import
    (rule #16): the module and pytest collection never pay for aiohttp's
    resolver machinery unless a real fetch actually happens.
    """
    import aiohttp.resolver  # noqa: PLC0415

    resolver = aiohttp.resolver.DefaultResolver()
    try:
        return await resolver.resolve(host, port, family)
    finally:
        await resolver.close()


def _default_clock() -> float:
    return time.monotonic()


DEFAULT_CLOCK: Callable[[], float] = _default_clock

# R5's belt. Production default True; forced False wherever the transport is
# mocked (aioresponses has no real socket, so there is no real peername to
# read) — an explicit flag, never sniffed from "are we under test" (plan.md's
# own risk note).
VERIFY_PEERNAME_AFTER_CONNECT: bool = True


@dataclass
class FetchResult:
    outcome: str
    message: str = ""
    final_url: str = ""
    redirects: int = 0
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    found: list[str] = field(default_factory=list)
    source_hint: str = ""
    bytes_read: int = 0
    elapsed_ms: int = 0
    # Internal only — never serialised on the wire. The route runs extraction
    # over this (see module docstring for why it isn't done here).
    html: str = ""


def _result(outcome: str, **kwargs: Any) -> FetchResult:
    return FetchResult(outcome=outcome, **kwargs)


def _media_type(content_type_header: str) -> str:
    return content_type_header.split(";", 1)[0].strip().lower()


def _make_decompressor(content_encoding: str) -> Optional[Any]:
    """A bounded, streaming decompressor for ``Content-Encoding`` — or
    ``None`` when the body isn't (recognisably) compressed.

    ``auto_decompress`` is turned OFF on our own session (see
    :func:`fetch_url`) so this is the ONLY place decompression happens, in
    BOTH real and mocked (aioresponses does not decompress on its own,
    measured directly) traffic: the size cap must be enforced on DECODED
    bytes (A5's gzip-bomb attack), so decompression and the cap-check must
    live in the SAME streaming loop, never "decompress fully, then check".
    """
    encoding = content_encoding.strip().lower()
    if encoding in ("gzip", "x-gzip"):
        return zlib.decompressobj(zlib.MAX_WBITS | 16)
    if encoding == "deflate":
        return zlib.decompressobj(-zlib.MAX_WBITS)
    return None


def _peer_ip(resp: Any) -> Optional[str]:
    """Best-effort read of the real socket peer (R5's belt). ``None`` under
    any mocked/absent transport — the caller decides what "unknown" means.
    """
    try:
        connection = resp.connection
        if connection is None:
            return None
        transport = connection.transport
        if transport is None:
            return None
        peername = transport.get_extra_info("peername")
        if not peername:
            return None
        return str(peername[0])
    except Exception:  # noqa: BLE001 — never let introspection crash the fetch
        return None


async def fetch_url(url: str) -> FetchResult:
    """Fetch ``url`` under the SSRF guard, following redirects by hand.

    Reads its resolver/clock/peername-check switch from this module's OWN
    globals at call time (see module docstring) — never bound as defaults.
    """
    from src.core import settings  # noqa: PLC0415 — live read, house style

    resolve: ResolveFn = DEFAULT_RESOLVE
    clock: Callable[[], float] = DEFAULT_CLOCK
    verify_peer: bool = VERIFY_PEERNAME_AFTER_CONNECT

    import aiohttp  # noqa: PLC0415 — heavy, lazy per rule #16

    start = clock()
    current = url
    redirects = 0
    seen: set[str] = set()

    max_bytes = settings.URL_FETCH_MAX_BYTES
    total_budget = settings.URL_FETCH_TOTAL_BUDGET_S
    max_redirects = settings.URL_FETCH_MAX_REDIRECTS
    allowed_types = settings.URL_FETCH_ALLOWED_CONTENT_TYPES

    guarded_resolver = GuardedResolver(resolve=resolve)
    connector = aiohttp.TCPConnector(resolver=guarded_resolver, family=socket.AF_UNSPEC)
    timeout = aiohttp.ClientTimeout(total=settings.URL_FETCH_TIMEOUT_S)
    headers = {
        "User-Agent": settings.URL_FETCH_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }

    async with aiohttp.ClientSession(
        connector=connector, timeout=timeout, cookie_jar=aiohttp.DummyCookieJar(),
        headers=headers, auth=None, auto_decompress=False,
    ) as session:
        while True:
            if clock() - start > total_budget:
                return _result("timeout", redirects=redirects, final_url=current)
            if redirects > max_redirects:
                return _result("unreachable", redirects=redirects, final_url=current)

            url_verdict = screen_url(current)
            if not url_verdict.ok:
                return _result("invalid_url", redirects=redirects, final_url="")

            if current in seen:
                return _result("unreachable", redirects=redirects, final_url=current)
            seen.add(current)

            host_verdict: HostVerdict = HostVerdict(ok=True, approved=frozenset())
            try:
                # SSRF-ANCHOR:REDIRECT_SCREEN_EVERY_HOP
                host_verdict = await screen_host(url_verdict.host, url_verdict.port, resolve=resolve)
            except asyncio.TimeoutError:
                return _result("timeout", redirects=redirects, final_url=current)
            except OSError:
                # A typo'd/nonexistent host raises socket.gaierror (an OSError
                # subclass) straight out of the resolver — this used to escape
                # the closed-outcome contract entirely (measured: HTTP 500).
                # "we could not resolve it" is the same product outcome as any
                # other DNS/connect failure on this hop.
                return _result("unreachable", redirects=redirects, final_url=current)
            if not host_verdict.ok:
                if host_verdict.denied:
                    return _result("ssrf_denied", redirects=redirects, final_url="")
                return _result("unreachable", redirects=redirects, final_url=current)

            # B7 — a per-hop timeout of the FULL URL_FETCH_TIMEOUT_S on every
            # hop lets N hops cost up to N*URL_FETCH_TIMEOUT_S, defeating
            # URL_FETCH_TOTAL_BUDGET_S as a real ceiling. Shrink this hop's
            # own budget to whatever is left of the whole-journey clock.
            hop_timeout_s = max(0.001, min(settings.URL_FETCH_TIMEOUT_S, total_budget - (clock() - start)))
            try:
                resp = await session.get(
                    current, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=hop_timeout_s),
                )
            except asyncio.TimeoutError:
                return _result("timeout", redirects=redirects, final_url=current)
            except (aiohttp.ClientError, OSError):
                return _result("unreachable", redirects=redirects, final_url=current)

            async with resp:
                if resp.status in _REDIRECT_STATUSES:
                    location = resp.headers.get("Location", "")
                    if not location:
                        return _result("unreachable", redirects=redirects, final_url=current)
                    next_url = urljoin(current, location)
                    next_scheme = urlsplit(next_url).scheme.lower()
                    if next_scheme not in ("http", "https"):
                        return _result("invalid_url", redirects=redirects, final_url="")
                    redirects += 1
                    current = next_url
                    continue

                if resp.status >= 400:
                    return _result("blocked", redirects=redirects, final_url=current)

                if verify_peer:
                    peer_ip = _peer_ip(resp)
                    # B6 — a raw-IP-literal URL (http://93.184.216.34/) never
                    # reaches GuardedResolver.resolve() at all: aiohttp's own
                    # connector recognises an already-numeric host and skips
                    # its resolver entirely. ``approved_for`` is then always
                    # empty for it, which used to read as "nothing was ever
                    # approved" -> a false ssrf_denied on a perfectly public
                    # literal. ``screen_host`` (this SAME hop, just above)
                    # already resolved+screened this exact host, so fall back
                    # to ITS approved set whenever the resolver-level one is
                    # empty — the two are the same host, screened twice.
                    approved = guarded_resolver.approved_for(url_verdict.host) or host_verdict.approved
                    if peer_ip is None or verify_peername(approved, peer_ip) is not None:
                        return _result("ssrf_denied", redirects=redirects, final_url="")

                content_type = _media_type(resp.headers.get("Content-Type", ""))
                unsupported_content = False
                if content_type not in allowed_types:
                    # SSRF-ANCHOR:CONTENT_TYPE_CHECK
                    unsupported_content = True
                if unsupported_content:
                    return _result("unsupported_content", redirects=redirects, final_url=current, bytes_read=0)

                declared_len = resp.headers.get("Content-Length")
                if declared_len is not None:
                    try:
                        if int(declared_len) > max_bytes:
                            return _result("too_large", redirects=redirects, final_url=current, bytes_read=0)
                    except ValueError:
                        pass

                # auto_decompress=False on the session (above), so this is the
                # ONLY place a compressed body is inflated — decompression and
                # the cap-check share one streaming loop so the cap is on
                # DECODED bytes (A5's gzip-bomb attack), never on the
                # (much smaller) wire size.
                decompressor = _make_decompressor(resp.headers.get("Content-Encoding", ""))
                buffer = bytearray()
                bytes_read = 0
                too_large = False
                try:
                    async for chunk in resp.content.iter_chunked(_CHUNK_SIZE):
                        if decompressor is not None:
                            remaining = max_bytes + 1 - bytes_read
                            try:
                                piece = decompressor.decompress(bytes(chunk), max(0, remaining))
                            except zlib.error:
                                return _result("unreachable", redirects=redirects, final_url=current)
                        else:
                            piece = bytes(chunk)
                        buffer.extend(piece)
                        bytes_read += len(piece)
                        if bytes_read > max_bytes:
                            # SSRF-ANCHOR:STREAMING_SIZE_CAP
                            too_large = True
                        if too_large:
                            break
                except asyncio.TimeoutError:
                    return _result("timeout", redirects=redirects, final_url=current, bytes_read=bytes_read)
                except (aiohttp.ClientError, OSError):
                    return _result("unreachable", redirects=redirects, final_url=current, bytes_read=bytes_read)

                if too_large:
                    return _result("too_large", redirects=redirects, final_url=current, bytes_read=bytes_read)

                elapsed_ms = int((clock() - start) * 1000)
                html_text = bytes(buffer).decode("utf-8", errors="replace")
                return _result(
                    "ok",
                    final_url=current, redirects=redirects, bytes_read=bytes_read,
                    elapsed_ms=max(0, elapsed_ms), html=html_text,
                )
