"""The browser agent's egress proxy — the ONE door Chromium's traffic leaves by.

Why it exists (CodeRabbit on #902): Playwright's ``context.route`` sees the
first request of a navigation only. Redirect hops, WebSockets and a host
whose DNS changes after our check all slipped past it. Chromium is therefore
launched with this proxy as its only way out (``--proxy-server`` plus
``<-loopback>``, so even localhost goes through it), and the proxy:

1. reads the target of each connection — ``CONNECT host:port`` (HTTPS,
   WebSockets over TLS) or an absolute ``http://`` request line;
2. screens the host with the URL-fetch SSRF guard
   (``services/fetch/guard.py``: ``screen_url`` for the shape,
   ``screen_host`` resolving ONCE and denying the whole host if any address
   is private);
3. connects to one of the addresses IT approved — never resolving again —
   so the address checked is the address used (no DNS rebinding);
4. otherwise answers ``403`` and closes.

``resolve`` and ``open_connection`` are injected, so the suite runs it with a
fake resolver and a loopback test site and nothing leaves the machine.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlsplit

from src.services.fetch.guard import ResolveFn, screen_host, screen_url

logger = logging.getLogger("job360.services.browser_agent.egress")

OpenFn = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]
HEAD_LIMIT = 32 * 1024
CONNECT_TIMEOUT_S = 15.0
_HOP_BY_HOP = (b"proxy-connection", b"connection", b"keep-alive", b"proxy-authorization")


async def _default_open(host: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(host, port)


def _target(method: str, target: str) -> Optional[tuple[str, str, int, str]]:
    """(url to screen, host, port, origin-form path) of one proxy request, or None when malformed."""
    if method == "CONNECT":
        host, _, port_text = target.rpartition(":")
        host = host.strip("[]")
        if not host or not port_text.isdigit():
            return None
        port = int(port_text)
        return f"https://{host}:{port}/", host, port, ""
    parts = urlsplit(target)
    if parts.scheme != "http" or not parts.hostname:
        return None  # a proxy sees plain http here; anything else is not a browser's request
    try:
        port = parts.port or 80
    except ValueError:
        return None
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return target, parts.hostname, port, path


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:  # noqa: BLE001 - already closed
            logger.debug("egress pipe close failed", exc_info=True)


class EgressProxy:
    """A tiny screening HTTP/CONNECT proxy on 127.0.0.1 (one per browser pool)."""

    def __init__(self, resolve: ResolveFn, open_connection: Optional[OpenFn] = None) -> None:
        self._resolve = resolve
        self._open = open_connection or _default_open
        self._server: Any = None
        self.port = 0
        self.denied: list[str] = []  # host + reason of the last refusals (tests read it; never a path)

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=HEAD_LIMIT)
        self.port = int(self._server.sockets[0].getsockname()[1])
        return self.port

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _refuse(self, writer: asyncio.StreamWriter, host: str, reason: str) -> None:
        self.denied = (self.denied + [f"{host}: {reason}"])[-20:]
        logger.info("browser egress refused %s (%s)", host, reason)
        try:
            body = b"Job360's browser only reaches the public internet."
            writer.write(
                b"HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain\r\nConnection: close\r\n"
                + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
            )
            await writer.drain()
        finally:
            writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return
        lines = head.split(b"\r\n")
        try:
            method, target, version = lines[0].decode("latin-1").split(" ", 2)
        except ValueError:
            writer.close()
            return
        parsed = _target(method.upper(), target)
        if parsed is None:
            await self._refuse(writer, "?", "not a web address")
            return
        url, host, port, path = parsed
        shape = screen_url(url)
        if not shape.ok or not shape.host:
            await self._refuse(writer, host, shape.reason or "no host")
            return
        try:
            verdict = await screen_host(shape.host, port, resolve=self._resolve)
        except OSError:
            await self._refuse(writer, host, "the address could not be resolved")
            return
        if not verdict.ok or not verdict.approved:
            await self._refuse(writer, host, verdict.reason or "denied")
            return
        # PINNED: only the addresses screened above are ever used. IPv4 first, then
        # each in turn: Railway has no outbound IPv6 unless switched on, and a
        # string sort put a dual-stack site's IPv6 address first, so every such
        # site was refused (CodeRabbit on #902). Refuse only when all fail.
        up_reader = up_writer = None
        for address in sorted(verdict.approved, key=lambda a: (":" in a, a)):
            try:
                up_reader, up_writer = await asyncio.wait_for(self._open(address, port), CONNECT_TIMEOUT_S)
                break
            except (OSError, asyncio.TimeoutError):
                continue
        if up_reader is None or up_writer is None:
            await self._refuse(writer, host, "the site did not answer")
            return
        if method.upper() == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            # Origin-form request line, hop-by-hop proxy headers dropped, one request per connection.
            out = [f"{method} {path} {version}".encode("latin-1")]
            for line in lines[1:]:
                if not line:
                    continue
                name = line.split(b":", 1)[0].strip().lower()
                if name not in _HOP_BY_HOP:
                    out.append(line)
            out.append(b"Connection: close")
            up_writer.write(b"\r\n".join(out) + b"\r\n\r\n")
            await up_writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
