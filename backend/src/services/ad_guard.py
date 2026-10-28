#!/usr/bin/env python3
"""Ad guard: clean a job ad's text or HTML, scan it, say what was found.

A job ad is third-party text that the user's assistant reads next to the user's
personal data. "Ads are data, never instructions" is only as strong as the
model's adherence, so this module makes the obvious payload classes visible
deterministically. It is standard library only and is served to the hunt kit as
``kit_guard.py`` (``GET /api/hunt-kit/kit_guard.py``); the server never imports
the hunt kit, the kit downloads this file instead.

The mechanism mirrors ai-job-search ``tools/posting_guard.py`` (MIT,
github.com/ai-job-search, commit 83c788d). The regex tag scanner, the
whitespace folding and the backtracking instruction patterns of the reference
are replaced by linear scans (word by word, per line), so no regex ever
backtracks on caller text. What it does, in text mode:

  * invisible characters (zero-width, bidi controls, soft hyphen, BOM): removed
    and counted. The ones that indicate tampering (a zero-width character inside
    a word, a bidi override or isolate) are flagged
    (posting_guard.py:18-20, :168-224);
  * Unicode tag characters (U+E0000-U+E007F): a second alphabet that renders as
    nothing but reads as ASCII to a model. Removed, counted under ``tag`` and
    always flagged, except the three subdivision flag emoji (England, Scotland,
    Wales), which are matched in full and kept (posting_guard.py:21-26, :85-92);
  * text spelled in invisible characters (a run of tag characters, a run of
    variation selectors carrying one byte each): decoded, listed under
    ``invisible_text`` and scanned like hidden content (posting_guard.py:27-30,
    :96-97);
  * instruction-shaped lines aimed at an agent rather than a reader: QUOTED,
    NEVER REMOVED (posting_guard.py:31-32). The user sees them in the
    ``instruction_like`` list and the cleaned text still contains them;
  * URLs that only exist in hidden content: listed (posting_guard.py:33);
  * words mixing Latin with Cyrillic or Greek letters: listed
    (posting_guard.py:34-35).

HTML mode (``--html``, ``guard_html``) adds hidden page content, read with
``html.parser`` and an element stack instead of the reference's tag regex
(posting_guard.py:113, a pattern CodeQL flags on caller text). Mechanism kept
from the reference: an element is hidden by display:none, visibility:hidden,
opacity:0, font-size:0, a transparent colour, clip / clip-path, off-screen
positioning, a zero-size box together with overflow:hidden, the ``hidden``
attribute or aria-hidden (posting_guard.py:10-17, :99-109, :236-261); the style
is read as a browser reads it, entities decoded and CSS comments removed
(:244-251); a stack with HTML5 implied end tags keeps a stray closer from ending
a hidden element early and an open <li> or <p> from swallowing the page
(:116-121, :264-346); script, style, noscript, svg, template and comments are
dropped and scanned but not listed (:112, :278-286). Hidden content leaves the
cleaned text and is scanned for instruction-like lines and URLs; blocks are
listed under ``hidden_blocks``. CSS is read by splitting on ``;`` and ``:``.

Hidden content (invisible characters, text spelled in them) is removed, because
removal is the defense. The warning mark in the verdict is for the user and
fires only on signals that indicate targeting. The guard ADVISES; it never
blocks, never sorts and never drops an ad (posting_guard.py:37-42).

Usage:
  python kit_guard.py --text|--html [--in FILE] [--json]

Input is ``--in FILE`` or stdin, decoded as UTF-8 with replacement. Output: the
verdict line, a ``--- findings ---`` block (JSON with ``--json``), then
``--- text ---`` and the cleaned text. Exit 0 whenever the input could be read,
2 when ``--in`` cannot (posting_guard.py:44-56).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

EXCERPT = 120
LINE_MAX = 200  # quoted excerpts (posting_guard.py:70)
SCAN_CHUNK = 500  # a longer line is scanned in pieces of at most this many characters
CHUNK_OVERLAP = 100  # pieces overlap by this much, so no phrase is cut in two

ZERO_WIDTH = "\u200b\u200c\u200d\u2060\u2061\u2062\u2063\u2064"
BIDI = "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
BIDI_TAMPER = "\u202d\u202e\u2066\u2067\u2068\u2069"  # overrides and isolates
FORMAT = "\u00ad\u2028\u2029"  # soft hyphen, line and paragraph separators
BOM = "\ufeff"
# U+E0000-U+E007F: the Unicode "tags" block, one invisible code point per ASCII
# character (posting_guard.py:77-82).
TAG_CHARS = "".join(chr(c) for c in range(0xE0000, 0xE0080))
INVISIBLE = ZERO_WIDTH + BIDI + FORMAT + BOM + TAG_CHARS
_STRIP = {ord(c): None for c in INVISIBLE}
TAG_RUN = re.compile("[\U000E0000-\U000E007F]+")
# The one legitimate use of tag characters: the England, Scotland and Wales
# flags (posting_guard.py:85-92). Fixed alternatives only: linear.
RGI_TAG_FLAG = re.compile(
    "\U0001F3F4\U000E0067\U000E0062(?:\U000E0065\U000E006E\U000E0067"
    "|\U000E0073\U000E0063\U000E0074|\U000E0077\U000E006C\U000E0073)\U000E007F"
)
# Variation selectors: a lone one (the VS16 in a heart emoji) is noise, a run of
# two or more carries a payload (posting_guard.py:93-97).
VARIATION = re.compile("[\ufe00-\ufe0f\U000E0100-\U000E01EF]")
VARIATION_RUN = re.compile("[\ufe00-\ufe0f\U000E0100-\U000E01EF]{2,}")

URL = re.compile(r"https?://[^\s<>\"')\]]+")  # one character class: linear
WORD = re.compile(r"\w+")
# A word, with an inner apostrophe or hyphen kept inside it. The separator is
# never a word character, so the pattern cannot backtrack across it.
TOKEN = re.compile(r"\w+(?:['’-]\w+)*")
SPACES = re.compile(r"[ \t\xa0\f\v]+")

_JOB_TITLES = frozenset({
    "engineer", "developer", "researcher", "scientist", "architect", "specialist", "lead", "consultant",
    "manager", "analyst", "intern", "trainer", "expert", "strategist", "designer", "tester", "owner",
    "programmer",
})
_SHELL_WORDS = frozenset({"curl", "wget", "ssh", "scp"})
_ROLE_WORDS = frozenset({"assistant", "system", "user"})
_PREV = frozenset({"previous", "prior", "above", "earlier"})
_RUN_OBJECTS = frozenset({"command", "commands", "script", "code", "shell", "instruction", "instructions", "steps"})
_NOUNS_INSTR =frozenset({"instruction", "instructions", "prompt", "prompts"})


def _fold(text: str) -> str:
    """Normalise whitespace line by line (no regex runs across the whole text)."""
    out: list[str] = []
    blank = 0
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = SPACES.sub(" ", line).strip()
        if line:
            blank = 0
        else:
            blank += 1
            if blank > 1:
                continue
        out.append(line)
    return "\n".join(out).strip()


_STRIP_READ = {ord(c): None for c in ZERO_WIDTH + BIDI + BOM}


def strip_invisible(text: str) -> str:
    """Drop what hides text from a reader, keep every other character (no whitespace folding):
    tag characters (the three subdivision flags stay), zero-width and bidi controls, BOM and runs of
    two or more variation selectors (a lone one, the emoji VS16, stays). A zero-width joiner next to a
    non-letter (an emoji sequence) stays. Used on the MCP read path (``services/untrusted``)."""
    if text.isascii():
        return text
    flags = list(RGI_TAG_FLAG.finditer(text))
    pieces: list[str] = []
    last = 0
    for m in flags:
        pieces.append(_strip_read(text[last:m.start()]))
        pieces.append(m.group(0))
        last = m.end()
    pieces.append(_strip_read(text[last:]))
    return "".join(pieces)


def _strip_read(text: str) -> str:
    text = VARIATION_RUN.sub("", text)
    out: list[str] = []
    for i, ch in enumerate(text):
        if ord(ch) in _STRIP_READ or "\U000E0000" <= ch <= "\U000E007F":
            if ch == "\u200d" and not (text[i - 1:i].isalpha() and text[i + 1:i + 2].isalpha()):
                out.append(ch)  # an emoji joiner stays; one inside a word goes
            continue
        out.append(ch)
    return "".join(out)


def clean_text(text: str) -> tuple[str, dict[str, Any]]:
    """Remove invisible characters.

    Returns (text, {"invisible_chars": {category: count}, "invisible_flagged": n,
    "invisible_text": [decoded, ...]}). The flagged count covers a zero-width or
    BOM character between two letters and any bidi override or isolate. A BOM at
    offset 0, emoji joiners, RTL marks, soft hyphens, emoji flag tags and a lone
    variation selector are counted but not flagged: encoding noise, not
    tampering. Tag runs and variation runs are decoded into ``invisible_text``,
    since what they spell is the payload (posting_guard.py:168-224).
    """
    counts: dict[str, int] = {}
    flagged = 0
    for key, chars in (("zero_width", ZERO_WIDTH), ("bidi", BIDI), ("format", FORMAT), ("bom", BOM)):
        n = sum(text.count(c) for c in chars)
        if n:
            counts[key] = n
    flags = RGI_TAG_FLAG.findall(text)
    unflagged = RGI_TAG_FLAG.sub("", text)  # the text minus its legitimate flags
    tags = sum(1 for ch in unflagged if "\U000E0000" <= ch <= "\U000E007F")
    if tags:
        counts["tag"] = tags
    variation = len(VARIATION.findall(text))
    if variation:
        counts["variation"] = variation
    for i, ch in enumerate(text):
        if ch in BIDI_TAMPER:
            flagged += 1
        elif ch in ZERO_WIDTH or ch == BOM:
            before = text[i - 1] if i else ""
            after = text[i + 1] if i + 1 < len(text) else ""
            if before.isalpha() and after.isalpha():
                flagged += 1
    invisible_text: list[str] = []
    for run in TAG_RUN.findall(unflagged):
        decoded = "".join(chr(ord(c) - 0xE0000) for c in run if 0x20 <= ord(c) - 0xE0000 < 0x7F).strip()
        if decoded:
            invisible_text.append(decoded[:LINE_MAX])
    for run in VARIATION_RUN.findall(text):
        data = bytes(ord(c) - 0xFE00 if ord(c) <= 0xFE0F else ord(c) - 0xE0100 + 16 for c in run)
        decoded = "".join(c for c in data.decode("utf-8", errors="replace") if c.isprintable()).strip()
        if decoded:
            invisible_text.append(decoded[:LINE_MAX])
    text = VARIATION.sub("", text)
    if flags:
        # Strip around the flags so the visible emoji survive intact.
        pieces: list[str] = []
        last = 0
        for m in RGI_TAG_FLAG.finditer(text):
            pieces.append(text[last:m.start()].translate(_STRIP))
            pieces.append(m.group(0))
            last = m.end()
        pieces.append(text[last:].translate(_STRIP))
        text = "".join(pieces)
    else:
        text = text.translate(_STRIP)
    return _fold(text), {"invisible_chars": counts, "invisible_flagged": flagged, "invisible_text": invisible_text}


def _excerpt(line: str, at: int) -> str:
    """Up to LINE_MAX characters of ``line``, always including position ``at``.

    The quote lets the user dismiss a flag in a second, so it must show the
    match (posting_guard.py:349-365).
    """
    if len(line) <= LINE_MAX:
        return line
    lead = LINE_MAX // 3
    start = max(0, at - lead)
    end = min(len(line), start + LINE_MAX)
    start = max(0, end - LINE_MAX)
    head = "…" if start else ""
    tail = "…" if end < len(line) else ""
    return f"{head}{line[start:end]}{tail}"


def _tokens(line: str) -> list[tuple[str, int, int]]:
    """(lowercased word without apostrophes, start, end) for each word of ``line``."""
    out = []
    for m in TOKEN.finditer(line):
        word = m.group(0).lower().replace("'", "").replace("’", "")
        out.append((word, m.start(), m.end()))
    return out


def _negation_end(words: list[str], i: int) -> int | None:
    """Index after "do not" / "dont" / "never" starting at ``i``, else None."""
    if i < len(words):
        if words[i] == "never" or words[i] == "dont":
            return i + 1
        if words[i] == "do" and i + 1 < len(words) and words[i + 1] == "not":
            return i + 2
    return None


def _word_rule_hit(line: str) -> int | None:
    """Position of the first instruction-shaped phrase, matched word by word.

    The reference expresses these as regexes (posting_guard.py:124-154); here
    each is a short walk over the token list, so the cost is linear in the
    line whatever it holds.
    """
    toks = _tokens(line)
    words = [t[0] for t in toks]
    n = len(words)
    for i, w in enumerate(words):
        at = toks[i][1]
        nxt = words[i + 1] if i + 1 < n else ""
        # ignore all/any/the/previous/prior/above/earlier (:125)
        if w == "ignore" and (nxt in _PREV or nxt in {"all", "any", "the"}):
            return at
        # disregard|forget [all|any|the|your|my] previous|... instructions|prompts (:129)
        if w in {"disregard", "forget"}:
            j = i + 1
            if j < n and words[j] in {"all", "any", "the", "your", "my"}:
                j += 1
            if (j + 1 < n and (words[j] in _PREV or words[j] in {"preceding", "initial", "original", "system"})
                    and words[j + 1] in _NOUNS_INSTR):
                return at
        # you are a/an/the AI | AI assistant|agent|model | assistant | llm | language model (:130)
        if w == "you" and nxt == "are" and i + 3 < n and words[i + 2] in {"a", "an", "the"}:
            third, fourth = words[i + 3], words[i + 4] if i + 4 < n else ""
            if third in {"ai", "assistant", "llm"} or (third == "language" and fourth == "model"):
                return at
        # system prompt (:131)
        if w == "system" and nxt == "prompt":
            return at
        # (do not|dont|never) (tell|inform) the user, not "the user's" (:137)
        end = _negation_end(words, i)
        if end is not None and end + 2 < n and words[end] in {"tell", "inform"}:
            if words[end + 1] == "the" and words[end + 2] == "user":
                return at
        # (do not|dont|never) (mention|reveal|disclose) <this/it/the above...> ... to the user (:140)
        if end is not None and end + 1 < n and words[end] in {"mention", "reveal", "disclose"}:
            if _demonstrative_to_user(line, toks, end + 1):
                return at
        # (run|execute) the following command|script|... (:141)
        if (w in {"run", "execute"} and nxt == "the" and i + 3 < n and words[i + 2] == "following"
                and words[i + 3] in _RUN_OBJECTS):
            return at
        # as an AI, unless a job title follows within a few words (:144)
        if w == "as" and nxt == "an" and i + 2 < n and words[i + 2] == "ai":
            titled = any(x.rstrip("s") in _JOB_TITLES or x in _JOB_TITLES for x in words[i + 3:i + 7])
            if not titled:
                return at
        # shell and tool words (:149, :153)
        if w in {"tool_use", "function_call"} or any(part in _SHELL_WORDS for part in w.split("-")):
            return at
    return None


def _demonstrative_to_user(line: str, toks: list[tuple[str, int, int]], i: int) -> bool:
    """True for "this|these|that|it|any of this|the above/following/previous" then, within
    30 characters and the same sentence, "to the user" (posting_guard.py:140)."""
    words = [t[0] for t in toks]
    n = len(words)
    if words[i] in {"this", "these", "that", "it"}:
        j = i + 1
    elif words[i] == "any" and i + 2 < n and words[i + 1] == "of" and words[i + 2] == "this":
        j = i + 3
    elif words[i] == "the" and i + 1 < n and words[i + 1] in {"above", "following", "previous"}:
        j = i + 2
    else:
        return False
    limit = toks[j - 1][2] + 30
    for k in range(j, n - 2):
        if toks[k][1] > limit:
            return False
        if "." in line[toks[k - 1][2]:toks[k][1]]:
            return False
        if words[k] == "to" and words[k + 1] == "the" and words[k + 2] == "user":
            return True
    return False


def _substring_rule_hit(line: str) -> int | None:
    """Fixed-text markers (posting_guard.py:145-152): found with str.find, never a regex."""
    low = line.lower()
    for marker in ("<|", "[inst]", "settings.json", "allowed-tools"):
        at = low.find(marker)
        if at >= 0:
            return at
    at = low.find(".env")
    while at >= 0:
        after = low[at + 4:at + 5]
        if not (after.isalnum() or after == "_"):
            return at
        at = low.find(".env", at + 1)
    at = low.find("###")
    while at >= 0:
        rest = low[at + 3:].lstrip()
        if rest.startswith("system") or rest.startswith("instruction"):
            return at
        at = low.find("###", at + 1)
    # a line that opens with "assistant:", "system:" or "user:" (:145)
    stripped = line.lstrip()
    head = TOKEN.match(stripped)
    if head and head.group(0).lower() in _ROLE_WORDS and stripped[head.end():].lstrip().startswith(":"):
        return len(line) - len(stripped)
    return None


def _instruction_hit(line: str) -> int | None:
    hit = _substring_rule_hit(line)
    word_hit = _word_rule_hit(line)
    if hit is None:
        return word_hit
    return hit if word_hit is None else min(hit, word_hit)


def _chunks(line: str) -> list[tuple[int, str]]:
    """(offset, piece) pairs of at most SCAN_CHUNK characters, cut at a space when there is one.

    Neighbouring pieces overlap by CHUNK_OVERLAP characters, so a phrase that straddles a cut
    ("ignore all | previous instructions") is still whole in one piece.
    """
    if len(line) <= SCAN_CHUNK:
        return [(0, line)]
    pieces: list[tuple[int, str]] = []
    start = 0
    while start < len(line):
        end = min(len(line), start + SCAN_CHUNK)
        if end < len(line):
            cut = line.rfind(" ", start + SCAN_CHUNK // 2, end)
            if cut > start:
                end = cut
        pieces.append((start, line[start:end]))
        if end >= len(line):
            break
        start = end - CHUNK_OVERLAP  # end - start >= SCAN_CHUNK // 2 > CHUNK_OVERLAP: always moves on
    return pieces


def scan(visible: str, hidden: str = "") -> dict[str, Any]:
    """Instruction-shaped lines, hidden URLs, visible URL count, mixed-script words.

    Instruction lines are QUOTED, never removed from ``visible``
    (posting_guard.py:368-393).
    """
    seen: set[str] = set()
    instruction_like: list[str] = []
    for line in visible.splitlines() + hidden.splitlines():
        stripped = line.strip()
        if not stripped or stripped in seen:
            continue
        for offset, piece in _chunks(stripped):
            at = _instruction_hit(piece)
            if at is not None:
                seen.add(stripped)
                instruction_like.append(_excerpt(stripped, offset + at))
                break
    hidden_urls: list[str] = []
    for url in URL.findall(hidden):
        if url not in hidden_urls:
            hidden_urls.append(url)
    homoglyphs: list[str] = []
    for word in WORD.findall(visible + "\n" + hidden):
        if word not in homoglyphs and _mixed_script(word):
            homoglyphs.append(word)
    return {
        "instruction_like": instruction_like,
        "hidden_urls": hidden_urls,
        "visible_url_count": len(URL.findall(visible)),
        "homoglyph_runs": homoglyphs,
    }


def _mixed_script(word: str) -> bool:
    latin = confusable = False
    for ch in word:
        o = ord(ch)
        if "A" <= ch <= "Z" or "a" <= ch <= "z":
            latin = True
        elif 0x0370 <= o <= 0x03FF or 0x0400 <= o <= 0x04FF:  # Greek and Coptic, Cyrillic (:157)
            confusable = True
    return latin and confusable


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def verdict(findings: dict[str, Any]) -> str:
    """One line. The warning mark fires only on targeting signals; hidden blocks and
    hidden URLs are context (posting_guard.py:400-432). Advises, never blocks."""
    alarms = []
    n = len(findings.get("instruction_like") or [])
    if n:
        alarms.append(_plural(n, "instruction-like line", "instruction-like lines"))
    n = int(findings.get("invisible_flagged") or 0)
    if n:
        alarms.append(_plural(n, "invisible character inside words", "invisible characters inside words"))
    n = int((findings.get("invisible_chars") or {}).get("tag") or 0)
    if n:
        hidden = "(text hidden from the reader)"
        alarms.append(_plural(n, f"tag character {hidden}", f"tag characters {hidden}"))
    n = len(findings.get("invisible_text") or [])
    if n:
        alarms.append(_plural(n, "string spelled in invisible characters", "strings spelled in invisible characters"))
    n = len(findings.get("homoglyph_runs") or [])
    if n:
        alarms.append(_plural(n, "mixed-script word", "mixed-script words"))

    context = []
    n = len(findings.get("hidden_blocks") or [])
    if n:
        context.append(_plural(n, "hidden block", "hidden blocks"))
    n = len(findings.get("hidden_urls") or [])
    if n:
        context.append(_plural(n, "hidden URL", "hidden URLs"))
    removed = " and ".join(context)

    if alarms:
        line = "ad_guard: ⚠ " + ", ".join(alarms) + " - review before continuing"
        return f"{line} ({removed} removed)" if removed else line
    if removed:
        return f"ad_guard: clean ({removed} removed, none instruction-like)"
    return "ad_guard: clean"


_VOID = frozenset({"br", "img", "input", "meta", "link", "hr", "wbr", "source", "area", "base", "col", "embed",
                   "param", "track"})
_BLOCK = frozenset({"p", "div", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "section",
                    "article", "header", "footer", "table", "thead", "tbody", "blockquote", "pre", "dd", "dt", "dl",
                    "nav", "main", "aside", "figure", "form", "fieldset", "legend", "address", "hr", "td", "th",
                    "option"})  # posting_guard.py:116-118
_IMPLIED_END = frozenset({"p", "li", "dd", "dt", "tr", "td", "th", "option"})  # :119
_CLOSES_P = _BLOCK - {"br"}  # :120
_DROP = frozenset({"script", "style", "noscript", "svg", "template"})  # :112
_UNITS = ("rem", "px", "pt", "em", "%", "vw", "vh", "ex", "ch", "cm", "mm", "in", "pc")
MAX_BLOCKS = 50  # hidden blocks listed; a hostile page cannot flood the report


def _strip_css_comments(css: str) -> str:
    """Remove /* ... */ (an unterminated one runs to the end, as in a browser)."""
    out: list[str] = []
    i = 0
    while True:
        j = css.find("/*", i)
        if j < 0:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:j])
        k = css.find("*/", j + 2)
        if k < 0:
            return "".join(out)
        i = k + 2


def _number(value: str) -> float | None:
    v = value.strip()
    if not v or len(v) > 40:
        return None
    for unit in _UNITS:
        if v.endswith(unit):
            v = v[:-len(unit)]
            break
    try:
        return float(v)
    except ValueError:
        return None


def _style_hides(style: str) -> bool:
    """True when a declaration hides the box (posting_guard.py:99-109, :240-261).

    Declarations are split on ``;`` and ``:``. A collapsed box hides only
    together with overflow:hidden; either half alone is ordinary layout CSS.
    """
    zero_box = clipped = False
    for decl in _strip_css_comments(style).split(";"):
        name, sep, value = decl.partition(":")
        if not sep:
            continue
        name = name.strip().lower()
        value = value.replace("!important", "").strip().lower()
        if len(value) > 200:
            continue
        if name == "display" and value == "none":
            return True
        if name == "visibility" and value in ("hidden", "collapse"):
            return True
        if name in ("opacity", "font-size") and _number(value) == 0:
            return True
        if name == "color" and value == "transparent":
            return True
        if name == "clip" and value.startswith("rect("):
            first = value[5:].replace(",", " ").split(None, 1)
            if first and _number(first[0]) == 0:
                return True
        if name == "clip-path" and value.startswith("inset(") and value[6:].lstrip().startswith("100%"):
            return True
        if name in ("left", "top", "text-indent") and value.endswith("px"):
            num = _number(value)
            if num is not None and num <= -1000:
                return True
        if name in ("height", "width", "max-height", "max-width") and _number(value) == 0:
            zero_box = True
        if name in ("overflow", "overflow-x", "overflow-y") and "hidden" in value.split():
            clipped = True
    return zero_box and clipped


def _is_hidden(attrs: list[tuple[str, str | None]]) -> bool:
    found: dict[str, str] = {}
    for key, val in attrs:
        found.setdefault(key, val or "")  # the first of a duplicated attribute wins
    if "hidden" in found or found.get("aria-hidden", "").strip().lower() == "true":
        return True
    return _style_hides(found.get("style", ""))


class _Walker(HTMLParser):
    """Visible text out, hidden content aside. One pass, linear: a count of open
    names answers "is this closer open?" without scanning the stack."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)  # entities decoded once, in text and attributes
        self.visible: list[str] = []
        self.hidden_buf: list[str] = []
        self.hidden_parts: list[str] = []
        self.hidden_blocks: list[str] = []
        self.stack: list[str] = []
        self.open: dict[str, int] = {}
        self.hidden_at: int | None = None  # stack index of the element that began the hidden run
        self.listed = True

    def _emit(self, text: str) -> None:
        (self.visible if self.hidden_at is None else self.hidden_buf).append(text)

    def flush(self) -> None:
        block = _fold("".join(self.hidden_buf))
        self.hidden_buf.clear()
        self.hidden_at = None
        if block:
            if self.listed and len(self.hidden_blocks) < MAX_BLOCKS:
                # Cleaned before cutting: the excerpt carries no invisible characters.
                self.hidden_blocks.append(clean_text(block)[0][:EXCERPT])
            self.hidden_parts.append(block)

    def _pop_to(self, index: int) -> None:
        for name in self.stack[index:]:
            self.open[name] -= 1
        del self.stack[index:]
        if self.hidden_at is not None and self.hidden_at >= len(self.stack):
            self.flush()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK:
            self._emit("\n")
        if tag in _VOID:
            return
        if self.stack and ((tag in _IMPLIED_END and self.stack[-1] == tag)
                           or (tag in _CLOSES_P and self.stack[-1] == "p")):
            self._pop_to(len(self.stack) - 1)
        self.stack.append(tag)
        self.open[tag] = self.open.get(tag, 0) + 1
        if self.hidden_at is None:
            if tag in _DROP:
                self.hidden_at, self.listed = len(self.stack) - 1, False
            elif _is_hidden(attrs):
                self.hidden_at, self.listed = len(self.stack) - 1, True

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)  # "/>" on a non-void tag is ignored, as browsers do

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK:
            self._emit("\n")
        if not self.open.get(tag):
            return  # a closer with no opener is ignored
        index = len(self.stack) - 1
        while self.stack[index] != tag:
            index -= 1
        self._pop_to(index)

    def handle_data(self, data: str) -> None:
        self._emit(data)

    def handle_comment(self, data: str) -> None:
        self.hidden_parts.append(data)  # scanned, never listed


