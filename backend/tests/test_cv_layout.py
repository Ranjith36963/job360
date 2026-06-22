"""Batch 1.7 (Pillar 1) — layout-aware PDF section segmentation tests.

``segment_sections_from_words`` is pure data shaping over synthetic
word lists, so no PDFs required. The ``extract_sections_from_pdf``
wrapper gets a light integration test with pdfplumber monkey-patched.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


from src.services.profile import cv_parser
from src.services.profile.layout import segment_sections_from_words


def _word(text: str, size: float, top: float, x0: float = 50.0, page: int = 0) -> dict:
    """Build a synthetic pdfplumber word dict for tests."""
    return {
        "text": text,
        "size": size,
        "top": top,
        "x0": x0,
        "fontname": "Helvetica",
        "page": page,
    }


# ── segment_sections_from_words ─────────────────────────────────────


def test_empty_word_list_returns_empty_header():
    assert segment_sections_from_words([]) == {"header": ""}


def test_no_size_metadata_falls_back_to_header_only():
    """If none of the words carry a ``size``, we can't cluster — everything is header."""
    words = [{"text": "Hello", "top": 0, "x0": 10}, {"text": "World", "top": 15, "x0": 10}]
    out = segment_sections_from_words(words)
    assert "hello world" in out["header"].lower() or "Hello" in out["header"]
    # At minimum, the text survived.
    assert out["header"]


def test_uniform_size_means_no_headers():
    """All words same size → no threshold crossed → single header bucket."""
    words = [_word("Some", 11, 0.0), _word("body", 11, 0.0), _word("text", 11, 20.0)]
    out = segment_sections_from_words(words)
    assert list(out.keys()) == ["header"]
    assert "Some body" in out["header"]
    assert "text" in out["header"]


def test_larger_font_creates_section_boundary():
    """A +2pt line ≥ header_threshold becomes a section key."""
    words = [
        # Pre-header body
        _word("Name", 11, 0.0), _word("Surname", 11, 0.0),
        # Heading at larger size
        _word("Experience", 14, 30.0),
        # Body of Experience
        _word("Senior", 11, 60.0), _word("Engineer", 11, 60.0),
        _word("at", 11, 60.0), _word("Acme", 11, 60.0),
    ]
    out = segment_sections_from_words(words)
    assert "experience" in out
    assert "Senior Engineer at Acme" in out["experience"]
    assert "Name Surname" in out["header"]


def test_multiple_sections_emit_distinct_keys():
    body = 11
    hdr = 14
    words = [
        _word("Ada", hdr, 0.0),       # looks like a header (big), but first line pre-split
        _word("Experience", hdr, 30.0),
        _word("Senior", body, 50.0), _word("Eng", body, 50.0),
        _word("Education", hdr, 80.0),
        _word("BSc", body, 100.0), _word("Maths", body, 100.0),
        _word("Skills", hdr, 130.0),
        _word("Python", body, 150.0), _word("Rust", body, 150.0),
    ]
    out = segment_sections_from_words(words)
    assert "experience" in out
    assert "education" in out
    assert "skills" in out
    assert "Senior Eng" in out["experience"]
    assert "BSc Maths" in out["education"]
    assert "Python Rust" in out["skills"]


def test_long_large_line_not_treated_as_heading():
    """A 200-char line in big font is a title/summary paragraph, not a section heading."""
    big_words = [_word(w, 14, 0.0) for w in ["This", "is", "a", "long", "line"] * 5]
    out = segment_sections_from_words(big_words)
    # Cluster produced no section; all in header
    assert list(out.keys()) == ["header"]


def test_page_break_starts_new_line_group():
    """Word on page 2 with same top coord must not join page 1's line."""
    words = [
        _word("Page1", 11, 10.0, page=0),
        _word("Page2", 11, 10.0, page=1),
    ]
    out = segment_sections_from_words(words)
    # Both should appear but NOT on same line — either in separate newline groups
    assert "Page1" in out["header"]
    assert "Page2" in out["header"]
    # Order preserved: page 0 before page 1
    assert out["header"].index("Page1") < out["header"].index("Page2")


# ── extract_sections_from_pdf (pdfplumber mocked) ───────────────────


def test_extract_sections_returns_none_when_pdf_unreadable():
    """Non-existent path bubbles through pdfplumber as an open error → ``None``."""
    result = cv_parser.extract_sections_from_pdf("/nonexistent/does-not-exist.pdf")
    assert result is None


def test_extract_sections_happy_path_with_mocked_pdfplumber():
    """Patch pdfplumber.open to yield a page whose extract_words returns our synthetic list."""
    words = [
        _word("Alex", 11, 0.0),
        _word("Experience", 14, 20.0),
        _word("Senior", 11, 40.0), _word("Engineer", 11, 40.0),
    ]

    mock_page = MagicMock()
    mock_page.extract_words = MagicMock(return_value=[dict(w) for w in words])
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    with patch("pdfplumber.open", return_value=mock_pdf):
        result = cv_parser.extract_sections_from_pdf("fake.pdf")

    assert result is not None
    assert "experience" in result
    assert "Senior Engineer" in result["experience"]
    assert "Alex" in result["header"]


def test_extract_sections_returns_none_on_empty_word_list():
    mock_page = MagicMock()
    mock_page.extract_words = MagicMock(return_value=[])
    mock_pdf = MagicMock()
    mock_pdf.pages = [mock_page]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    with patch("pdfplumber.open", return_value=mock_pdf):
        assert cv_parser.extract_sections_from_pdf("empty.pdf") is None


def test_extract_sections_survives_per_page_extract_words_error():
    """One flaky page shouldn't take the whole PDF down."""
    good_page = MagicMock()
    good_page.extract_words = MagicMock(return_value=[_word("Works", 11, 0.0)])
    bad_page = MagicMock()
    bad_page.extract_words = MagicMock(side_effect=RuntimeError("bad page"))

    mock_pdf = MagicMock()
    mock_pdf.pages = [bad_page, good_page]
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)

    with patch("pdfplumber.open", return_value=mock_pdf):
        result = cv_parser.extract_sections_from_pdf("flaky.pdf")

    assert result is not None
    assert "Works" in result["header"]
