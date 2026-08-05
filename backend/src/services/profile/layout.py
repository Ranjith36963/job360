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

# ── Column detection ──────────────────────────────────────────────────────
# A page is only treated as two-column when ALL of these hold. They exist to
# make the check CONSERVATIVE: a false positive would slice a normal one-column
# CV down the middle and destroy every line, which is far worse than leaving a
# two-column page as it is today.
# MEASURED, not guessed, over 7 single-column CVs (16 pages) and 6 LinkedIn
# exports (18 pages):
#
#     widest MID-PAGE blank channel      single-column CVs :   0 - 3 pt
#                                        LinkedIn exports  :  32 - 132 pt
#
# The two populations do not overlap; there is an empty band between 3 and 32,
# and 12 sits in it. That gap is what makes this a real signal rather than a
# tuned constant — a threshold with nothing either side of it is a guess.
MIN_GUTTER_PT = 12.0
# The gap must fall in the middle of the page. This is the guard that actually
# protects one-column CVs: their widest blank channel is always a MARGIN
# (measured at 0-11% or 90-100% of width), never the centre.
GUTTER_ZONE = (0.15, 0.85)
# A floor against a page whose only right-hand object is a floated logo or a
# page number. Deliberately an absolute count, NOT a share: a sidebar is
# SUPPOSED to be small. The share-based rule this replaces demanded 15% of
# words per side and so rejected four of six real LinkedIn sidebars, which
# hold 8-19% — it was rejecting the very thing it was written to find.
MIN_SIDE_WORDS = 5


def find_column_gutter(
    words: list[dict[str, Any]], page_width: float
) -> float | None:
    """Find the x of the blank vertical channel splitting a two-column page.

    WHY THIS EXISTS. LinkedIn's "Save to PDF" always renders a narrow sidebar
    (Contact, Top Skills) beside the main column (Experience, Education).
    Reading such a page with ``page.extract_text()`` walks it line by line
    across the FULL width, so the two columns interleave and fuse. Measured on
    a real export, one line came out as::

        Pandas (Software) Mathematics Tutor
        └─ a sidebar skill ─┘└─ a job title ─┘

    A job title welded to a skill matches no employer and parses as neither, and
    the profile ends up with zero work history. This is not one bad file — it is
    the shape of every LinkedIn PDF export.

    HOW. Mark every x-position covered by any word, then look for the widest
    uncovered run. That is geometry only: it needs no knowledge of the language,
    the profession, or which side the sidebar is on, so it works on a LinkedIn
    export, a two-column academic CV, or a design-heavy résumé alike.

    Returns the split x, or ``None`` when the page is single-column — in which
    case the caller must leave extraction exactly as it was.
    """
    if not words or page_width <= 0:
        return None

    # Occupancy in 1pt bins. Coarse on purpose: sub-point precision would let
    # kerning gaps masquerade as a gutter.
    covered = [False] * (int(page_width) + 1)
    for w in words:
        x0, x1 = int(max(0, w.get("x0", 0))), int(min(page_width, w.get("x1", 0)))
        for x in range(x0, x1 + 1):
            covered[x] = True

    lo, hi = int(page_width * GUTTER_ZONE[0]), int(page_width * GUTTER_ZONE[1])
    best_start = best_len = 0
    run_start = None
    for x in range(lo, hi + 1):
        if not covered[x]:
            run_start = x if run_start is None else run_start
        else:
            if run_start is not None and x - run_start > best_len:
                best_start, best_len = run_start, x - run_start
            run_start = None
    if run_start is not None and hi + 1 - run_start > best_len:
        best_start, best_len = run_start, hi + 1 - run_start

    if best_len < MIN_GUTTER_PT:
        return None

    split = best_start + best_len / 2.0
    left = sum(1 for w in words if w.get("x1", 0) <= split)
    right = len(words) - left
    # Both sides must carry real content. A page whose only right-hand object is
    # a floated logo has a wide blank channel too, and splitting on it would be
    # nonsense.
    if min(left, right) < MIN_SIDE_WORDS:
        return None
    return split


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