def clean_html(markup: str) -> tuple[str, dict[str, Any]]:
    """Visible text and the hidden content that was removed.

    Returns (text, {"hidden_blocks": excerpts of hidden elements, "hidden_text":
    everything removed, for scanning and not reported}), as
    posting_guard.py:264-346 does, on html.parser instead of its tag regex.
    Unterminated hidden elements, comments and script blocks hide to the end.
    """
    walker = _Walker()
    walker.feed(markup)
    walker.close()
    if walker.hidden_at is not None:
        walker.flush()
    return _fold("".join(walker.visible)), {
        "hidden_blocks": walker.hidden_blocks,
        "hidden_text": "\n".join(walker.hidden_parts),
    }


def _guard(text: str, hidden_text: str, hidden_blocks: list[str]) -> tuple[str, dict[str, Any]]:
    text, tf = clean_text(text)
    hidden_text, htf = clean_text(hidden_text)
    # Invisible characters inside hidden content count and alarm like visible
    # ones (posting_guard.py:446-454); what they spell is hidden content too.
    for key, n in htf["invisible_chars"].items():
        tf["invisible_chars"][key] = tf["invisible_chars"].get(key, 0) + n
    tf["invisible_flagged"] += htf["invisible_flagged"]
    tf["invisible_text"] += htf["invisible_text"]
    hidden = "\n".join([hidden_text, *tf["invisible_text"]])
    findings: dict[str, Any] = {"hidden_blocks": hidden_blocks, **tf, **scan(text, hidden)}
    findings["verdict"] = verdict(findings)
    return text, findings


