"""Layout-aware PDF section segmentation.

Batch 1.7 (Pillar 1). The original ``cv_parser.extract_text_from_pdf``
flattens a PDF to a single blob, losing font size and position cues
that human readers rely on to distinguish a heading ("Experience")
from its body. Plain-text flattening is *good enough* for single-
column CVs but mis-parses multi-column layouts (skills bleed into
experience — plan §10 Batch 1.7 acceptance criterion).

This module clusters words by font size: anything ``HEADER_DELTA_PT``
above the median body size — typically +2 — is treated as a heading
and starts a new section. Lines between headings become the section
body. The output shape matches ``linkedin_parser._split_sections``'s
``{heading_lower: body}`` contract so downstream LLM prompts can
receive pre-segmented text on an opt-in basis.

Deliberately no pdfplumber import here — pure data shaping. The
``cv_parser`` owns the PDF open. This keeps the module unit-testable
on synthetic word lists without any real PDF files.
"""

from __future__ import annotations

import logging
from statistics import median
from typing import Any, Iterable

logger = logging.getLogger("job360.profile.layout")


HEADER_DELTA_PT = 1.5  # header = body_size + HEADER_DELTA_PT
LINE_TOLERANCE_PT = 2.0  # words within this vertical distance count as the same line


def _group_into_lines(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Group words into visual lines using their ``top`` coordinate.

    pdfplumber emits words with ``top`` (y-coord from page top) and
    ``x0`` (left x). Words are sorted first by page index, then by
    ``top`` ascending, then ``x0`` ascending. We bucket consecutive
    words into the same line when their ``top`` differs by less than
    ``LINE_TOLERANCE_PT``.
    """
    if not words:
        return []

    # Stable sort: page → top → x0
    sorted_words = sorted(
        words,
        key=lambda w: (
            w.get("page", 0),
            round(float(w.get("top", 0.0)), 1),
            float(w.get("x0", 0.0)),
        ),
    )

    lines: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_top: float | None = None
    current_page: int | None = None

    for w in sorted_words:
        try:
            top = float(w.get("top", 0.0))
        except (TypeError, ValueError):
            continue
        page = w.get("page", 0)

        if current_top is None:
            current = [w]
            current_top = top
            current_page = page
            continue

        # New line when y moves by more than tolerance OR when page flips
        if page != current_page or abs(top - current_top) > LINE_TOLERANCE_PT:
            if current:
                lines.append(current)
            current = [w]
            current_top = top
            current_page = page
        else:
            current.append(w)

    if current:
        lines.append(current)
    return lines


def _line_size(line: list[dict[str, Any]]) -> float:
    """Return the median ``size`` of the words in a line (0.0 if missing)."""
    sizes = []
    for w in line:
        try:
            sizes.append(float(w.get("size", 0.0)))
        except (TypeError, ValueError):
            continue
    return median(sizes) if sizes else 0.0


def _line_text(line: list[dict[str, Any]]) -> str:
    """Concatenate words on a line with single spaces."""
    parts = [str(w.get("text", "")) for w in line if w.get("text")]
    return " ".join(parts).strip()


def segment_sections_from_words(words: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Segment a word stream into ``{heading: body}`` by font-size clustering.

    Returns a dict with lowercase heading keys and their accumulated
    body text (lines joined with newlines). Content preceding the
    first detected heading falls under the ``"header"`` key, matching
    the LinkedIn parser's convention.

    Edge cases:
      * No words → ``{"header": ""}``
      * No size metadata on any word → ``{"header": "<all joined>"}``
        (can't cluster → treat everything as header body)
      * All words same size → no heading detected → same as above
    """
    word_list = list(words)
    lines = _group_into_lines(word_list)
    if not lines:
        return {"header": ""}

    sizes = [_line_size(ln) for ln in lines]
    # Body median is taken over ALL word sizes (not line sizes) so that
    # body text — which is vastly more numerous than headings — dominates
    # the median. A line-size median gets pulled toward header sizes in
    # CVs with many short lines, which would miss header detection
    # entirely (caught by ``test_multiple_sections_emit_distinct_keys``).
    word_sizes: list[float] = []
    for ln in lines:
        for w in ln:
            try:
                s = float(w.get("size", 0.0))
            except (TypeError, ValueError):
                continue
            if s > 0.0:
                word_sizes.append(s)
    if not word_sizes:
        return {"header": "\n".join(_line_text(ln) for ln in lines).strip()}

    body_median = median(word_sizes)
    header_threshold = body_median + HEADER_DELTA_PT

    sections: dict[str, list[str]] = {"header": []}
    current = "header"

    for ln, size in zip(lines, sizes):
        text = _line_text(ln)
        if not text:
            continue
        # A heading is a short line in a larger font than the body
        is_header = size >= header_threshold and len(text) < 80 and len(text.split()) < 12
        if is_header:
            current = text.lower().strip()
            sections.setdefault(current, [])
        else:
            sections.setdefault(current, []).append(text)

    return {k: "\n".join(v).strip() for k, v in sections.items()}
