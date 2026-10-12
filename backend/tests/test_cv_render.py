"""Styled CV renderer (cv_render.render_cv_pdf) — checked by reading the PDF back."""

from __future__ import annotations

import copy
import io

import pytest

from src.services.tailoring.cv_render import render_cv_pdf

NAME = "RANJITH MALIGA GURUPRAKASH"
DATE = "June 2025 - September 2025"
CV = (
    "RANJITH MALIGA GURUPRAKASH\nAI Engineer | Agentic Systems\n"
    "United Kingdom | +44 7442 564327 | rahulranjith369@gmail.com\n"
    "github.com/Ranjith36963 | linkedin.com/in/ranjith-ai-engineer\n\n"
    "SUMMARY\nAI engineer who designs, ships and evaluates agentic systems.\n\n"
    "EXPERIENCE\nAI Solutions Engineer – R&D Department | Calnex Solutions | Stevenage, UK | "
    "June 2025 – September 2025\n"
    "- Built a containerised multimodal assistant in Docker, at 95% response accuracy.\n"
    "- Engineered a RAG pipeline with AWS Bedrock and ChromaDB, improving relevance by 40%.\n\n"
    "EDUCATION\nMSc Artificial Intelligence and Robotics | University of Hertfordshire | 2024"
)
STYLE = {
    "version": 1, "source": "default",
    "page": {"format": "A4", "margin_left": 15, "margin_right": 15, "margin_top": 14, "pages": 1},
    "font": {"family": "sans"},
    "name": {"size": 20, "bold": True, "color": "#1F3864", "align": "center", "upper": True},
    "headline": {"size": 11, "color": "#555555", "align": "center"},
    "contact": {"size": 9, "color": "#000000", "align": "center"},
    "heading": {"size": 11.5, "bold": True, "color": "#1F3864", "upper": True, "rule": True,
                "rule_color": "#1F3864"},
    "body": {"size": 9.5, "color": "#000000"},
    "entry": {"bold": True, "dates_align": "right"},
    "bullet": "•",
    "link_color": "#1F3864",
}
NAVY = (31 / 255, 56 / 255, 100 / 255)


def _open(data: bytes):
    import pdfplumber

    return pdfplumber.open(io.BytesIO(data))


def _rows(page):
    """Lines top to bottom as (text, chars)."""
    return [(ln["text"], ln["chars"]) for ln in page.extract_text_lines(return_chars=True)]


def _close(color, rgb) -> bool:
    return color is not None and len(color) == 3 and all(abs(a - b) < 0.02 for a, b in zip(color, rgb))


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return render_cv_pdf(CV, copy.deepcopy(STYLE))


def test_one_page_and_no_title(pdf_bytes: bytes) -> None:
    with _open(pdf_bytes) as pdf:
        assert len(pdf.pages) == 1
        assert "Curriculum Vitae" not in (pdf.pages[0].extract_text() or "")


def test_name_is_biggest_bold_navy(pdf_bytes: bytes) -> None:
    with _open(pdf_bytes) as pdf:
        rows = _rows(pdf.pages[0])
        text, chars = rows[0]
        assert text == NAME
        biggest = max(c["size"] for _, cs in rows for c in cs)
        solid = [c for c in chars if c["text"].strip()]
        assert all(abs(c["size"] - biggest) < 0.1 for c in solid)
        assert all("Bold" in c["fontname"] for c in solid)
        assert all(_close(c["non_stroking_color"], NAVY) for c in solid)


def test_heading_navy_with_rule_below(pdf_bytes: bytes) -> None:
    with _open(pdf_bytes) as pdf:
        page = pdf.pages[0]
        _, chars = next(r for r in _rows(page) if r[0] == "EXPERIENCE")
        assert all(_close(c["non_stroking_color"], NAVY) for c in chars if c["text"].strip())
        bottom = max(c["bottom"] for c in chars)
        lines = [ln for ln in page.lines if abs(ln["top"] - ln["bottom"]) < 0.5]
        assert any(0 <= ln["top"] - bottom < 6 and ln["x1"] - ln["x0"] > 400 for ln in lines)


def test_date_is_right_aligned(pdf_bytes: bytes) -> None:
    with _open(pdf_bytes) as pdf:
        page = pdf.pages[0]
        # The date was transliterated: en dash -> "-".
        row = next(r for r in _rows(page) if r[0].endswith(DATE))
        last = max(c["x1"] for c in row[1])
        right_margin = page.width - 15 * 72 / 25.4
        assert abs(last - right_margin) < 2 * 72 / 25.4


def test_bullets_are_shapes_not_dashes(pdf_bytes: bytes) -> None:
    with _open(pdf_bytes) as pdf:
        page = pdf.pages[0]
        assert not any(t.startswith(("- ", "•")) for t, _ in _rows(page))
        assert len(page.curves) + len(page.rects) >= 2


def test_links_are_clickable(pdf_bytes: bytes) -> None:
    with _open(pdf_bytes) as pdf:
        uris = [h.get("uri") or "" for h in pdf.pages[0].hyperlinks]
    assert any("linkedin.com" in u for u in uris)
    assert any("github.com" in u for u in uris)
    assert any(u.startswith("mailto:") for u in uris)


def test_long_cv_fits_or_shrinks_but_body_never_below_floor() -> None:
    exp = CV.split("EXPERIENCE\n")[1].split("\n\nEDUCATION")[0]
    long_cv = CV.replace(exp, "\n".join([exp] * 6))
    data = render_cv_pdf(long_cv, copy.deepcopy(STYLE))
    with _open(data) as pdf:
        sizes = [c["size"] for p in pdf.pages for c in p.chars if c["text"].strip()]
        assert min(sizes) >= 8.4
        assert len(pdf.pages) <= 2


