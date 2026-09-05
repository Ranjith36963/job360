"""The three-rung extraction ladder (spec R8) — standard library only.

1. JSON-LD ``schema.org/JobPosting`` — structured title/company/location, plus
   a description that is itself HTML and needs its own tag-strip.
2. Meta tags — ``og:title``/``<title>``, ``og:site_name``, ``og:description``.
3. A light readability heuristic — the container subtree with the most text
   that is not mostly link text.

Rungs are tried in order and MERGED, not replaced: a later rung only fills a
field the earlier rungs left empty. No dependency (spec R8's "why no
dependency" — JSON-LD is the extractor that actually works here; a C parser
fed attacker-controlled bytes is a bigger surface than the guard this slice
exists to close).

Security (A10/S10): every field returned is TEXT ONLY. Tags are stripped,
entities are unescaped exactly once via ``html.parser``'s own
``convert_charrefs``, and — because that unescape can turn a literal
``&lt;script&gt;`` back into ``<script>`` as plain text — every field goes
through one final scrub that removes any stray ``<``/``>`` left over, so no
shape that reads as a tag can ever survive into the response even when a
hostile page spends its whole body trying to reintroduce one.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Optional

# Same caps BringJobRequest already enforces — settings-backed (C4: this used
# to import api.routes.bring directly, dragging FastAPI into a service module,
# the only such import in the backend). Aliased under the historical names so
# the rest of this file needs no further edits.
from src.core.settings import (
    BRING_MAX_FIELD as _MAX_FIELD,
)
from src.core.settings import (
    BRING_MAX_TEXT as _MAX_TEXT,
)
from src.core.settings import (
    URL_FETCH_MAX_JSONLD_BYTES as _MAX_JSONLD_BYTES,
)
from src.utils.loop_guard import cpu_bound

# Elements whose text (and everything nested inside) must never reach the
# extracted output — scripts/styles are the security-sensitive ones; the rest
# are structural noise (nav/chrome) the heuristic rung must not score.
_EXCLUDED_TAGS = frozenset(
    {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe", "template"}
)

# A block-ish set of tags that each get their OWN scored text frame in the
# heuristic rung. Everything else (span, strong, h1, img...) still
# contributes its text to whichever of these frames are currently open.
_CONTAINER_TAGS = frozenset(
    {"html", "body", "main", "article", "section", "div", "ul", "ol", "table", "blockquote", "li", "td", "p"}
)

# A container whose text is more than this fraction link text is treated as
# navigation, not content, and is never chosen as the "best" block.
_LINK_RATIO_CEILING = 0.6


@dataclass
class ExtractResult:
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    found: list[str] = field(default_factory=list)
    source_hint: str = ""


def _clean_text(text: str, *, max_len: int) -> str:
    """Collapse whitespace, scrub any stray angle bracket, cap the length.

    The angle-bracket scrub is deliberate and unconditional (A10): even a
    LEGITIMATE decoded entity that happens to spell out ``<`` must not
    survive, because the response is rendered as text and a "no raw HTML
    ever" guarantee that has an exception for "well, this one came from an
    entity" is not a guarantee.
    """
    collapsed = " ".join(text.split())
    scrubbed = collapsed.replace("<", "").replace(">", "")
    return scrubbed[:max_len].strip()


class _TextOnlyParser(HTMLParser):
    """Strip tags/scripts/styles, unescape entities once (convert_charrefs is
    the html.parser default), return plain text. Used for JSON-LD
    descriptions and meta content — small, already-scoped HTML fragments
    with no need for the heuristic's container scoring.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._exclude_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in _EXCLUDED_TAGS:
            self._exclude_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        return None

    def handle_endtag(self, tag: str) -> None:
        if tag in _EXCLUDED_TAGS and self._exclude_depth > 0:
            self._exclude_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._exclude_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def strip_html_to_text(html: str, *, max_len: int = _MAX_TEXT) -> str:
    """Plain text from an HTML fragment: tags gone, entities unescaped once,
    no stray angle brackets, whitespace collapsed, length-capped.
    """
    parser = _TextOnlyParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — a malformed fragment must not crash the route
        pass
    return _clean_text(parser.text(), max_len=max_len)


