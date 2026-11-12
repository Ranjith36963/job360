"""The literal term matcher for the ad's requirements in a CV review (owner 2026-11-10).

The reviewer judges meaning (does the profile support this requirement); Job360 runs no model and checks only
what needs no understanding, with :class:`Tokens`:

* :meth:`Tokens.find` (strict): the term, word for word and in order, is in the text (case and whitespace aside);
  it returns the TEXT's own spelling, so a gap is stored in the ad's exact words;
* :meth:`Tokens.match`: ``find``, or the term in another word form (a trailing s / es folds on a piece longer than
  3 characters; a two-piece term matches one run-together piece); what the CV review compares with;
* :meth:`Tokens.claims` (loose): a reader would see the term in the text, also as a spelling variant (``NodeJS`` /
  ``Node.js`` / ``Node JS``). A gap is checked with it, deliberately LOOSER than ``find``, so a tokenisation miss
  can never let a CV claim a requirement the profile lacks.

No acronym, synonym or initials inference (the reviewer judges meaning now; hard rule #28: no synonym list), no
regex on caller text (CodeQL): one linear scan per text, whole tokens only, every input capped. Tokens: NFKC,
format characters dropped, ``- / | :`` and dashes split, edge markup and a trailing possessive stripped; ``C++``,
``C#``, ``.NET``, ``Node.js`` stay single tokens. 1-2 letter terms (``Go``, ``R``, ``C``) compare original case.
"""

from __future__ import annotations

import unicodedata
from typing import Any, Optional

from src.services.applications.cv_checks import _KEYWORD_MAX_WORDS, _OUTER_PUNCT

MAX_TEXT_CHARS = 400_000  # per text; longer input is cut (a CV / ad is far shorter)
MAX_PIECES = 60_000  # per text
MAX_PIECE_CHARS = 100  # a longer "word" is junk and is dropped
_RUN_MAX = _KEYWORD_MAX_WORDS  # a spelling variant may be split over this many pieces
_SPLIT_CHARS = "/|•:-‐‑‒–—―−"
_EXTRA_EDGE = "`*_~<>@…‘’“”«»"
_LEAD_STRIP = _OUTER_PUNCT.replace(".", "") + _EXTRA_EDGE + "#"  # keep a leading "." (".NET")
_TRAIL_STRIP = _OUTER_PUNCT + _EXTRA_EDGE  # keep a trailing "#" ("C#")


