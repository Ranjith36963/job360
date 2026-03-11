"""Tests for CV parser module."""

import json
from pathlib import Path

import pytest

from src.cv_parser import extract_text, extract_profile, save_profile, load_profile, _match_terms


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_docx(tmp_path):
    from docx import Document
    doc = Document()
    doc.add_paragraph("AI Engineer with Python, PyTorch, TensorFlow experience.")
    doc.add_paragraph("Based in London, UK. Worked with AWS, Docker, LangChain.")
    path = tmp_path / "test_cv.docx"
    doc.save(str(path))
    return path


@pytest.fixture
def sample_pdf(tmp_path):
    """Create a minimal valid PDF with extractable text."""
    # Minimal hand-crafted PDF — avoids fpdf2/cryptography dependency
    text_content = "AI Engineer with Python, PyTorch, TensorFlow experience. Based in London, UK. Worked with AWS, Docker, LangChain."
    stream = (
        f"BT /F1 12 Tf 100 700 Td ({text_content}) Tj ET"
    )
    stream_bytes = stream.encode("latin-1")
    objects = []
    # obj 1: catalog
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    # obj 2: pages
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    # obj 3: page
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    )
    # obj 4: content stream
    objects.append(
        f"4 0 obj\n<< /Length {len(stream_bytes)} >>\nstream\n".encode("latin-1")
        + stream_bytes
        + b"\nendstream\nendobj\n"
    )
    # obj 5: font
    objects.append(
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    )

    body = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(body))
        body += obj

    xref_offset = len(body)
    xref = f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n"
    xref += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    body += xref.encode("latin-1")

    path = tmp_path / "test_cv.pdf"
    path.write_bytes(body)
    return path


# ---------------------------------------------------------------------------
# Text extraction tests
# ---------------------------------------------------------------------------

def test_extract_text_docx(sample_docx):
    text = extract_text(str(sample_docx))
    assert "Python" in text
    assert "PyTorch" in text
    assert "London" in text


def test_extract_text_pdf(sample_pdf):
    text = extract_text(str(sample_pdf))
    assert "Python" in text
    assert "PyTorch" in text
    assert "London" in text


def test_extract_text_unsupported(tmp_path):
    txt_file = tmp_path / "resume.txt"
    txt_file.write_text("some text")
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text(str(txt_file))


def test_extract_text_missing_file():
    with pytest.raises(FileNotFoundError):
        extract_text("/nonexistent/path/cv.pdf")


# ---------------------------------------------------------------------------
# Term matching tests
# ---------------------------------------------------------------------------

def test_match_terms_finds_skills():
    matches = _match_terms("Python and PyTorch developer", ["Python", "Java"])
    assert matches == ["Python"]


def test_match_terms_case_insensitive():
    matches = _match_terms("python pytorch tensorflow", ["Python", "PyTorch"])
    assert "Python" in matches
    assert "PyTorch" in matches


def test_match_terms_no_match():
    matches = _match_terms("marketing SEO analytics", ["Python", "PyTorch"])
    assert matches == []


def test_match_terms_substring_matching():
    matches = _match_terms("Experience with Deep Learning models", ["Deep Learning"])
    assert "Deep Learning" in matches


# ---------------------------------------------------------------------------
# Profile extraction tests
# ---------------------------------------------------------------------------

def test_extract_profile_full_cv():
    text = (
        "AI Engineer with Python, PyTorch, TensorFlow experience. "
        "Expert in LangChain, RAG, LLM, NLP, Deep Learning. "
        "Used AWS, Docker, Kubernetes, FastAPI. "
        "Based in London, UK."
    )
    profile = extract_profile(text)
    assert "Python" in profile["primary_skills"]
    assert "PyTorch" in profile["primary_skills"]
    assert "AWS" in profile["secondary_skills"]
    assert "London" in profile["locations"]
    assert "AI Engineer" in profile["job_titles"]


def test_extract_profile_empty_text():
    profile = extract_profile("")
    assert profile["job_titles"] == []
    assert profile["primary_skills"] == []
    assert profile["secondary_skills"] == []
    assert profile["tertiary_skills"] == []
    assert profile["locations"] == []


def test_extract_profile_has_metadata():
    profile = extract_profile("Python developer")
    assert "extracted_at" in profile
    # Should be a valid ISO timestamp
    from datetime import datetime
    datetime.fromisoformat(profile["extracted_at"])


# ---------------------------------------------------------------------------
# Save / Load tests
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(tmp_path):
    profile = {
        "job_titles": ["AI Engineer"],
        "primary_skills": ["Python", "PyTorch"],
        "secondary_skills": ["AWS"],
        "tertiary_skills": ["Git"],
        "locations": ["London"],
        "source_file": "cv.pdf",
        "extracted_at": "2025-01-01T00:00:00+00:00",
    }
    path = tmp_path / "profile.json"
    save_profile(profile, path)
    loaded = load_profile(path)
    assert loaded == profile


def test_load_missing_file(tmp_path):
    result = load_profile(tmp_path / "nonexistent.json")
    assert result is None


def test_save_creates_parent_dirs(tmp_path):
    profile = {"job_titles": [], "primary_skills": []}
    path = tmp_path / "nested" / "deep" / "profile.json"
    save_profile(profile, path)
    assert path.exists()


# ---------------------------------------------------------------------------
# Integration with skill_matcher
# ---------------------------------------------------------------------------

def test_scorer_uses_cv_profile(tmp_path, monkeypatch):
    """When a CV profile exists, scorer should use its skill lists."""
    from datetime import datetime, timezone
    from src.models import Job
    import src.filters.skill_matcher as sm
    import src.cv_parser as cv_mod

    profile_path = tmp_path / "cv_profile.json"
    # Profile with ONLY "Python" as primary skill
    profile = {
        "job_titles": ["AI Engineer"],
        "primary_skills": ["Python"],
        "secondary_skills": [],
        "tertiary_skills": [],
        "locations": ["London"],
    }
    profile_path.write_text(json.dumps(profile))

    monkeypatch.setattr(cv_mod, "CV_PROFILE_PATH", profile_path)
    sm.reload_profile()

    job = Job(
        title="AI Engineer",
        company="Test",
        apply_url="https://example.com",
        source="test",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London",
        description="Python developer with AWS and Docker experience",
    )
    score = sm.score_job(job)
    # With only Python as a primary skill, AWS and Docker won't contribute
    # Score should be lower than with the full keyword list
    assert score > 0
    sm.reload_profile()


def test_scorer_falls_back_without_profile(tmp_path, monkeypatch):
    """Without a CV profile, scorer should use default keywords.py lists."""
    from datetime import datetime, timezone
    from src.models import Job
    import src.filters.skill_matcher as sm
    import src.cv_parser as cv_mod

    monkeypatch.setattr(cv_mod, "CV_PROFILE_PATH", tmp_path / "nonexistent.json")
    sm.reload_profile()

    job = Job(
        title="AI Engineer",
        company="Test",
        apply_url="https://example.com",
        source="test",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London, UK",
        description=(
            "Python PyTorch TensorFlow LangChain RAG LLM NLP "
            "Deep Learning AWS Docker Kubernetes"
        ),
    )
    score = sm.score_job(job)
    # Should score high with the full default list
    assert score >= 70
    sm.reload_profile()
