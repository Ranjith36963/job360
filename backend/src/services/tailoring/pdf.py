"""ATS-friendly PDF rendering (spec guardrail #5).

Deliberately plain: one column, one standard font, real selectable text, no tables
or graphics — so applicant-tracking software parses it cleanly. fpdf2 is
lazy-imported (heavy-dep discipline, root rules #11/#16).
"""

from __future__ import annotations

from src.utils.loop_guard import cpu_bound


@cpu_bound
def render_pdf(text: str, title: str = "") -> bytes:
    """Render plain text to a clean, single-column, machine-readable PDF (bytes)."""
    from fpdf import FPDF  # lazy import
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=18, top=16, right=18)
    pdf.add_page()

    # Core font Helvetica is latin-1 only; sanitise so odd unicode never crashes the
    # render (the text layer stays fully machine-readable either way).
    def _safe(s: str) -> str:
        return (s or "").encode("latin-1", "replace").decode("latin-1")

    # new_x=LMARGIN, new_y=NEXT after every cell so the next line starts back at the
    # left margin (otherwise fpdf2 leaves x at the right edge → 0-width next cell).
    if title:
        pdf.set_font("Helvetica", style="B", size=14)
        pdf.multi_cell(0, 8, _safe(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(2)

    pdf.set_font("Helvetica", size=11)
    for line in _safe(text).split("\n"):
        # Blank lines still need height so paragraph spacing survives.
        pdf.multi_cell(0, 6, line if line.strip() else " ",
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    out = pdf.output()  # fpdf2 v2 returns a bytearray
    return bytes(out)