# ---------------------------------------------------------------------------
# Rung 1 — JSON-LD schema.org/JobPosting
# ---------------------------------------------------------------------------


class _JsonLdScriptParser(HTMLParser):
    """Collect the raw text of every ``<script type="application/ld+json">``
    block. A dedicated tiny parser rather than a regex — script content can
    legitimately contain ``</scr`` inside a JS string, and html.parser
    already knows how to find the real terminator (CDATA content rules).
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._capture = False
        self._buf: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag == "script":
            attr_map = dict(attrs)
            self._capture = (attr_map.get("type") or "").strip().lower() == "application/ld+json"
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._capture:
            self.blocks.append("".join(self._buf))
        self._capture = False

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buf.append(data)


def _iter_json_ld_objects(html: str) -> list[dict[str, Any]]:
    parser = _JsonLdScriptParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup must not crash extraction
        pass

    objects: list[dict[str, Any]] = []
    for block in parser.blocks:
        # B3 — a hostile ``"["*60000`` bomb inside the script tag makes
        # ``json.loads`` raise ``RecursionError`` (a ``RuntimeError``, NOT a
        # ``ValueError``) — it used to escape uncaught and 500 the route.
        # Skipping any block over the byte cap BEFORE parsing bounds the CPU
        # spent even reaching that recursion limit in the first place.
        if len(block.encode("utf-8", errors="ignore")) > _MAX_JSONLD_BYTES:
            continue
        try:
            payload = json.loads(block)
        except (ValueError, RecursionError):
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            if "@graph" in candidate and isinstance(candidate["@graph"], list):
                objects.extend(m for m in candidate["@graph"] if isinstance(m, dict))
            else:
                objects.append(candidate)
    return objects


def _is_job_posting(obj: dict[str, Any]) -> bool:
    kind = obj.get("@type")
    if isinstance(kind, str):
        return kind == "JobPosting"
    if isinstance(kind, list):
        return "JobPosting" in kind
    return False


def _job_posting_location(obj: dict[str, Any]) -> str:
    loc = obj.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return ""
    address = loc.get("address")
    if not isinstance(address, dict):
        return ""
    parts = [
        str(address.get(key) or "").strip()
        for key in ("addressLocality", "addressRegion", "addressCountry")
    ]
    return ", ".join(p for p in parts if p)


def _extract_json_ld(html: str) -> ExtractResult:
    result = ExtractResult()
    posting = next((o for o in _iter_json_ld_objects(html) if _is_job_posting(o)), None)
    if posting is None:
        return result

    title = str(posting.get("title") or "").strip()
    org = posting.get("hiringOrganization")
    company = str(org.get("name") or "").strip() if isinstance(org, dict) else ""
    location = _job_posting_location(posting)
    description = strip_html_to_text(str(posting.get("description") or ""))

    if title:
        result.title = _clean_text(title, max_len=_MAX_FIELD)
    if company:
        result.company = _clean_text(company, max_len=_MAX_FIELD)
    if location:
        result.location = _clean_text(location, max_len=_MAX_FIELD)
    if description:
        result.description = description
    if any((result.title, result.company, result.location, result.description)):
        result.source_hint = "json_ld"
    return result


# ---------------------------------------------------------------------------
# Rung 2 — meta tags
# ---------------------------------------------------------------------------


class _MetaParser(HTMLParser):
    """Collect ``<meta property=... content=...>`` and the bare ``<title>``
    text — stops descending into the body (nothing meta-relevant lives
    there) once ``</head>`` is seen, which also bounds the work on a huge page.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False
        self._done = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if self._done:
            return
        if tag == "meta":
            attr_map = dict(attrs)
            key = attr_map.get("property") or attr_map.get("name") or ""
            content = attr_map.get("content") or ""
            if key and content:
                self.meta[key.strip().lower()] = content
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._in_title and not self._done:
            self.title += data


