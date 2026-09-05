"""Bootstrap developer smoke test for Job360.

Runs the end-to-end happy path against a locally running FastAPI backend to
prove a fresh clone is wired correctly. Zero backend-module imports — only
``httpx`` + ``fpdf2`` + stdlib — so this script runs even before the backend
package is installed in the active interpreter.

Workflow:
  1. GET /api/health (bail early if backend not reachable)
  2. Generate a tiny PDF CV with fpdf2
  3. POST /api/auth/register with a timestamp-suffixed email (idempotent reruns)
  4. POST /api/profile multipart (CV + preferences JSON form field)
  5. POST /api/jobs/bring with a small ad (the product path: store + birth
     an Application; nothing is scored)
  6. GET /api/applications/job/{job_id} and print the application id + status

Usage:
    python scripts/bootstrap_dev.py
    python scripts/bootstrap_dev.py --api-url http://localhost:8000

Requires: ``pip install httpx fpdf2``
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone

import httpx
from fpdf import FPDF
from fpdf.enums import XPos, YPos


def banner(step: int, msg: str) -> None:
    print(f"==> Step {step}: {msg}", flush=True)


def fail(msg: str, resp: httpx.Response | None = None) -> None:
    print(f"[bootstrap] FAILED: {msg}", file=sys.stderr, flush=True)
    if resp is not None:
        print(f"    HTTP {resp.status_code}: {resp.text[:500]}", file=sys.stderr, flush=True)
    sys.exit(1)


def make_cv_pdf_bytes() -> bytes:
    """Render a tiny plain-text CV PDF in memory. Mirrors the
    ``_make_plain_cv_pdf`` helper in tests/test_linkedin_github.py."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in [
        "Jane Bootstrap",
        "jane.bootstrap@example.com",
        "Senior ML Engineer",
        "",
        "Skills: Python, FastAPI, PyTorch, Docker, AWS.",
        "",
        "Acme AI, 2020-2024: Built RAG pipelines and LLM fine-tuning stacks.",
        "University of Cambridge, BSc Computer Science.",
    ]:
        pdf.cell(0, 6, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    # fpdf2's .output() returns a bytearray when no path is passed.
    buf = io.BytesIO()
    pdf.output(buf)
    return buf.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000",
        help="Backend base URL (default: http://localhost:8000)",
    )
    args = parser.parse_args()

    base = args.api_url.rstrip("/")

    # Persist cookies across calls via a single Client instance.
    with httpx.Client(base_url=base, timeout=30.0, follow_redirects=True) as client:
        # ----- Step 1: health --------------------------------------------
        banner(1, f"Health check against {base}/api/health")
        try:
            r = client.get("/api/health")
        except httpx.HTTPError as e:
            fail(f"Cannot reach backend at {base}. Is it running? ({e})")
        if r.status_code != 200:
            fail("Health check returned non-200", r)
        print(f"    ok: {r.json()}", flush=True)

        # ----- Step 2: build CV ------------------------------------------
        banner(2, "Generating in-memory PDF CV")
        cv_bytes = make_cv_pdf_bytes()
        print(f"    ok: {len(cv_bytes)} bytes", flush=True)

        # ----- Step 3: register -------------------------------------------
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        email = f"bootstrap+{ts}@job360.local"
        password = "bootstrap-s3cret-pw"
        banner(3, f"Registering user {email}")
        r = client.post(
            "/api/auth/register",
            json={"email": email, "password": password},
        )
        if r.status_code != 201:
            fail("Register failed", r)
        user = r.json()
        print(f"    ok: user_id={user.get('id')}", flush=True)
        if not client.cookies.get("job360_session"):
            fail("Register did not set job360_session cookie")

        # ----- Step 4: upload profile ------------------------------------
        banner(4, "Uploading CV + preferences")
        prefs = {
            "target_job_titles": ["Senior Software Engineer"],
            "preferred_locations": ["London", "Remote"],
            "additional_skills": [],
        }
        files = {"cv": ("bootstrap_cv.pdf", cv_bytes, "application/pdf")}
        data = {"preferences": json.dumps(prefs)}
        r = client.post("/api/profile", files=files, data=data)
        if r.status_code != 200:
            fail("Profile upload failed", r)
        profile = r.json()
        summary = profile.get("summary") or {}
        print(
            f"    ok: skills_count={summary.get('skills_count')} " f"job_titles={summary.get('job_titles')}",
            flush=True,
        )

        # ----- Step 5: bring a job --------------------------------------
        banner(5, "Bringing a job (POST /api/jobs/bring)")
        r = client.post(
            "/api/jobs/bring",
            json={
                "title": "Senior Software Engineer",
                "company": "Bootstrap Ltd",
                "location": "London, UK",
                "description": "Python, FastAPI and Postgres. Remote-friendly. "
                "This ad exists only to prove a fresh clone is wired end to end.",
            },
        )
        if r.status_code != 200:
            fail("Bring failed", r)
        brought = r.json()
        application_id = brought.get("application_id")
        job_id = (brought.get("job") or {}).get("id")
        if not application_id or not job_id:
            fail("Bring response missing application_id / job.id")
        print(f"    ok: job_id={job_id} application_id={application_id} existing={brought.get('existing')}", flush=True)

        # ----- Step 6: read the application back ------------------------
        banner(6, f"Reading /api/applications/job/{job_id}")
        r = client.get(f"/api/applications/job/{job_id}")
        if r.status_code != 200:
            fail("Application read failed", r)
        application = r.json()
        if application.get("id") != application_id:
            fail(f"Application mismatch: bring said {application_id}, read back {application.get('id')}")

    print(f"Bootstrap complete. application_id={application_id} status={application.get('status')}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
