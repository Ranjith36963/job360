"""S1 of the CV benchmark: a CV line's evidence comes from the job, project or raw block it sits under.

Under rules 3 a true number could move to another job: "improving relevance by 40%" (Northwind's) written under
Tidewater, quoting Northwind's own bullet, was stored strong. The line's record is set by the header line above it
(``cv_records.line_records``); its quotes must lie inside that record's own text (``evidence_wrong_record``).
"""
from __future__ import annotations

from typing import Any

import pytest

from src.services.applications import cv_records, cv_review
from src.services.profile.models import CVData, UserProfile
from tests._cv_review import ats_cv
from tests.test_cv_review import _setup, _stored
from tests.test_cv_review_held_lines import B1, B2, B3, CV_DATA, RAW, _seed, _sha

ORBITLY = "Orbitly (orbitly.uk) | Founder & AI-Native Engineer | United Kingdom | Feb 2026 - Present"
NORTHWIND = "Northwind Labs | AI Solutions Engineer – R&D Department | Stevenage, United Kingdom | Jun 2025 - Sep 2025"
TIDEWATER = "Tidewater | AI/ML Engineer Intern | United Kingdom (Remote) | Oct 2024 - Jan 2025"


def _body(*blocks: tuple[str, str]) -> str:
    return "Ada Lovelace\nEXPERIENCE\n" + "".join(f"{h}\n- {b}\n" for h, b in blocks)


def _cv(*blocks: tuple[str, str]) -> str:
    return ats_cv(_body(*blocks))  # rules 6: ATS-ready, the blocks' own lines keep their review line numbers


def _rows(text: str, profile: UserProfile) -> list[dict[str, Any]]:
    """Each line quoting itself (a bullet quotes the profile's own sentence, word for word)."""
    return [{"n": n, "verdict": "supported", "evidence": [ln.strip().lstrip(cv_review._BULLETS + " \t")]}
            for n, ln in enumerate(cv_review.content_lines(text, profile), 1)]


async def _verdict(client: Any, text: str, profile: UserProfile) -> tuple[str, list[str]]:
    app_id, cv_id = await _setup(client, cv=text)
    got = await _stored(client, app_id, {"artifact_id": cv_id, "sha256": _sha(text), "confidence": "strong",
                                         "lines": _rows(text, profile), "reviewer": "fresh helper"})
    return got["confidence"], got["downgraded"]


@pytest.mark.asyncio
async def test_a_true_number_quoted_from_another_job_is_weak(authenticated_async_context, fixture_user_id):
    profile = UserProfile(cv_data=CVData(**CV_DATA))
    async with authenticated_async_context() as client:
        _seed(fixture_user_id, profile)
        moved = _cv((ORBITLY, B1), (NORTHWIND, B3), (TIDEWATER, B2))  # Northwind's 40% under Tidewater
        got = await _verdict(client, moved, profile)
        assert got[0] == "weak" and "evidence_wrong_record" in got[1], got


@pytest.mark.asyncio
async def test_quote_inside_own_record_stays_strong(authenticated_async_context, fixture_user_id):
    profile = UserProfile(cv_data=CVData(**CV_DATA))
    async with authenticated_async_context() as client:
        _seed(fixture_user_id, profile)
        text = _cv((ORBITLY, B1), (NORTHWIND, B2), (TIDEWATER, B3))
        assert await _verdict(client, text, profile) == ("strong", [])


@pytest.mark.asyncio
async def test_bullet_under_an_unheld_experience_header_is_weak(authenticated_async_context, fixture_user_id):
    profile = UserProfile(cv_data=CVData(**CV_DATA))
    unheld = "Zenith Robotics | Founder & AI-Native Engineer | Feb 2026 - Present"  # an invented employer
    assert cv_records.line_records(_body((unheld, B1)), profile) == [None, frozenset()]
    async with authenticated_async_context() as client:
        _seed(fixture_user_id, profile)
        got = await _verdict(client, _cv((unheld, B1)), profile)
        assert got[0] == "weak" and "evidence_wrong_record" in got[1], got


@pytest.mark.asyncio
async def test_raw_block_quote_counts_for_its_header_only(authenticated_async_context, fixture_user_id):
    """A profile with raw text only: a header's block runs from its raw header line to the next one."""
    profile = UserProfile(cv_data=CVData(name="Ada Lovelace", raw_text=RAW))
    header = "Northwind Labs | AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage"
    got = cv_records.line_records(_cv((header, B2)), profile)
    assert got[0] is None and len(got[1] or ()) == 1, got
    texts = cv_records.record_texts(profile)[next(iter(got[1] or ()))]
    assert texts == [header, B2], texts  # the block stops at the next raw header
    async with authenticated_async_context() as client:
        _seed(fixture_user_id, profile)
        assert await _verdict(client, _cv((header, B2)), profile) == ("strong", [])
        got = await _verdict(client, _cv((header, B1)), profile)  # Orbitly's raw bullet under Northwind
        assert got[0] == "weak" and "evidence_wrong_record" in got[1], got


def test_sections_reset_the_record_and_other_sections_are_free():
    profile = UserProfile(cv_data=CVData(**CV_DATA))
    text = (f"Ada Lovelace\nSUMMARY\n{B2}\nEXPERIENCE\n{NORTHWIND}\n- {B2}\nPROJECTS\n"
            f"Support Agent | LangGraph, MCP, Gmail, Slack\n- Support agent that drafts replies.\nSKILLS\n- {B1}\n")
    got = cv_records.line_records(text, profile)
    assert got[0] is None and got[1] is None and got[3] is None  # the summary and each header line: no record
    assert "cv_positions[1]" in (got[2] or ()) and all(k.startswith("raw_text:") for k in got[2] - {"cv_positions[1]"})
    assert "cv_projects[0]" in (got[4] or ()) and all(k.startswith("raw_text:") for k in got[4] - {"cv_projects[0]"})
    assert got[5] is None  # under SKILLS: free again
    # the PDF's own header shape holds only its raw record, yet it NAMES the role (production CV 1060): both count
    pdf = "Northwind Labs | AI Solutions Engineer, R&D Jun 2025 – Sep 2025 | Stevenage"
    assert "cv_positions[1]" in (cv_records.line_records(_cv((pdf, B2)), profile)[1] or ())
    # and the structured shape reaches the raw block of the same job
    assert any(k.startswith("raw_text:") for k in cv_records.line_records(_cv((NORTHWIND, B2)), profile)[1] or ())