def _extract_meta(html: str) -> ExtractResult:
    result = ExtractResult()
    parser = _MetaParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 — malformed markup must not crash extraction
        pass

    title = (parser.meta.get("og:title") or parser.title or "").strip()
    company = (parser.meta.get("og:site_name") or "").strip()
    description = (parser.meta.get("og:description") or "").strip()

    if title:
        result.title = _clean_text(title, max_len=_MAX_FIELD)
    if company:
        result.company = _clean_text(company, max_len=_MAX_FIELD)
    if description:
        result.description = _clean_text(description, max_len=_MAX_TEXT)
    if any((result.title, result.company, result.description)):
        result.source_hint = "meta"
    return result


# ---------------------------------------------------------------------------
# Rung 3 — the heuristic
# ---------------------------------------------------------------------------


class _ExtractBudgetExceeded(Exception):  # noqa: N818 — internal control-flow signal, not a public error
    """Raised to stop parsing early once the real-time budget is spent."""


# B4 — how many of the INNERMOST open container frames actually copy a text
# chunk into their own ``text_parts`` list. Every open frame still gets cheap
# O(1) integer counters (``total_len``/``link_len``, needed for scoring), but
# only the last few frames pay for an actual string copy: without this cap, a
# single ``handle_data`` call inside a document nested ``max_depth`` (200)
# deep copies that SAME chunk into all 200 frames — a 2 MiB document nested
# that deep could allocate on the order of 200x its own size (~400 MB,
# measured). A typical real container nesting (html>body>main>article>
# section>div>p) is well under this depth, so ordinary pages lose nothing.
_MAX_TEXT_ACCUM_FRAMES = 8


class _Frame:
    __slots__ = ("tag", "text_parts", "link_len", "total_len")

    def __init__(self, tag: str) -> None:
        self.tag = tag
        self.text_parts: list[str] = []
        self.link_len = 0
        self.total_len = 0


class _HeuristicParser(HTMLParser):
    """A light readability pass: drop chrome/script/style, keep text per
    block container, and remember the container whose own text is largest
    while not being mostly link text.

    Bounded on purpose (A7 / the nesting-bomb attack): the container-frame
    stack never grows past ``max_depth`` (deeper tags simply don't get their
    own frame — their text still flows to the deepest TRACKED frame), and a
    real-time budget check aborts parsing early, returning whatever the best
    candidate was so far, rather than hang on a pathological document.
    """

    def __init__(self, *, max_depth: int, budget_s: float) -> None:
        super().__init__(convert_charrefs=True)
        self._max_depth = max(1, max_depth)
        self._deadline = time.monotonic() + max(0.05, budget_s)
        self._exclude_stack: list[str] = []
        self._link_depth = 0
        self._frames: list[_Frame] = []
        self._ops = 0
        self.best_text = ""
        self.best_len = 0
        # A whole-document fallback so a page with no candidate CONTAINER tag
        # at all (bare text, no markup) still yields something.
        self._root_parts: list[str] = []

    def _check_budget(self) -> None:
        self._ops += 1
        if self._ops % 256 == 0 and time.monotonic() > self._deadline:
            raise _ExtractBudgetExceeded()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self._check_budget()
        if self._exclude_stack:
            if tag in _EXCLUDED_TAGS:
                self._exclude_stack.append(tag)
            return
        if tag in _EXCLUDED_TAGS:
            self._exclude_stack.append(tag)
            return
        if tag == "a":
            self._link_depth += 1
        if tag in _CONTAINER_TAGS and len(self._frames) < self._max_depth:
            self._frames.append(_Frame(tag))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        self._check_budget()

    def handle_endtag(self, tag: str) -> None:
        self._check_budget()
        if self._exclude_stack:
            if self._exclude_stack[-1] == tag:
                self._exclude_stack.pop()
            return
        if tag == "a" and self._link_depth > 0:
            self._link_depth -= 1
        if tag in _CONTAINER_TAGS and self._frames and self._frames[-1].tag == tag:
            frame = self._frames.pop()
            if frame.total_len > self.best_len and frame.link_len <= frame.total_len * _LINK_RATIO_CEILING:
                self.best_len = frame.total_len
                self.best_text = "".join(frame.text_parts)

    def handle_data(self, data: str) -> None:
        self._check_budget()
        if self._exclude_stack:
            return
        self._root_parts.append(data)
        is_link_text = self._link_depth > 0
        # B4 — only the innermost _MAX_TEXT_ACCUM_FRAMES frames copy the text
        # itself; every frame still gets its counters (see class docstring).
        for frame in self._frames[-_MAX_TEXT_ACCUM_FRAMES:]:
            frame.text_parts.append(data)
        for frame in self._frames:
            frame.total_len += len(data)
            if is_link_text:
                frame.link_len += len(data)

    def result_text(self) -> str:
        if self.best_text:
            return self.best_text
        return "".join(self._root_parts)


