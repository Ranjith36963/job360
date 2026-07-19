"""SSRF guard for user-supplied webhook URLs.

A webhook channel lets a user paste an ``http(s)://`` URL that the server later
calls. Without a check, a user could point it at ``http://127.0.0.1/...`` or the
cloud metadata endpoint ``http://169.254.169.254/...`` and make the *server*
fetch internal resources on their behalf — a Server-Side Request Forgery (SSRF).

``assert_public_http_url`` rejects any URL whose host resolves to a private,
loopback, link-local, reserved, multicast, or unspecified IP address.

**Checked twice, on purpose:**
- at CREATE time — reject an obviously-internal target up front.
- at SEND time — re-resolve the host before every dispatch. This is the
  DNS-rebinding defence: a host that resolved to a public IP at create time can
  be re-pointed to ``10.0.0.5`` (or ``127.0.0.1``) later, so we must resolve
  again the moment before we actually make the request.
"""
from __future__ import annotations

import ipaddress
import socket
from typing import cast
from urllib.parse import urlparse

# Ports we allow. ``None`` (scheme default) is always allowed; an explicit port
# must be one of these standard http(s) / alt-http(s) ports.
_ALLOWED_PORTS = {80, 443, 8080, 8443}


def assert_public_http_url(url: str) -> None:
    """Raise ``ValueError`` if ``url`` is not a safe, public http(s) target.

    Rejects, in order:
    - a scheme other than ``http`` / ``https``.
    - a non-standard explicit port (only 80/443/8080/8443 allowed).
    - a host that cannot be resolved.
    - a host that resolves to ANY private / loopback / link-local / reserved /
      multicast / unspecified IP (checked across every resolved address).

    The IPv4 cloud-metadata address ``169.254.169.254`` is covered by the
    link-local check (``169.254.0.0/16``).
    """
    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https"):
        raise ValueError("webhook must be an http(s) URL")

    # Port check — ``parsed.port`` is None when no explicit port is given.
    try:
        port = parsed.port
    except ValueError as exc:
        # urlparse raises for a malformed port (e.g. "http://h:notaport/").
        raise ValueError("webhook port not allowed") from exc
    if port is not None and port not in _ALLOWED_PORTS:
        raise ValueError("webhook port not allowed")

    host = parsed.hostname
    if not host:
        raise ValueError("webhook host cannot be resolved")

    # Collect every IP the host maps to. If the host is already an IP literal
    # (e.g. "127.0.0.1" or "::1"), use it directly — no DNS lookup.
    try:
        literal = ipaddress.ip_address(host)
        candidates = [literal]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except (OSError, UnicodeError) as exc:
            raise ValueError("webhook host cannot be resolved") from exc
        candidates = []
        for info in infos:
            sockaddr = info[4]
            # getaddrinfo sockaddr[0] is always the address string for the
            # AF_INET/AF_INET6 families; cast is a no-op at runtime.
            ip_str = cast(str, sockaddr[0]).split("%", 1)[0]  # strip IPv6 scope id
            try:
                candidates.append(ipaddress.ip_address(ip_str))
            except ValueError:
                # A resolved value we cannot parse is treated as unsafe.
                raise ValueError(
                    "webhook host resolves to a private/reserved address"
                )
        if not candidates:
            raise ValueError("webhook host cannot be resolved")

    for ip in candidates:
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(
                "webhook host resolves to a private/reserved address"
            )
