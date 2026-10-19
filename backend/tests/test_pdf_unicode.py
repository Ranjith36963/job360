"""Typographic characters must survive PDF rendering as ASCII, never as '?'."""

from __future__ import annotations

from src.services.tailoring.pdf import _to_latin1, render_pdf


def test_typographic_chars_transliterated() -> None:
    src = (
        "R&D – June 2025 – September 2025 — x ‑ y − z "
        "‘a’ “b” • ● ▪ ◦ ‣ wait… "
        "a → b ⇒ c ← d ≥ 1 ≤ 2 ™ €5 "
        "n b t​z"
    )
    out = _to_latin1(src)
    assert "?" not in out
    assert "June 2025 - September 2025" in out
    assert "'a'" in out and '"b"' in out
    assert "wait..." in out
    assert "a -> b -> c <- d >= 1 <= 2 (TM) EUR5" in out
    assert "n b tz" in out


def test_latin1_chars_survive() -> None:
    s = "café £ × · ©"
    assert _to_latin1(s) == s


def test_fallback_strips_marks_and_drops_unknown() -> None:
    assert _to_latin1("ő") == "o"
    out = _to_latin1("a中b")
    assert out == "ab" and "?" not in out


def test_render_pdf_end_to_end() -> None:
    pdf = render_pdf(
        "Engineer – R&D • 3.2s → 0.8s “quoted”",
        title="Curriculum Vitae",
    )
    assert pdf.startswith(b"%PDF")
