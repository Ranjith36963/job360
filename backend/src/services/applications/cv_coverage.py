"""JD coverage, the pure core: do the ad's EXACT words reach the CV, and only where the profile says them?

Job360 runs no model. The assistant saves the ad's requirements (``save_fit`` ``jd_requirements``, items
``{"text", "kind"}``); this module only compares three texts, token by token:

* the frozen ad (``applications.job_description_snapshot``): a requirement counts only if its words occur there
  literally (case and whitespace aside); it is then reported in the AD's spelling;
* the profile (:func:`cv_checks.profile_source_text`): a requirement is SUPPORTED when it occurs there literally,
  or is the acronym of a profile phrase / the phrase of a profile acronym (initials, a trailing "s" allowed);
* the CV text: a supported requirement is COVERED only when it occurs there literally. A requirement the profile
  lacks is a GAP: it must not be in the CV at all.

No synonym list (hard rule #28), no regex on caller text (CodeQL): one linear scan per text, whole tokens only
(``Java`` is not in ``JavaScript``; ``C++``, ``C#``, ``.NET``, ``Node.js`` stay one token; ``/`` and ``|`` split
``Python/Java``). Every input is capped, so work is bounded. Stored output holds ad words and codes only.
"""

from __future__ import annotations

from typing import Any, Optional

from src.services.applications.cv_checks import (
    _KEYWORD_MAX_ITEMS,
    _KEYWORD_MAX_WORDS,
    _OUTER_PUNCT,
    profile_source_text,
)

# Closed problem codes (report order). ``BLOCKING`` ones make ``ok`` false on their own.
PROBLEM_CODES = ("no_ad_terms", "term_not_in_ad", "term_too_long", "jd_coverage_low", "gap_claimed_in_cv")
BLOCKING = frozenset({"no_ad_terms", "jd_coverage_low", "gap_claimed_in_cv"})

MAX_TEXT_CHARS = 400_000  # per text; longer input is cut (a CV / ad / profile is far shorter)
MAX_TERM_CHARS = 200  # = settings.FIT_GAP_TEXT_MAX_CHARS
MAX_PIECE_CHARS = 100  # a longer "word" is junk and is dropped
_ACRONYM_MIN, _ACRONYM_MAX = 2, 6
_EXPAND_MIN = 3  # a 2-letter acronym ("AI") would match any two words ("and in"): it needs the literal word
_SPLIT_CHARS = "/|•"
_LEAD_STRIP = _OUTER_PUNCT.replace(".", "") + "`"  # keep a leading "." (".NET")
_TRAIL_STRIP = _OUTER_PUNCT + "`"


def _pieces(text: str, *, offsets: bool = False) -> tuple[list[str], list[tuple[int, int]]]:
    """Whole-token pieces of ``text``: case-folded, outer punctuation trimmed, ``/ | bullet`` splitting a token.
    With ``offsets`` also each piece's (start, end) in the ORIGINAL text. One pass, no regex."""
    text = text[:MAX_TEXT_CHARS]
    norm: list[str] = []
    spans: list[tuple[int, int]] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < n and not text[j].isspace():
            j += 1
        start = i
        for k in range(i, j + 1):  # split at separators; k == j closes the last piece
            if k < j and text[k] not in _SPLIT_CHARS:
                continue
            raw = text[start:k]
            lead = len(raw) - len(raw.lstrip(_LEAD_STRIP))
            core = raw[lead:].rstrip(_TRAIL_STRIP)
            if core and len(core) <= MAX_PIECE_CHARS:
                norm.append(core.casefold())
                if offsets:
                    spans.append((start + lead, start + lead + len(core)))
            start = k + 1
        i = j
    return norm, spans


def _find(hay: list[str], needle: list[str], index: dict[str, list[int]]) -> int:
    """Index of the first contiguous occurrence of ``needle`` in ``hay`` (-1 if none); ``index`` maps a token to its
    positions, so the scan only visits places where the first word occurs."""
    first = needle[0]
    for pos in index.get(first, ()):
        if hay[pos : pos + len(needle)] == needle:
            return pos
    return -1


