"""JD coverage, the pure core: do the ad's EXACT words reach the CV, and only where the profile says them?

Job360 runs no model. The assistant saves the ad's requirements (``save_fit`` ``jd_requirements``, items
``{"text", "kind"}``); this module only compares three texts, token by token:

* the frozen ad (``applications.job_description_snapshot``): a requirement counts only if its words occur there
  literally (case and whitespace aside); it is then reported in the AD's spelling;
* the profile (:func:`cv_checks.profile_source_text`): a requirement is SUPPORTED when it occurs there literally
  (or as a spelling variant: ``NodeJS`` / ``Node.js``), or by the two ACRONYM rules below;
* the CV text: a supported requirement is COVERED only when it occurs there literally (strict). A requirement the
  profile lacks is a GAP: it must not be in the CV at all, and that check is deliberately LOOSER than coverage, so
  a tokenisation miss can never turn a claimed gap into ``ok``.

Acronym support (never fuzzy, and the answer names the profile phrase that supplied it, ``support``):
* the ad writes an ALL-CAPS acronym of 3-5 letters (optional plural s): a run of 3+ consecutive CAPITALISED
  profile words with those initials (broken by any non-capitalised word, ``.,;:`` and a new line / field), or a
  profile token written in capitals that is the same acronym;
* the ad writes a phrase: a profile token written in CAPITALS (optional plural s) equal to its initials.

Known misses (they fail closed as gaps or as uncovered, never as claimed): synonyms and renamings (K8s for
Kubernetes), Cyrillic / Greek look-alike letters, a term the ad spells differently from the requirement. No
synonym list (hard rule #28), no regex on caller text (CodeQL): one linear scan per text, whole tokens only,
every input capped. Tokens: NFKC, format characters dropped, ``- / | :`` and dashes split, edge markup and a
trailing possessive stripped; ``C++``, ``C#``, ``.NET``, ``Node.js`` stay single tokens. 1-2 letter terms
(``Go``, ``R``, ``C``) compare original case on all three texts.
"""

from __future__ import annotations

import math
import unicodedata
from typing import Any, Optional

from src.services.applications.cv_checks import (
    _KEYWORD_MAX_ITEMS,
    _KEYWORD_MAX_WORDS,
    _OUTER_PUNCT,
    profile_source_text,
)

# Closed problem codes (report order). ``BLOCKING`` ones make ``ok`` false on their own.
PROBLEM_CODES = (
    "no_ad_terms",
    "no_must_haves",
    "term_not_in_ad",
    "term_too_long",
    "jd_coverage_low",
    "gap_claimed_in_cv",
)
BLOCKING = frozenset({"no_ad_terms", "no_must_haves", "jd_coverage_low", "gap_claimed_in_cv"})

DEFAULT_THRESHOLD = 0.8
MIN_THRESHOLD = 0.5
MAX_TEXT_CHARS = 400_000  # per text; longer input is cut (a CV / ad / profile is far shorter)
MAX_PIECES = 60_000  # per text
MAX_TERM_CHARS = 200  # = settings.FIT_GAP_TEXT_MAX_CHARS
MAX_PIECE_CHARS = 100  # a longer "word" is junk and is dropped
_RUN_MAX = _KEYWORD_MAX_WORDS  # a spelling variant may be split over this many pieces
_ACRONYM_MIN, _ACRONYM_MAX = 3, 5
_SPLIT_CHARS = "/|•:-‐‑‒–—―−"
_EXTRA_EDGE = "`*_~<>@…‘’“”«»"
_LEAD_STRIP = _OUTER_PUNCT.replace(".", "") + _EXTRA_EDGE + "#"  # keep a leading "." (".NET")
_TRAIL_STRIP = _OUTER_PUNCT + _EXTRA_EDGE  # keep a trailing "#" ("C#")
_BREAK_END = ".,;:!?)]}"


