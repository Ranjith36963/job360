"""Shared inputs for the cv_reviewed tests that prove the ROW checks (rules <= 5) with the rules-6 checks ON.

Rules 6 (cv_review.ats_and_jd) judge the CV's ATS format (cv_format: experience, skills and education headings, a
headline or summary, a complete contact line, 700-9000 characters, no markup) and the ad's requirements
(review_jd). Those checks are never switched off in a test: a test that is not about them gives them honest input.

- ``ATS_TAIL`` closes a short CV: the SUMMARY, SKILLS and EDUCATION sections it lacks, 3 headings and
  ``len(ATS_LINES)`` content lines. Append it AFTER the CV's own lines, so their review line numbers stay what they were.
- ``ats_cv(text)`` adds a contact line under the name and the tail after the lines (a minimal CV's whole fix).
- ``ATS_RAW`` is the profile text that says every word of the tail (append it to the profile's ``raw_text``), so each
  tail line is quotable and its headings are exempt from review rows.
- ``ats_rows(first_n)`` are the rows for the tail's lines, each quoting itself, starting at review line ``first_n``.
- ``blank_ad`` empties an application's frozen ad: ``jd_no_ad`` is stored, never weak (the ad is proven end to end in
  tests/test_cv_review_benchmark.py).
"""
from __future__ import annotations

from typing import Any

ATS_LINES = (
    "Data engineer who designs reliable batch pipelines, warehouse models and data quality monitoring for analytics "
    "teams, working mostly in Python and Snowflake.",
    "Daily tools are Python for pipelines, Snowflake for warehousing, SQL for analysis, dbt for models, Airflow for "
    "scheduling and Git for review.",
    "Comfortable with code review, writing runbooks, pairing with analysts and explaining data models to "
    "non-technical colleagues.",
    "BSc Computer Science, University of Leeds, graduated in 2024 with a final project on automated data quality "
    "checks for warehouse tables.",
)
ATS_TAIL = f"SUMMARY\n{ATS_LINES[0]}\nSKILLS\n{ATS_LINES[1]}\n{ATS_LINES[2]}\nEDUCATION\n{ATS_LINES[3]}\n"
ATS_RAW = ATS_TAIL


CONTACT = "ada@example.com | +44 7700 900123 | linkedin.com/in/ada"


def ats_cv(text: str) -> str:
    """A minimal CV ("Name\\nEXPERIENCE\\n...") made ATS-ready: a contact line under the name (exempt from rows) and
    :data:`ATS_TAIL` after the CV's own lines (they keep their review line numbers)."""
    name, _, rest = text.partition("\n")
    return f"{name}\n{CONTACT}\n{rest}" + ("" if rest.endswith("\n") else "\n") + ATS_TAIL


def ats_pairs() -> list[dict[str, str]]:
    """``{cv_line, profile_quote, field_path}`` for each tail line (the shape of the tests' L1 / L2)."""
    return [{"cv_line": ln, "profile_quote": ln, "field_path": "cv_data.raw_text"} for ln in ATS_LINES]


def ats_rows(first_n: int) -> list[dict[str, Any]]:
    """Rows ``{n, verdict, evidence}`` for the tail's lines when the first one is review line ``first_n``."""
    return [{"n": first_n + i, "verdict": "supported", "evidence": [ln]} for i, ln in enumerate(ATS_LINES)]


async def blank_ad(application_id: int) -> None:
    """Empty the application's frozen ad text: the cv_reviewed ``jd`` block is then ``{"state": "jd_no_ad"}``."""
    from src.api import dependencies as api_deps

    db = await api_deps.get_db()
    await db._db.execute("UPDATE applications SET job_description_snapshot = '' WHERE id = ?", (application_id,))
    await db._db.commit()
