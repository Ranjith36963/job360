"""CV style fingerprint: read the design of the uploaded PDF (no LLM)."""

from __future__ import annotations

import os

import pytest

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


OWNER_PDF = (
    "C:/Users/Ranjith/.claude/uploads/8b9c4a11-8507-4603-9903-fb3bb4341734/"
    "e7373fa0-Ranjith_Guruprakash_CV.pdf"
)


def test_new_keys_default_and_normalise() -> None:
    n = normalize_style({})
    assert n["entry"]["first_bold"] is True
    assert n["skills"] == {"label_bold": True}
    assert n["project_name_color"] == n["heading"]["color"]
    assert normalize_style({"project_name_color": ""})["project_name_color"] == ""
    assert normalize_style({"project_name_color": "#abcdef"})["project_name_color"] == "#ABCDEF"
    assert normalize_style({"heading": {"color": "#112233"}})["project_name_color"] == "#112233"
    assert normalize_style({"project_name_color": "red"})["project_name_color"] == DEFAULT_STYLE["heading"]["color"]


def test_render_then_extract_roundtrips_new_keys(tmp_path) -> None:
    from src.services.tailoring.cv_render import render_cv_pdf

    cv = (
        "JANE DOE\nEngineer | Things\njane@example.com\n\n"
        "EXPERIENCE\nAcme | Engineer | June 2024 – May 2025\n- Did a thing well here.\n"
        "Beta | Dev | 2023 – 2024\n- Did another thing here.\n\n"
        "PROJECTS\nAlpha | Python\n- Built alpha.\nBeta2 | Rust\n- Built beta.\n\n"
        "SKILLS\nLanguages: Python, Rust\nTools: Docker, Make\n"
    )
    path = tmp_path / "cv.pdf"
    path.write_bytes(render_cv_pdf(cv, dict(DEFAULT_STYLE)))
    got = extract_cv_style(str(path))
    assert got is not None
    assert got["entry"]["bold"] is True and got["entry"]["first_bold"] is True
    assert got["skills"]["label_bold"] is True
    assert got["project_name_color"] == got["heading"]["color"]


@pytest.mark.skipif(not os.path.exists(OWNER_PDF), reason="owner's real CV is not on this machine")
def test_owner_real_pdf_fingerprint() -> None:
    got = extract_cv_style(OWNER_PDF)
    assert got is not None
    assert got["entry"]["first_bold"] is True
    assert got["skills"]["label_bold"] is True
    assert got["project_name_color"] == got["heading"]["color"] != ""
