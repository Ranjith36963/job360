"""Parse LinkedIn data export ZIP to extract structured career data."""

from __future__ import annotations

import csv
import io
import logging
import zipfile

from src.profile.models import CVData

logger = logging.getLogger("job360.profile.linkedin")


def _find_csv_in_zip(zf: zipfile.ZipFile, target: str) -> str | None:
    """Find a CSV by name, handling both flat and nested ZIP structures."""
    for name in zf.namelist():
        if ".." in name or name.startswith("/"):
            continue
        if name.endswith(target) or name.endswith(f"/{target}"):
            return name
    return None


def _read_csv(zf: zipfile.ZipFile, filename: str) -> list[dict]:
    """Read a CSV from the ZIP and return a list of row dicts."""
    path = _find_csv_in_zip(zf, filename)
    if not path:
        return []
    try:
        raw = zf.read(path).decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(raw))
        return list(reader)
    except Exception as e:
        logger.warning(f"Failed to read {filename}: {e}")
        return []


def _parse_positions(rows: list[dict]) -> list[dict]:
    """Extract structured position data from Positions.csv rows."""
    positions = []
    for row in rows:
        title = row.get("Title", "").strip()
        if not title:
            continue
        positions.append({
            "title": title,
            "company": row.get("Company Name", "").strip(),
            "start": row.get("Started On", "").strip(),
            "end": row.get("Finished On", "").strip(),
            "description": row.get("Description", "").strip(),
        })
    return positions


def _parse_skills(rows: list[dict]) -> list[str]:
    """Extract skill names from Skills.csv rows."""
    skills = []
    seen = set()
    for row in rows:
        name = row.get("Name", "").strip()
        if name and name.lower() not in seen:
            skills.append(name)
            seen.add(name.lower())
    return skills


def _parse_education(rows: list[dict]) -> list[dict]:
    """Extract education entries from Education.csv rows."""
    entries = []
    for row in rows:
        school = row.get("School Name", "").strip()
        if not school:
            continue
        entries.append({
            "school": school,
            "degree": row.get("Degree Name", "").strip(),
            "start": row.get("Start Date", "").strip(),
            "end": row.get("End Date", "").strip(),
            "notes": row.get("Notes", "").strip(),
        })
    return entries


def _parse_certifications(rows: list[dict]) -> list[dict]:
    """Extract certification entries from Certifications.csv rows."""
    certs = []
    for row in rows:
        name = row.get("Name", "").strip()
        if not name:
            continue
        certs.append({
            "name": name,
            "authority": row.get("Authority", "").strip(),
            "start": row.get("Started On", "").strip(),
            "end": row.get("Finished On", "").strip(),
        })
    return certs


def _parse_profile(rows: list[dict]) -> dict:
    """Extract profile summary from Profile.csv rows."""
    if not rows:
        return {"summary": "", "industry": "", "headline": ""}
    row = rows[0]
    return {
        "summary": row.get("Summary", "").strip(),
        "industry": row.get("Industry", "").strip(),
        "headline": row.get("Headline", "").strip(),
    }


def _empty_linkedin_data() -> dict:
    """Return an empty LinkedIn data structure."""
    return {"positions": [], "skills": [], "education": [], "certifications": [],
            "summary": "", "industry": "", "headline": ""}


def parse_linkedin_zip(file_path: str) -> dict:
    """Parse LinkedIn ZIP export and return structured data dict."""
    try:
        with zipfile.ZipFile(file_path, "r") as zf:
            return _parse_zip(zf)
    except (zipfile.BadZipFile, Exception) as e:
        logger.warning(f"Failed to parse LinkedIn ZIP: {e}")
        return _empty_linkedin_data()


def parse_linkedin_zip_from_bytes(content: bytes) -> dict:
    """Parse from in-memory bytes (for Streamlit file_uploader)."""
    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as zf:
            return _parse_zip(zf)
    except (zipfile.BadZipFile, Exception) as e:
        logger.warning(f"Failed to parse LinkedIn ZIP: {e}")
        return _empty_linkedin_data()


def _parse_zip(zf: zipfile.ZipFile) -> dict:
    """Internal: parse all CSVs from an open ZipFile."""
    positions = _parse_positions(_read_csv(zf, "Positions.csv"))
    skills = _parse_skills(_read_csv(zf, "Skills.csv"))
    education = _parse_education(_read_csv(zf, "Education.csv"))
    certifications = _parse_certifications(_read_csv(zf, "Certifications.csv"))
    profile = _parse_profile(_read_csv(zf, "Profile.csv"))

    return {
        "positions": positions,
        "skills": skills,
        "education": education,
        "certifications": certifications,
        "summary": profile["summary"],
        "industry": profile["industry"],
        "headline": profile["headline"],
    }


def enrich_cv_from_linkedin(cv: CVData, linkedin_data: dict) -> CVData:
    """Merge LinkedIn data into existing CVData, deduplicating."""
    # Skills
    seen_skills = {s.lower() for s in cv.skills}
    new_linkedin_skills = []
    for s in linkedin_data.get("skills", []):
        if s.lower() not in seen_skills:
            new_linkedin_skills.append(s)
            seen_skills.add(s.lower())

    # Job titles from positions
    seen_titles = {t.lower() for t in cv.job_titles}
    for pos in linkedin_data.get("positions", []):
        title = pos.get("title", "")
        if title and title.lower() not in seen_titles:
            cv.job_titles.append(title)
            seen_titles.add(title.lower())

    # Education
    existing_edu = {e.lower() for e in cv.education}
    for edu in linkedin_data.get("education", []):
        entry = f"{edu.get('degree', '')} - {edu.get('school', '')}".strip(" -")
        if entry and entry.lower() not in existing_edu:
            cv.education.append(entry)
            existing_edu.add(entry.lower())

    # Certifications
    existing_certs = {c.lower() for c in cv.certifications}
    for cert in linkedin_data.get("certifications", []):
        name = cert.get("name", "")
        if name and name.lower() not in existing_certs:
            cv.certifications.append(name)
            existing_certs.add(name.lower())

    # Summary — only fill if empty
    if not cv.summary and linkedin_data.get("summary"):
        cv.summary = linkedin_data["summary"]

    # Store LinkedIn-specific fields
    cv.linkedin_positions = linkedin_data.get("positions", [])
    cv.linkedin_skills = new_linkedin_skills
    cv.linkedin_industry = linkedin_data.get("industry", "")

    return cv