class Tokens:
    """The pieces of one text: normalised, original case, and source spans (when the piece is exact)."""

    def __init__(self, text: Any) -> None:
        self.src = text[:MAX_TEXT_CHARS] if isinstance(text, str) else ""
        self.norm: list[str] = []
        self.orig: list[str] = []
        self.span: list[Optional[tuple[int, int]]] = []
        self._scan(self.src)
        self.idx = _index(self.norm)
        self._idx_orig: Optional[dict[str, list[int]]] = None
        self._loose: Optional[set[str]] = None

    def _scan(self, text: str) -> None:
        n = len(text)
        i = 0
        while i < n and len(self.norm) < MAX_PIECES:
            if text[i].isspace():
                i += 1
                continue
            j = i
            while j < n and not text[j].isspace():
                j += 1
            self._token(text[i:j], i)
            i = j

    def _token(self, raw: str, base: int) -> None:
        tok = raw.replace("’", "'")
        if not tok.isascii():
            tok = "".join(c for c in unicodedata.normalize("NFKC", tok) if unicodedata.category(c) != "Cf")
        exact = len(tok) == len(raw)
        start = 0
        for k in range(len(tok) + 1):
            if k < len(tok) and tok[k] not in _SPLIT_CHARS:
                continue
            piece = tok[start:k]
            lead = len(piece) - len(piece.lstrip(_LEAD_STRIP))
            core = piece[lead:].rstrip(_TRAIL_STRIP)
            if core.endswith("'s") and len(core) > 3:
                core = core[:-2].rstrip(_TRAIL_STRIP)
            if core and len(core) <= MAX_PIECE_CHARS:
                a = base + start + lead
                self.norm.append(core.casefold())
                self.orig.append(core)
                self.span.append((a, a + len(core)) if exact else None)
            start = k + 1

    def _pos(self, needle: list[str], case: bool) -> int:
        hay = self.orig if case else self.norm
        if case and self._idx_orig is None:
            self._idx_orig = _index(self.orig)
        index = self._idx_orig if case and self._idx_orig is not None else self.idx
        for pos in index.get(needle[0], ()) if needle else ():
            if hay[pos : pos + len(needle)] == needle:
                return pos
        return -1

    def find(self, term: Any) -> str:
        """The term, word for word, in this text: this text's own spelling of it, else ``""``."""
        t = Tokens(term if isinstance(term, str) else "")
        short = len(t.norm) == 1 and len(t.norm[0]) <= 2
        pos = self._pos(t.orig if short else t.norm, short)
        if pos < 0:
            return ""
        end = pos + len(t.norm) - 1
        a, b = self.span[pos], self.span[end]
        if all(s is not None for s in self.span[pos : end + 1]) and a is not None and b is not None:
            return " ".join(self.src[a[0] : b[1]].split())
        return " ".join(self.orig[pos : end + 1])

    def match(self, term: Any) -> str:
        """:meth:`find`, else the term in another word form (checker D3, 2026-11-12): a trailing ``s`` / ``es`` on a
        piece longer than 3 characters folds (``RAG pipelines`` / ``RAG pipeline``), and a two-piece term matches one
        piece that is both run together (``Lang Graph`` / ``LangGraph``). Whole pieces only: ``Java`` never matches
        ``JavaScript``. 1-2 letter terms stay :meth:`find`. This text's own spelling, else ``""``."""
        exact = self.find(term)
        t = Tokens(term if isinstance(term, str) else "")
        if exact or not t.norm or (len(t.norm) == 1 and len(t.norm[0]) <= 2):
            return exact
        needles = [t.norm] + ([[t.norm[0] + t.norm[1]]] if len(t.norm) == 2 else [])
        for needle in needles:
            for key in _forms(needle[0]):
                for pos in self.idx.get(key, ()):
                    hay = self.norm[pos : pos + len(needle)]
                    if len(hay) == len(needle) and all(_same(a, b) for a, b in zip(hay, needle)):
                        return " ".join(self.orig[pos : pos + len(needle)])
        return ""

    def claims(self, term: Any) -> bool:
        """A reader would see the term here: :meth:`match`, or its letters and digits run together (looser)."""
        if self.match(term):
            return True
        key = _loose_key(Tokens(term if isinstance(term, str) else "").norm)
        return bool(key) and key in self.loose

    @property
    def loose(self) -> set[str]:
        """Alphanumeric-only concatenations of 1..4 adjacent pieces: ``Node JS`` / ``NodeJS`` / ``node.js`` agree."""
        if self._loose is None:
            flat = [f for f in ("".join(c for c in t if c.isalnum()) for t in self.norm) if f]
            out: set[str] = set()
            for i in range(len(flat)):
                acc = ""
                for f in flat[i : i + _RUN_MAX]:
                    acc += f
                    out.add(acc)
            self._loose = out
        return self._loose


def _folds(long: str, short: str) -> bool:
    """``long`` is ``short`` plus a trailing ``s`` / ``es``, on a piece longer than 3 characters (never shorter
    than 3 after the fold: ``goes`` is not ``go``)."""
    return len(long) > 3 and len(short) >= 3 and long in (short + "s", short + "es")


def _same(a: str, b: str) -> bool:
    return a == b or _folds(a, b) or _folds(b, a)


def _forms(piece: str) -> list[str]:
    """Every piece that :func:`_same` could equal: the index keys to look up (no scan of the whole text)."""
    out = [piece, piece + "s", piece + "es"]
    out += [piece[:-k] for k in (1, 2) if piece.endswith("es"[2 - k :]) and _folds(piece, piece[:-k])]
    return out


def same_term(a: Any, b: Any) -> bool:
    """Two terms are one requirement for :meth:`Tokens.match` (case, a plural and a split aside): a duplicate."""
    x, y = Tokens(a).norm, Tokens(b).norm
    if len(x) < len(y):
        x, y = y, x
    if len(x) == 2 and len(y) == 1:
        x = [x[0] + x[1]]
    return bool(x) and len(x) == len(y) and all(_same(p, q) for p, q in zip(x, y))


def _index(toks: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i, t in enumerate(toks):
        out.setdefault(t, []).append(i)
    return out


def _loose_key(term: list[str]) -> str:
    """The loose form of a term, or "" when it must be matched strictly (1-2 letters, ``C++`` / ``C#`` / ``.NET``)."""
    if not term or any(c in "+#" for t in term for c in t) or any(t.startswith(".") for t in term):
        return ""
    key = "".join(c for t in term for c in t if c.isalnum())
    return key if len(key) >= 3 else ""
