"""Read two-column PDFs one column at a time.

WHY THIS EXISTS. LinkedIn's "Save to PDF" always renders a narrow sidebar
(Contact, Top Skills) beside the main column (Experience, Education). Reading
such a page with pdfplumber's plain ``extract_text()`` walks it line by line
across the FULL page width, so the two columns interleave and fuse. On a real
export, one line came out as::

    Pandas (Software) Mathematics Tutor
    └─ a sidebar skill ─┘└─ a job title ─┘

A job title welded to a skill parses as neither, and that profile ended up with
ZERO work history. This was not one bad file — it is the shape of every LinkedIn
PDF export, so it silently degraded every LinkedIn upload we have ever taken.

THE RISK RUNS THE OTHER WAY TOO. Splitting a normal one-column CV down the
middle would destroy every line in it — a far worse outcome than the bug being
fixed. Most uploads are one-column CVs, so the detector must stay silent on
them. Roughly half the tests below are about that direction.

THRESHOLDS ARE MEASURED, NOT CHOSEN. Over 7 real CVs (16 pages) and 6 real
LinkedIn exports (18 pages), the widest MID-PAGE blank channel was:

    single-column CVs :   0 -   3 pt
    LinkedIn exports  :  32 - 132 pt

The populations do not overlap, and MIN_GUTTER_PT (12) sits in the empty band
between them. Tests below encode both sides of that band so a future tweak that
closes the gap fails loudly.
"""

from __future__ import annotations

from src.services.profile.layout import MIN_GUTTER_PT, find_column_gutter

PAGE_W = 612.0  # US Letter, what LinkedIn exports


def _w(x0: float, x1: float, text: str = "x") -> dict:
    """One word at an x-range. Only x matters to column detection."""
    return {"x0": x0, "x1": x1, "top": 10.0, "bottom": 20.0, "text": text}


def _column(x0: float, x1: float, n: int) -> list[dict]:
    return [_w(x0, x1, f"w{i}") for i in range(n)]


def test_a_linkedin_shaped_page_is_detected():
    """Narrow sidebar + wide main column, the real LinkedIn geometry: the
    sidebar sits at x 30-160 and the main column starts at x 223."""
    words = _column(30, 160, 15) + _column(223, 560, 150)
    gutter = find_column_gutter(words, PAGE_W)
    assert gutter is not None, "the LinkedIn two-column shape was missed"
    assert 160 < gutter < 223, f"gutter {gutter} is not inside the blank channel"


def test_a_small_sidebar_is_still_a_column():
    """THE BUG THIS REPLACED. The first version demanded each side hold 15% of
    the page's words. Real LinkedIn sidebars hold 8-19%, so it rejected four of
    six genuine exports — the rule was rejecting the very thing it was written
    to find. A sidebar is SUPPOSED to be small."""
    words = _column(30, 160, 8) + _column(223, 560, 160)  # sidebar = 4.8%
    assert find_column_gutter(words, PAGE_W) is not None, (
        "a genuine narrow sidebar was rejected for being narrow"
    )


def test_a_single_column_cv_is_left_alone():
    """The protective direction. One column of text with ordinary margins must
    return None, so extraction stays byte-identical to what it is today."""
    words = _column(60, 550, 400)
    assert find_column_gutter(words, PAGE_W) is None


def test_wide_page_margins_are_never_mistaken_for_a_gutter():
    """Measured: every single-column CV's widest blank channel is a MARGIN
    (0-11% or 90-100% of width), often 40-67pt — wider than MIN_GUTTER_PT. Only
    the mid-page restriction keeps those from reading as a column split."""
    words = _column(90, 520, 300)  # ~90pt of blank margin on both sides
    assert find_column_gutter(words, PAGE_W) is None, (
        "a page margin was read as a column gutter"
    )


def test_a_floated_logo_does_not_split_the_page():
    """A page whose only right-hand object is a logo or page number has a wide
    blank channel too. Splitting there would be nonsense."""
    words = _column(60, 300, 200) + _column(500, 540, 2)
    assert find_column_gutter(words, PAGE_W) is None


def test_the_measured_band_between_the_two_populations_is_respected():
    """Encodes the measurement itself: a 3pt mid-page gap (the widest seen in
    any real one-column CV) must NOT split, and a 32pt one (the narrowest seen
    in any real LinkedIn export) MUST. If a future tweak closes that band, this
    fails instead of silently mangling uploads."""
    narrow = _column(60, 300, 100) + _column(303, 550, 100)
    assert find_column_gutter(narrow, PAGE_W) is None, "a 3pt CV gap split the page"

    wide = _column(60, 268, 100) + _column(300, 550, 100)
    assert find_column_gutter(wide, PAGE_W) is not None, "a 32pt LinkedIn gutter was missed"

    assert 3 < MIN_GUTTER_PT < 32, (
        "MIN_GUTTER_PT must stay inside the measured empty band (3pt..32pt); "
        f"it is {MIN_GUTTER_PT}"
    )


def test_no_words_and_bad_width_are_handled():
    """Extraction must never crash on a blank or malformed page — a layout
    optimisation cannot be allowed to cost someone their upload."""
    assert find_column_gutter([], PAGE_W) is None
    assert find_column_gutter(_column(60, 550, 10), 0) is None