def guard(raw: str) -> tuple[str, dict[str, Any]]:
    """Run the whole gate on plain text. Returns (cleaned text, findings with a verdict)."""
    return _guard(raw, "", [])


def guard_html(raw: str) -> tuple[str, dict[str, Any]]:
    """Run the whole gate on a page's HTML: hidden content is removed, then scanned."""
    text, hf = clean_html(raw)
    return _guard(text, hf["hidden_text"], hf["hidden_blocks"])


def _plain_findings(findings: dict[str, Any]) -> str:
    lines = []
    for key in ("hidden_blocks", "instruction_like", "invisible_text", "hidden_urls", "homoglyph_runs"):
        items = findings[key]
        lines.append(f"{key} ({len(items)}):")
        lines.extend(f'  - "{item}"' for item in items)
    chars = ", ".join(f"{k}={v}" for k, v in findings["invisible_chars"].items())
    lines.append("invisible_chars: " + (chars or "none"))
    lines.append(f"invisible_flagged: {findings['invisible_flagged']}")
    lines.append(f"visible_url_count: {findings['visible_url_count']}")
    return "\n".join(lines)


def _force_utf8_output() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)  # absent on a StringIO under test
        if reconfigure:
            reconfigure(encoding="utf-8")


def _read_input(path: Path | None) -> str:
    if path is not None:
        data = path.read_bytes()
    else:
        buffer = getattr(sys.stdin, "buffer", None)
        if buffer is None:
            return str(sys.stdin.read())
        data = buffer.read()
    return data.decode("utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    parser = argparse.ArgumentParser(prog="kit_guard.py", description="Integrity gate for a job ad's text.")
    parser.add_argument("--in", dest="infile", type=Path, default=None, help="input file (default: stdin)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--text", action="store_true", help="plain text input (the default)")
    mode.add_argument("--html", action="store_true", help="a saved page's HTML: hidden content is removed")
    parser.add_argument("--json", action="store_true", help="print findings as JSON")
    args = parser.parse_args(argv)

    try:
        raw = _read_input(args.infile)
    except OSError as exc:
        print(f"ad_guard: cannot read input: {exc}", file=sys.stderr)
        return 2

    cleaned, findings = (guard_html if args.html else guard)(raw)
    print(findings["verdict"])
    print("--- findings ---")
    print(json.dumps(findings, ensure_ascii=False, indent=2) if args.json else _plain_findings(findings))
    print("--- text ---")
    print(cleaned)
    return 0


if __name__ == "__main__":
    sys.exit(main())