class _Toks:
    """The pieces of one text: normalised, original-case, source spans (if exact), capital flag, break flag."""

    def __init__(self, text: str) -> None:
        self.norm: list[str] = []
        self.orig: list[str] = []
        self.span: list[Optional[tuple[int, int]]] = []
        self.cap: list[bool] = []
        self.brk: list[bool] = []
        self._scan(text[:MAX_TEXT_CHARS])
        self.idx = _index(self.norm)
        self._idx_orig: Optional[dict[str, list[int]]] = None
        self._loose: Optional[set[str]] = None

    def _add(self, core: str, span: Optional[tuple[int, int]]) -> None:
        self.norm.append(core.casefold())
        self.orig.append(core)
        self.span.append(span)
        self.cap.append(core[:1].isupper())
        self.brk.append(False)

    def _scan(self, text: str) -> None:
        n = len(text)
        i = 0
        while i < n and len(self.norm) < MAX_PIECES:
            if text[i].isspace():
                j = i
                while j < n and text[j].isspace():
                    j += 1
                if self.brk and "\n" in text[i:j]:
                    self.brk[-1] = True
                i = j
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
                self._add(core, (a, a + len(core)) if exact else None)
                if k < len(tok) and tok[k] in "/|•":
                    self.brk[-1] = True
            start = k + 1
        if self.norm and tok and tok[-1] in _BREAK_END:
            self.brk[-1] = True

    @property
    def idx_orig(self) -> dict[str, list[int]]:
        if self._idx_orig is None:
            self._idx_orig = _index(self.orig)
        return self._idx_orig

    def find(self, needle: list[str], *, case: bool = False) -> int:
        hay, index = (self.orig, self.idx_orig) if case else (self.norm, self.idx)
        for pos in index.get(needle[0], ()):
            if hay[pos : pos + len(needle)] == needle:
                return pos
        return -1

    @property
    def loose(self) -> set[str]:
        """Alphanumeric-only concatenations of 1..4 adjacent pieces: ``Node JS`` / ``NodeJS`` / ``node.js`` agree."""
        if self._loose is None:
            flat = ["".join(c for c in t if c.isalnum()) for t in self.norm]
            flat = [f for f in flat if f]
            out: set[str] = set()
            for i in range(len(flat)):
                acc = ""
                for f in flat[i : i + _RUN_MAX]:
                    acc += f
                    out.add(acc)
            self._loose = out
        return self._loose


