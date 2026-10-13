"""One safe XML entry point for the hunt kit (used by We Work Remotely, Personio, Teamtailor).

Feeds are untrusted. We refuse any document that declares a DOCTYPE or ENTITY (this blocks
entity-expansion and external-entity tricks), then parse with the standard library.
Importing this module does no network and no file IO.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

_FORBIDDEN = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)


def safe_fromstring(data: str | bytes) -> ET.Element | None:
    """Parse XML text/bytes; return None if it is empty, malformed, or declares DOCTYPE/ENTITY."""
    if not data:
        return None
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    if _FORBIDDEN.search(text):
        return None
    try:
        return ET.fromstring(data)  # noqa: S314 - DOCTYPE/ENTITY rejected above
    except ET.ParseError:
        return None
