"""One safe XML entry point for the hunt kit (used by We Work Remotely, Personio, Teamtailor).

Feeds are untrusted. We refuse any document that declares a DOCTYPE or ENTITY (this blocks
entity-expansion and external-entity tricks), then parse with the standard library.
Importing this module does no network and no file IO.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from typing import Any, cast

_FORBIDDEN = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)


def safe_fromstring(data: str | bytes) -> ET.Element | None:
    """Parse XML text/bytes; return None if it is empty, malformed, or declares DOCTYPE/ENTITY."""
    if not data:
        return None
    text = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    if _FORBIDDEN.search(text):
        return None
    # A pull parser fed only text whose DOCTYPE/ENTITY declarations were rejected
    # above: no entity expansion, no external entity, ever.
    parser: ET.XMLPullParser[ET.Element] = ET.XMLPullParser(events=("start",))
    root: ET.Element | None = None
    try:
        parser.feed(text)
        events = cast("Iterator[tuple[str, Any]]", parser.read_events())
        for _event, elem in events:
            if root is None and isinstance(elem, ET.Element):
                root = elem
        parser.close()
    except ET.ParseError:
        return None
    return root


# ------------------------------------------------------------------ linear scans over untrusted HTML
# A lazy "open ... close" regex (``<x>[\s\S]*?</x>``) is quadratic when a page holds many openers and no
# closer: every opener scans to the end of the page. These helpers give the same results in one pass.
def drop_blocks(
    text: str, opener: re.Pattern[str], closer_for: Callable[[str], re.Pattern[str]], repl: str = ""
) -> str:
    """``re.sub(opener + lazy-any + closer, repl, text)`` in linear time.

    ``closer_for(name)`` returns the closing-tag pattern for the lowercased group 1 of ``opener`` ("" when the
    opener has no group). A closer that is missing after one opener is missing after every later one, so that
    name is not searched for again; its opener text is kept, exactly as the regex would keep it.
    """
    out: list[str] = []
    pos = 0
    dead: set[str] = set()
    closers: dict[str, re.Pattern[str]] = {}
    while True:
        m = opener.search(text, pos)
        if m is None:
            break
        key = (m.group(1) or "").lower() if opener.groups else ""
        end = None
        if key not in dead:
            closer = closers.get(key) or closers.setdefault(key, closer_for(key))
            end = closer.search(text, m.end())
            if end is None:
                dead.add(key)
        if end is None:
            out.append(text[pos:m.start() + 1])
            pos = m.start() + 1
            continue
        out.append(text[pos:m.start()])
        out.append(repl)
        pos = end.end()
    out.append(text[pos:])
    return "".join(out)


def closed_end(text: str, closer: re.Pattern[str]) -> int:
    """End of the last ``closer`` match in ``text`` (-1 if none): a lazy ``open ... close`` match ends before it."""
    end = -1
    for m in closer.finditer(text):
        end = m.end()
    return end


def finditer_closed(rx: re.Pattern[str], closer: re.Pattern[str], text: str) -> Iterator[re.Match[str]]:
    """``rx.finditer(text)`` for a lazy pattern that must end on ``closer``, linear: it stops at the last closer."""
    end = closed_end(text, closer)
    return rx.finditer(text, 0, end) if end >= 0 else iter(())


def search_closed(rx: re.Pattern[str], closer: re.Pattern[str], text: str) -> re.Match[str] | None:
    """``rx.search(text)`` with the same stop at the last ``closer``."""
    end = closed_end(text, closer)
    return rx.search(text, 0, end) if end >= 0 else None