def _index(toks: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i, t in enumerate(toks):
        out.setdefault(t, []).append(i)
    return out


def _loose_key(term: list[str]) -> str:
    """The loose form of a term, or "" when it must be matched strictly (1-2 letters, ``C++`` / ``C#`` / ``.NET``)."""
    if any(c in "+#" for t in term for c in t) or any(t.startswith(".") for t in term):
        return ""
    key = "".join(c for t in term for c in t if c.isalnum())
    return key if len(key) >= 3 else ""


def _caps_base(orig: str) -> str:
    """``LLMs`` -> ``LLM``, ``AWS`` -> ``AWS``; "" when the token is not written in capitals."""
    base = orig[:-1] if orig.endswith("s") and orig[:-1].isupper() else orig
    return base if base.isalpha() and base.isupper() and len(base) >= _ACRONYM_MIN else ""


class _Profile(_Toks):
    """The profile's pieces plus the acronym lookups, built once."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.caps_tokens: dict[str, str] = {}
        for o in self.orig:
            b = _caps_base(o)
            if b:
                self.caps_tokens.setdefault(b.casefold(), o)
        chars: list[str] = []
        self._pos: list[int] = []
        for i, (o, c) in enumerate(zip(self.orig, self.cap)):
            chars.append(o[0].upper() if c and o.isalpha() and len(o) >= 2 else "_")
            self._pos.append(i)
            if self.brk[i]:
                chars.append("_")
                self._pos.append(-1)
        self.initials = "".join(chars)

    def phrase_with_initials(self, acr: str) -> str:
        at = self.initials.find(acr)
        if at < 0:
            return ""
        return " ".join(self.orig[self._pos[k]] for k in range(at, at + len(acr)))


def _acronym_of_ad_word(orig: str) -> str:
    base = orig[:-1] if orig.endswith("s") and orig[:-1].isupper() else orig
    ok = base.isalpha() and base.isupper() and _ACRONYM_MIN <= len(base) <= _ACRONYM_MAX
    return base if ok else ""


def _supported(prof: _Profile, term: list[str], ad_orig: list[str], short: bool) -> tuple[bool, str]:
    """(supported, phrase that supplied it - "" for a literal hit)."""
    if prof.find(ad_orig if short else term, case=short) >= 0:
        return True, ""
    if short:
        return False, ""
    key = _loose_key(term)
    if key and key in prof.loose:
        return True, "spelling variant in the profile"
    if len(term) == 1:
        acr = _acronym_of_ad_word(ad_orig[0])
        if acr:
            phrase = prof.phrase_with_initials(acr)
            if phrase:
                return True, phrase
            if acr.casefold() in prof.caps_tokens:
                return True, prof.caps_tokens[acr.casefold()]
        return False, ""
    acr2 = "".join(t[0] for t in term)
    if len(acr2) >= _ACRONYM_MIN and acr2 in prof.caps_tokens:
        return True, prof.caps_tokens[acr2]
    return False, ""


def _threshold(value: Any) -> float:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return min(1.0, max(MIN_THRESHOLD, float(value))) if ok else DEFAULT_THRESHOLD


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""


def coverage(
    cv_text: str, ad_text: str, requirements: list[Any], profile: Any, threshold: float = DEFAULT_THRESHOLD
) -> dict[str, Any]:
    """Judge the ad's requirements against the profile and the CV text. See the module docstring.

    Returns ``{covered, missing, gaps, ratio, ok, problems, support, must_total, soft_total}``: ``covered``
    supported terms literally in the CV; ``missing`` supported terms the CV lacks; ``gaps`` terms the profile does
    not support (ad words); ``ratio`` covered / (must-have AND supported), 1.0 when there is none; ``problems``
    ``[{"code", "term"}]``; ``support`` {term: profile phrase} for terms supported by an acronym or a variant;
    ``must_total`` the supported must-haves, ``soft_total`` the supported soft ones. A requirement of kind
    "soft" is not a must-have; plain strings and unmarked ones are. ``threshold`` is clamped to [0.5, 1]."""
    th = _threshold(threshold)
    ad_src = _text(ad_text)[:MAX_TEXT_CHARS]
    ad = _Toks(ad_src)
    cv = _Toks(_text(cv_text))
    prof = _Profile(profile_source_text(profile) if profile is not None else "")
    reqs = list(requirements)[:_KEYWORD_MAX_ITEMS] if isinstance(requirements, (list, tuple)) else []

    problems: list[dict[str, str]] = []
    seen: dict[tuple[str, ...], list[Any]] = {}  # term tokens -> [ad word, must_have, state, ad pieces]
    for raw in reqs:
        text, kind = (raw.get("text"), raw.get("kind")) if isinstance(raw, dict) else (raw, None)
        if not isinstance(text, str):
            continue
        text = " ".join(text.split())
        toks = _Toks(text).norm
        if not toks:
            continue
        key = tuple(toks)
        must = not (isinstance(kind, str) and kind == "soft")
        if key in seen:
            seen[key][1] = seen[key][1] or must
            continue
        if len(text) > MAX_TERM_CHARS or len(toks) > _KEYWORD_MAX_WORDS:
            seen[key] = [text[:MAX_TERM_CHARS], must, "long", []]
            continue
        pos = ad.find(list(toks))
        if pos < 0:
            seen[key] = [text, must, "absent", []]
            continue
        last = pos + len(toks) - 1
        spans = ad.span[pos : last + 1]
        if all(s is not None for s in spans):
            word = " ".join(ad_src[spans[0][0] : spans[-1][1]].split())  # type: ignore[index]
        else:
            word = " ".join(ad.orig[pos : last + 1])
        seen[key] = [word, must, "ok", ad.orig[pos : last + 1]]

    covered: list[str] = []
    missing: list[str] = []
    gaps: list[str] = []
    support: dict[str, str] = {}
    must_supported = must_covered = soft_supported = usable = 0
    for key, (word, must, state, ad_orig) in seen.items():
        if state == "long":
            problems.append({"code": "term_too_long", "term": word})
            continue
        if state == "absent":
            problems.append({"code": "term_not_in_ad", "term": word})
            continue
        usable += 1
        term = list(key)
        short = len(term) == 1 and len(term[0]) <= 2
        yes, phrase = _supported(prof, term, ad_orig, short)
        if not yes:
            gaps.append(word)
            lk = _loose_key(term)
            claimed = cv.find(ad_orig if short else term, case=short) >= 0 or bool(lk and lk in cv.loose)
            if claimed:
                problems.append({"code": "gap_claimed_in_cv", "term": word})
            continue
        if phrase:
            support[word] = phrase
        if must:
            must_supported += 1
        else:
            soft_supported += 1
        if cv.find(ad_orig if short else term, case=short) >= 0:
            covered.append(word)
            must_covered += must
        else:
            missing.append(word)

    ratio = must_covered / must_supported if must_supported else 1.0
    if usable == 0:
        problems.append({"code": "no_ad_terms", "term": ""})
    elif must_supported == 0:
        problems.append({"code": "no_must_haves", "term": ""})
    elif ratio < th - 1e-9:
        problems.append({"code": "jd_coverage_low", "term": ""})
    problems.sort(key=lambda p: PROBLEM_CODES.index(p["code"]))
    ok = not any(p["code"] in BLOCKING for p in problems)
    return {
        "covered": covered,
        "missing": missing,
        "gaps": gaps,
        "ratio": ratio,
        "ok": ok,
        "problems": problems,
        "support": support,
        "must_total": must_supported,
        "soft_total": soft_supported,
    }
