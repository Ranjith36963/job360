"""Layer-1 universal patterns — PATTERNS ONLY, never content (spec §6 + §7).

When a user KEEPS a polished doc, we derive a small dict of *structural* features
(counts, ratios, labels) from it and store that in ``tailoring_patterns``. We store
**no user_id and no document text** — only shape. This is what lets a brand-new
user get a decently-structured draft on day 1 without ever memorising or leaking
anyone's real CV content.

``derive_patterns`` is deliberately incapable of leaking content: it only returns
numbers and coarse labels. There is no code path here that stores a substring of the
input.
"""

from __future__ import annotations

import re
from collections import Counter

_BULLET_RE = re.compile(r"^\s*[-•*•]\s+")
# A "section header" heuristic: a short line that is mostly UPPERCASE letters.
_HEADER_RE = re.compile(r"^[A-Z][A-Z \t/&]{2,30}$")


def _band(n: int, edges: tuple[int, ...]) -> str:
    """Bucket a count into a coarse band label (so we never store an exact figure)."""
    lo = 0
    for e in edges:
        if n < e:
            return f"{lo}-{e - 1}"
        lo = e
    return f"{edges[-1]}+"


def derive_patterns(text: str, doc_kind: str) -> dict:
    """Return structural features only — no content, no PII.

    Safe to store in the shared universal layer.
    """
    text = text or ""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    words = re.findall(r"\b\w+\b", text)
    word_count = len(words)
    bullet_lines = sum(1 for ln in lines if _BULLET_RE.match(ln))
    header_lines = sum(1 for ln in lines if _HEADER_RE.match(ln.strip()))
    # First-person density → a coarse "warm vs formal" signal (cover letters warmer).
    lowered = [w.lower() for w in words]
    first_person = sum(1 for w in lowered if w in {"i", "my", "me", "i've", "i'm"})
    fp_ratio = (first_person / word_count) if word_count else 0.0

    return {
        "doc_kind": doc_kind,
        "word_band": _band(word_count, (150, 300, 450, 600, 900)),
        "line_band": _band(len(lines), (10, 20, 35, 55)),
        "bullet_ratio": round(bullet_lines / len(lines), 2) if lines else 0.0,
        "section_count_band": _band(header_lines, (2, 4, 6, 9)),
        "style": "warm" if fp_ratio > 0.02 else "formal",
        "avg_words_per_line": round(word_count / len(lines), 1) if lines else 0.0,
    }


def summarize_patterns(rows: list[dict], doc_kind: str) -> str:
    """Aggregate stored pattern dicts into a short prompt-guidance string.

    Used mostly for cold-start (a user with no history of their own). Returns "" when
    there's nothing learned yet, so the prompt simply omits the section.
    """
    rows = [r for r in rows if r.get("doc_kind") == doc_kind]
    if not rows:
        return ""

    def _mode(key: str) -> str:
        c = Counter(str(r.get(key, "")) for r in rows if r.get(key) not in (None, ""))
        return c.most_common(1)[0][0] if c else ""

    avg_bullet = sum(float(r.get("bullet_ratio", 0)) for r in rows) / len(rows)
    what = "CV" if doc_kind == "cv" else "cover letter"
    bullet_hint = "bullet-heavy" if avg_bullet >= 0.3 else "prose-led"
    return (
        f"Across {len(rows)} kept {what}s: typical length {_mode('word_band')} words, "
        f"{_mode('section_count_band')} sections, {bullet_hint}, "
        f"{_mode('style')} tone. Aim for a similar shape."
    )
