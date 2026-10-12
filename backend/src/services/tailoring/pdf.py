"""ATS-friendly PDF rendering (spec guardrail #5).

Deliberately plain: one column, one standard font, real selectable text, no tables
or graphics — so applicant-tracking software parses it cleanly. fpdf2 is
lazy-imported (heavy-dep discipline, root rules #11/#16).
"""

from __future__ import annotations

import unicodedata

from src.utils.loop_guard import cpu_bound

_TRANSLIT: dict[str, str] = {}
for _chars, _repl in (
    ("–—―‒‐‑−⁃﹘﹣－", "-"),
    ("‘’‚‛′ʼ", "'"),
    ("“”„‟″", '"'),
    ("•●▪◦‣■∙○", "-"),
    ("…", "..."),
    ("→⇒⟶", "->"),
    ("←⇐", "<-"),
    ("≥", ">="),
    ("≤", "<="),
    ("≠", "!="),
    ("≈", "~"),
    ("™", "(TM)"),
    ("€", "EUR"),
    ("              　", " "),
    ("​‌‍⁠﻿­", ""),
):
    for _c in _chars:
        _TRANSLIT[_c] = _repl


def _to_latin1(text: str) -> str:
    """Make text safe for the latin-1 core font without ever emitting "?".

    Typographic characters map to faithful ASCII (en dash -> "-", curly quotes ->
    straight, bullet -> "-"). Other non-latin-1 characters fall back to NFKD with
    combining marks dropped; anything still unencodable is dropped.
    """
    out: list[str] = []
    for ch in text or "":
        for c in _TRANSLIT.get(ch, ch):
            if ord(c) < 256:
                out.append(c)
                continue
            for d in unicodedata.normalize("NFKD", c):
                if not unicodedata.combining(d) and ord(d) < 256:
                    out.append(d)
    return "".join(out)


@cpu_bound
def render_pdf(text: str, title: str = "") -> bytes:
    """Render plain text to a clean, single-column, machine-readable PDF (bytes)."""
    from fpdf import FPDF  # lazy import
    from fpdf.enums import XPos, YPos

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=18, top=16, right=18)
    pdf.add_page()

    # Core font Helvetica is latin-1 only; _to_latin1 transliterates typographic
    # characters so they never turn into "?".
    _safe = _to_latin1

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
