"""Proper-noun integrity pass — deterministic enforcement of guardrail #2.

The "never fabricate" system prompt stops the model INVENTING facts, but it does
NOT stop it MISSPELLING a real one ("Monzo" -> "Monox") while it rewrites. That is a
model-level slip, so we fix it in deterministic code — not another prompt the model
can ignore.

After generation we:
  - collect proper-noun-like tokens from the SOURCE (the user's CV + the job),
  - scan the OUTPUT for the same,
  - AUTO-CORRECT an output token that is a near-miss of a source token
    (fuzzy >= threshold, same initial letter, similar length)  ->  "Monox" -> "Monzo",
  - FLAG an output brand/tech token that matches NOTHING in the source
    (a possible fabrication) for the user to review.

Safety: we bias hard toward NOT changing correct text. A correction only fires on a
high fuzzy score AND same first letter AND near-identical length, so an unrelated real
word ("Manager") is never rewritten into a source name. And the user still reviews the
output (guardrail #3), so this is a safety net, not the last word.

``rapidfuzz`` is already a project dependency (lazy-imported per rules #11/#16).
"""

from __future__ import annotations

import re

# A token is a word, optionally with INTERNAL . & + - (Node.js, AT&T) — but never a
# trailing separator (so a sentence period isn't glued on, which would break the
# word-boundary replace and the flag text).
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[.&+\-][A-Za-z0-9]+)*")
_FUZZY_MIN = 80          # >= this  -> treat as a typo of the source spelling, auto-correct
_FABRICATION_MAX = 55    # <= this  -> matches nothing in source, flag as possible fabrication

# Standard CV section headers — ALLCAPS words the model adds for STRUCTURE, not
# content. They're not in the source CV, so without this they'd be flagged as
# fabrications (noisy false positives, e.g. "SUMMARY"). Excluded from flagging only;
# auto-correction is unaffected. (Not profile extraction, so rule #28 doesn't apply.)
_SECTION_HEADERS = frozenset({
    "SUMMARY", "PROFILE", "OBJECTIVE", "EXPERIENCE", "EDUCATION", "SKILLS",
    "PROJECTS", "CERTIFICATIONS", "CERTIFICATION", "REFERENCES", "CONTACT",
    "LANGUAGES", "INTERESTS", "ACHIEVEMENTS", "EMPLOYMENT", "WORK", "VOLUNTEER",
    "AWARDS", "PUBLICATIONS", "QUALIFICATIONS", "TECHNICAL", "PROFESSIONAL",
    "PERSONAL", "HIGHLIGHTS", "COMPETENCIES", "ACCOMPLISHMENTS",
})


def _is_proper(tok: str) -> bool:
    """A proper-noun-like token: ALLCAPS acronym (AWS), camelCase brand (PyTorch),
    or a Titlecase word (Monzo, London)."""
    if not tok[:1].isupper():
        return False
    if tok.isupper() and len(tok) >= 2:          # AWS, SQL, NASA
        return True
    if any(c.isupper() for c in tok[1:]):        # PyTorch, GitHub, iOS-style
        return True
    return tok[1:].islower() and len(tok) >= 3   # Monzo, London, Barclays


def proper_nouns(text: str) -> set[str]:
    """Extract the set of proper-noun-like tokens from ``text``."""
    return {m.group(0) for m in _TOKEN_RE.finditer(text or "") if _is_proper(m.group(0))}


def repair_proper_nouns(output: str, source_text: str) -> tuple[str, list[str]]:
    """Return ``(repaired_output, flagged_terms)``.

    Auto-corrects near-miss proper nouns to their exact source spelling; flags
    clearly-branded proper nouns that appear nowhere in the source (possible
    fabrication). Never invents or deletes — only re-spells a near-miss.
    """
    from rapidfuzz import fuzz, process  # noqa: PLC0415 — lazy (rules #11/#16)

    source = proper_nouns(source_text)
    if not source:
        return output, []

    repaired = output
    flagged: list[str] = []
    # Longest first so a correction never partially clobbers a longer token.
    for tok in sorted(proper_nouns(output), key=len, reverse=True):
        if tok in source:
            continue  # exact match -> trust it
        best = process.extractOne(tok, source, scorer=fuzz.ratio)
        if best is None:
            continue
        match, score = best[0], best[1]
        if score >= _FUZZY_MIN and match[:1] == tok[:1] and abs(len(match) - len(tok)) <= 2:
            # near-miss of a real source name -> re-spell it ("Monox" -> "Monzo")
            repaired = re.sub(rf"\b{re.escape(tok)}\b", match, repaired)
        elif (
            score <= _FABRICATION_MAX
            and len(tok) >= 4  # skip 2-3 char abbreviations (MSc, F1, PhD, MBA) — almost always legit credentials, not fabricated brands
            and (tok.isupper() or any(c.isupper() for c in tok[1:]))
            and tok.upper() not in _SECTION_HEADERS  # not a structural section header
        ):
            # a brand/tech token that resembles nothing in the source -> surface it
            flagged.append(tok)
    return repaired, flagged
