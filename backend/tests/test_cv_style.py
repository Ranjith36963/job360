"""CV style fingerprint: read the design of the uploaded PDF (no LLM)."""

from __future__ import annotations

from src.services.profile.cv_style import DEFAULT_STYLE, extract_cv_style, normalize_style
from tests.test_profile_upload import _register_and_login, _stub_extraction, api  # noqa: F401


def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def _make_designed_pdf(path: str) -> None:
    """1 page: 20pt bold navy centred name, navy bold caps headings with a rule."""
    from fpdf import FPDF

    navy = _hex_to_rgb("#1F3864")
    pdf = FPDF(format="A4")
    pdf.set_margins(15, 15, 15)
    pdf.add_page()
    pdf.set_auto_page_break(False)
    width = 180
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*navy)
    pdf.cell(width, 10, "JANE EXAMPLE", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(0, 0, 0)
    pdf.cell(width, 5, "jane@example.com | +44 7700 900123", align="C", new_x="LMARGIN", new_y="NEXT")
    for heading, entries in (
        ("EXPERIENCE", (("Acme Ltd | Engineer", "June 2020 - Present"), ("Beta Inc | Analyst", "May 2018 - May 2020"))),
        ("EDUCATION", (("State University | BSc", "2014 - 2018"),)),
    ):
        pdf.ln(3)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(*navy)
        pdf.cell(width, 6, heading, new_x="LMARGIN", new_y="NEXT")
        y = pdf.get_y()
        pdf.set_draw_color(*navy)
        pdf.set_line_width(0.3)
        pdf.line(15, y, 195, y)
        pdf.set_text_color(0, 0, 0)
        for left, right in entries:
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.cell(120, 5, left)
            pdf.set_font("Helvetica", "", 9.5)
            pdf.cell(60, 5, right, align="R", new_x="LMARGIN", new_y="NEXT")
            pdf.set_font("Helvetica", "", 9.5)
            pdf.cell(width, 5, chr(149) + " Built things that worked well", new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def _make_serif_two_page_pdf(path: str) -> None:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(False)
    for page in range(2):
        pdf.add_page()
        if page == 0:
            pdf.set_font("Times", "B", 18)
            pdf.cell(0, 9, "John Plain", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Times", "", 10)
        for i in range(30):
            pdf.cell(0, 5, f"Plain paragraph text line number {i} of the page.", new_x="LMARGIN", new_y="NEXT")
    pdf.output(path)


def test_designed_pdf_fingerprint(tmp_path) -> None:
    p = str(tmp_path / "a.pdf")
    _make_designed_pdf(p)
    s = extract_cv_style(p)
    print("FIXTURE_A_STYLE", s)
    assert s is not None
    assert s["source"] == "uploaded"
    assert abs(s["name"]["size"] - 20) <= 1
    assert s["name"]["align"] == "center"
    assert s["name"]["bold"] is True
    assert s["name"]["upper"] is True
    assert s["name"]["color"] == "#1F3864"
    assert s["heading"]["color"] == "#1F3864"
    assert s["heading"]["rule"] is True
    assert s["heading"]["upper"] is True
    assert s["entry"]["dates_align"] == "right"
    assert s["page"]["pages"] == 1
    assert s["page"]["format"] == "A4"
    assert s["bullet"] == "•"
    assert s["font"]["family"] == "sans"


def test_serif_two_page_left_aligned(tmp_path) -> None:
    p = str(tmp_path / "b.pdf")
    _make_serif_two_page_pdf(p)
    s = extract_cv_style(p)
    assert s is not None
    assert s["page"]["pages"] == 2
    assert s["font"]["family"] == "serif"
    assert s["name"]["align"] == "left"
    assert s["entry"]["dates_align"] == "inline"


def test_garbage_bytes_return_none(tmp_path) -> None:
    p = tmp_path / "bad.pdf"
    p.write_bytes(b"\x00\x01 not a pdf at all")
    assert extract_cv_style(str(p)) is None
    assert extract_cv_style(str(tmp_path / "missing.pdf")) is None


def test_normalize_fills_defaults_and_clamps() -> None:
    assert normalize_style({}) == DEFAULT_STYLE
    assert normalize_style(None) == DEFAULT_STYLE
    s = normalize_style(
        {
            "name": {"size": 900, "color": "red"},
            "body": {"size": 1},
            "page": {"margin_left": 99, "margin_top": 0, "pages": 40},
            "bullet": "x",
            "link_color": "#abcdef",
        }
    )
    assert s["name"]["size"] == 28.0
    assert s["name"]["color"] == DEFAULT_STYLE["name"]["color"]
    assert s["body"]["size"] == 8.5
    assert s["page"]["margin_left"] == 30.0
    assert s["page"]["margin_top"] == 8.0
    assert s["page"]["pages"] <= 4
    assert s["bullet"] == "•"
    assert s["link_color"] == "#ABCDEF"