def _index(toks: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for i, t in enumerate(toks):
        out.setdefault(t, []).append(i)
    return out


def _is_acronym_shape(tok: str) -> bool:
    return _ACRONYM_MIN <= len(tok) <= _ACRONYM_MAX and tok.isalnum()


def _initials(toks: list[str]) -> str:
    """One letter per token; a token that is not a plain word becomes "_" so no acronym spans it."""
    return "".join(t[0] if t.isalpha() else "_" for t in toks)


class _Profile:
    """The profile's tokens, with the acronym lookups built once."""

    def __init__(self, text: str) -> None:
        self.toks, _ = _pieces(text)
        self.index = _index(self.toks)
        self.initials = _initials(self.toks)  # one letter per token: a phrase's acronym is a substring of it

    def says(self, term: list[str]) -> bool:
        if _find(self.toks, term, self.index) >= 0:
            return True
        if len(term) == 1:
            return self._acronym_expands(term[0])
        return self._phrase_has_acronym(term)

    def _acronym_expands(self, tok: str) -> bool:
        """``llm`` is said by "llms" in the profile, or by a run of words with those initials."""
        if not _is_acronym_shape(tok):
            return False
        bare = tok[:-1] if tok.endswith("s") and len(tok) > _EXPAND_MIN else tok
        if bare + "s" in self.index or bare in self.index:
            return True
        return len(bare) >= _EXPAND_MIN and bare in self.initials

    def _phrase_has_acronym(self, term: list[str]) -> bool:
        acr = _initials(term)
        return len(acr) >= _EXPAND_MIN and "_" not in acr and (acr in self.index or acr + "s" in self.index)


def _term_text(raw: Any) -> tuple[str, Optional[str]]:
    if isinstance(raw, dict):
        return str(raw.get("text") or ""), raw.get("kind")
    return str(raw or ""), None


def coverage(
    cv_text: str, ad_text: str, requirements: list[Any], profile: Any, threshold: float = 0.8
) -> dict[str, Any]:
    """Judge the ad's requirements against the profile and the CV text. See the module docstring.

    Returns ``{covered, missing, gaps, ratio, ok, problems}``: ``covered`` supported terms literally in the CV;
    ``missing`` supported terms the CV lacks; ``gaps`` terms the profile does not support (ad words);
    ``ratio`` covered / (must-have AND supported), 1.0 when there is none; ``problems`` ``[{"code", "term"}]``.
    A requirement of kind "soft" is not a must-have; plain strings and unmarked ones are."""
    ad_toks, ad_spans = _pieces(str(ad_text or ""), offsets=True)
    ad_clip = str(ad_text or "")[:MAX_TEXT_CHARS]
    ad_idx = _index(ad_toks)
    cv_toks, _ = _pieces(str(cv_text or ""))
    cv_idx = _index(cv_toks)
    prof = _Profile(profile_source_text(profile) if profile is not None else "")

    problems: list[dict[str, str]] = []
    seen: dict[tuple[str, ...], list[Any]] = {}  # term tokens -> [ad word, must_have]
    for raw in list(requirements or [])[:_KEYWORD_MAX_ITEMS]:
        text, kind = _term_text(raw)
        text = " ".join(text.split())
        toks, _s = _pieces(text)
        if not toks:
            continue
        key = tuple(toks)
        must = kind != "soft"
        if key in seen:
            seen[key][1] = seen[key][1] or must
            continue
        if len(text) > MAX_TERM_CHARS or len(toks) > _KEYWORD_MAX_WORDS:
            seen[key] = [text[:MAX_TERM_CHARS], must, "long"]
            continue
        pos = _find(ad_toks, list(toks), ad_idx)
        if pos < 0:
            seen[key] = [text, must, "absent"]
            continue
        a, b = ad_spans[pos][0], ad_spans[pos + len(toks) - 1][1]
        seen[key] = [" ".join(ad_clip[a:b].split()), must, "ok"]

    covered: list[str] = []
    missing: list[str] = []
    gaps: list[str] = []
    must_supported = 0
    must_covered = 0
    usable = 0
    for key, (word, must, state) in seen.items():
        if state == "long":
            problems.append({"code": "term_too_long", "term": word})
            continue
        if state == "absent":
            problems.append({"code": "term_not_in_ad", "term": word})
            continue
        usable += 1
        term = list(key)
        in_cv = _find(cv_toks, term, cv_idx) >= 0
        if not prof.says(term):
            gaps.append(word)
            if in_cv:
                problems.append({"code": "gap_claimed_in_cv", "term": word})
            continue
        if must:
            must_supported += 1
        if in_cv:
            covered.append(word)
            if must:
                must_covered += 1
        else:
            missing.append(word)

    ratio = must_covered / must_supported if must_supported else 1.0
    if usable == 0:
        problems.append({"code": "no_ad_terms", "term": ""})
    elif ratio < threshold - 1e-9:
        problems.append({"code": "jd_coverage_low", "term": ""})
    problems.sort(key=lambda p: PROBLEM_CODES.index(p["code"]))
    ok = not any(p["code"] in BLOCKING for p in problems)
    return {"covered": covered, "missing": missing, "gaps": gaps, "ratio": ratio, "ok": ok, "problems": problems}