def test_fewer_pages_than_target_is_fine() -> None:
    style = copy.deepcopy(STYLE)
    style["page"]["pages"] = 2
    with _open(render_cv_pdf(CV, style)) as pdf:
        assert len(pdf.pages) == 1


def test_serif_uses_times() -> None:
    style = copy.deepcopy(STYLE)
    style["font"]["family"] = "serif"
    with _open(render_cv_pdf(CV, style)) as pdf:
        fonts = {c["fontname"] for c in pdf.pages[0].chars}
    assert all("Times" in f for f in fonts)


def test_partial_style_still_renders() -> None:
    assert render_cv_pdf(CV, {})[:4] == b"%PDF"


# ── Regressions: reviewed render bugs ────────────────────────────────────────

import time  # noqa: E402

MM = 72 / 25.4
MIXED = (
    "Jane Doe\nSoftware Engineer\njane@x.com | +44 7442 564327\ngithub.com/jane\n\n"
    "Professional Summary\nEngineer who ships things for a living.\n\n"
    "Experience\nEngineer | Acme | 2020 - 2022\n- Built stuff that worked.\n\n"
    "Education\nBSc Computing | Uni | 2019"
)


def test_long_unbroken_token_renders_fast() -> None:
    blob = "a" * 60000
    start = time.perf_counter()
    for cv in (
        f"Jane Doe\nEngineer\njane@x.com | {blob}\n\nSUMMARY\nhi",
        f"{blob}\n\nSUMMARY\nhi",
        f"Jane Doe\n{blob}\n\nSUMMARY\nhi",
    ):
        assert render_cv_pdf(cv, {})[:4] == b"%PDF"
    assert time.perf_counter() - start < 2.0


def test_upper_after_latin1_does_not_crash() -> None:
    data = render_cv_pdf("Jane µ ÿ Doe\n\nSUMMARY\nhi", {})
    assert data[:4] == b"%PDF"
    data = render_cv_pdf("Jane Doe\n\nSummary ÿµ\nhi", {})
    assert data[:4] == b"%PDF"


def test_mixed_case_cv_parses_into_sections() -> None:
    from src.services.tailoring.cv_render import _parse

    header, blocks = _parse(MIXED)
    assert header == ["Jane Doe", "Software Engineer", "jane@x.com | +44 7442 564327", "github.com/jane"]
    heads = [v for k, v in blocks if k == "heading"]
    assert heads == ["Professional Summary", "Experience", "Education"]
    with _open(render_cv_pdf(MIXED, copy.deepcopy(STYLE))) as pdf:
        rows = _rows(pdf.pages[0])
        assert any(t == "EXPERIENCE" for t, _ in rows)
        body = next(cs for t, cs in rows if t.startswith("Engineer who ships"))
        assert min(c["x0"] for c in body) < 15 * MM + 4  # left aligned, not centred


def test_contact_row_has_real_spaces_and_links() -> None:
    cv = CV.replace(
        "United Kingdom | +44 7442 564327 | rahulranjith369@gmail.com",
        "rahulranjith369@gmail.com | +44 7442 564327 | United Kingdom",
    )
    with _open(render_cv_pdf(cv, copy.deepcopy(STYLE))) as pdf:
        page = pdf.pages[0]
        text = page.extract_text() or ""
        assert "rahulranjith369@gmail.com | +44 7442 564327" in text
        uris = [h.get("uri") or "" for h in page.hyperlinks]
    assert any(u == "mailto:rahulranjith369@gmail.com" for u in uris)
    assert any("github.com/Ranjith36963" in u for u in uris)


def test_huge_paragraph_stays_above_bottom_margin() -> None:
    para = " ".join(["word%d" % i for i in range(3000)])
    data = render_cv_pdf(f"Jane Doe\n\nSUMMARY\n{para}", copy.deepcopy(STYLE))
    with _open(data) as pdf:
        assert len(pdf.pages) > 1
        for p in pdf.pages:
            assert all(c["bottom"] <= p.height - 12 * MM + 0.5 for c in p.chars)


def test_wide_contact_token_stays_on_page() -> None:
    url = "https://example.com/" + "a" * 800
    data = render_cv_pdf(f"Jane Doe\nEngineer\njane@x.com | {url}\n\nSUMMARY\nhi", copy.deepcopy(STYLE))
    with _open(data) as pdf:
        for p in pdf.pages:
            assert all(c["x0"] >= 0 and c["x1"] <= p.width for c in p.chars)


def test_wide_date_does_not_overlap_entry_text() -> None:
    date = " - ".join(["June 2025"] * 20)
    data = render_cv_pdf(f"Jane Doe\n\nEXPERIENCE\nEngineer | Acme | {date}", copy.deepcopy(STYLE))
    with _open(data) as pdf:
        chars = [c for p in pdf.pages for c in p.chars if c["text"].strip()]
        assert all(c["x0"] >= 0 and c["x1"] <= pdf.pages[0].width for c in chars)
        for i, a in enumerate(chars):
            for b in chars[i + 1:]:
                ox = min(a["x1"], b["x1"]) - max(a["x0"], b["x0"])
                oy = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
                assert not (ox > 0.5 * min(a["x1"] - a["x0"], b["x1"] - b["x0"]) and oy > 2)