def _extract_heuristic(html: str, *, max_depth: int, budget_s: float) -> ExtractResult:
    result = ExtractResult()
    parser = _HeuristicParser(max_depth=max_depth, budget_s=budget_s)
    try:
        parser.feed(html)
        parser.close()
    except _ExtractBudgetExceeded:
        pass
    except Exception:  # noqa: BLE001 — malformed markup must not crash extraction
        pass

    text = _clean_text(parser.result_text(), max_len=_MAX_TEXT)
    if text:
        result.description = text
        result.source_hint = "heuristic"
    return result


# ---------------------------------------------------------------------------
# The ladder — tried in order, merged, not replaced.
# ---------------------------------------------------------------------------


@cpu_bound
def extract_job_fields(html: str, *, max_depth: int, budget_s: float) -> ExtractResult:
    """Run the three-rung ladder and merge the results.

    ``source_hint`` reports whichever rung supplied ``description`` (the
    heaviest field — the ladder exists mainly to fill it); if no rung filled
    it, whichever rung filled anything at all, in JSON-LD > meta > heuristic
    priority.

    C8 — ``max_depth``/``budget_s`` are required keyword-only, not defaulted
    here: their real defaults already live in ``settings.URL_FETCH_MAX_HTML_DEPTH``
    / ``settings.URL_FETCH_EXTRACT_BUDGET_S``, and a second, silently-diverging
    default on this function was a place the two could drift apart unnoticed.

    B4 — ``@cpu_bound``: three html.parser passes over up to a 2 MiB document
    are measured at ~3s, run synchronously. Called straight from ``async def
    fetch_url_route`` (as it used to be) this freezes the WHOLE event loop —
    every other user's request — for the duration; the caller MUST run this
    via ``asyncio.to_thread`` (see ``api/routes/bring.py``).
    """
    rungs = [
        _extract_json_ld(html),
        _extract_meta(html),
        _extract_heuristic(html, max_depth=max_depth, budget_s=budget_s),
    ]

    merged = ExtractResult()
    description_source = ""
    any_source = ""
    for rung in rungs:
        if rung.title and not merged.title:
            merged.title = rung.title
        if rung.company and not merged.company:
            merged.company = rung.company
        if rung.location and not merged.location:
            merged.location = rung.location
        if rung.description and not merged.description:
            merged.description = rung.description
            description_source = rung.source_hint
        if rung.source_hint and not any_source:
            any_source = rung.source_hint

    merged.source_hint = description_source or any_source
    for field_name in ("title", "company", "location", "description"):
        if getattr(merged, field_name):
            merged.found.append(field_name)
    return merged
