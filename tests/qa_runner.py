"""Autonomous QA runner — loads real human CVs, runs search pipeline, validates.

Usage:
    python tests/qa_runner.py                    # Run all 10 CVs
    python tests/qa_runner.py nurse              # Run single CV
    python tests/qa_runner.py --list             # List available CVs
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
import time
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

QA_PROFILES_DIR = PROJECT_ROOT / "tests" / "qa_profiles"
DATA_DIR = PROJECT_ROOT / "data"
RESULTS_DIR = DATA_DIR / "qa_results"


def _parse_plain_text_cv(text: str) -> dict:
    """Parse a plain-text CV into profile JSON structure.

    Handles real human CVs with varied formatting — extracts skills,
    titles, education by looking for section headers and content patterns.
    """
    lines = text.strip().split("\n")

    # Extract name (first non-empty line)
    name = ""
    for line in lines:
        if line.strip():
            name = line.strip()
            break

    # Find section boundaries
    section_patterns = {
        "skills": r"(?i)^(skills|technical skills|core competencies|key skills)",
        "experience": r"(?i)^(experience|work experience|professional experience|employment)",
        "education": r"(?i)^(education|qualifications|academic)",
        "certifications": r"(?i)^(certifications?|certificates?|courses?|accreditations?)",
        "summary": r"(?i)^(summary|profile|about me|objective|personal statement)",
    }

    sections: dict[str, str] = {}
    current_section = "header"
    section_text: list[str] = []

    for line in lines:
        matched_section = None
        for sec_name, pattern in section_patterns.items():
            if re.match(pattern, line.strip()):
                matched_section = sec_name
                break

        if matched_section:
            if section_text:
                sections[current_section] = "\n".join(section_text)
            current_section = matched_section
            section_text = []
        else:
            section_text.append(line)

    if section_text:
        sections[current_section] = "\n".join(section_text)

    # Extract skills from skills section
    skills: list[str] = []
    if "skills" in sections:
        skill_text = sections["skills"]
        # Try comma/pipe/bullet separation
        for sep in [",", "|", "•", "·", ";"]:
            if sep in skill_text:
                skills = [s.strip() for s in skill_text.split(sep) if len(s.strip()) > 1]
                break
        if not skills:
            # Line-by-line skills
            skills = [l.strip().lstrip("- ") for l in skill_text.split("\n")
                      if l.strip() and len(l.strip()) > 2 and len(l.strip()) < 60]

    # Extract job titles from experience section
    titles: list[str] = []
    if "experience" in sections:
        exp_text = sections["experience"]
        # Pattern: "Title, Company" or "Title | Company" or "Title at Company"
        title_patterns = [
            r"^([A-Z][^,\n]{3,40}),\s*[A-Z]",  # "Senior Developer, Google"
            r"^([A-Z][^|\n]{3,40})\s*[|]",       # "Senior Developer | Google"
            r"^([A-Z][^@\n]{3,40})\s+at\s+",     # "Senior Developer at Google"
        ]
        for line in exp_text.split("\n"):
            line = line.strip()
            for pat in title_patterns:
                m = re.match(pat, line)
                if m:
                    title = m.group(1).strip()
                    # Skip date-like lines
                    if not re.match(r"^\d{2}/\d{4}", title):
                        titles.append(title)
                    break

    # Extract education
    education: list[str] = []
    structured_education: list[dict] = []
    if "education" in sections:
        edu_text = sections["education"]
        degree_re = re.compile(
            r"(Master of \w+|Bachelor of \w+|MSc|BSc|BA|MA|PhD|BEng|MEng"
            r"|Master's|Bachelor's|Doctorate|PGCE|MBA|LLM|HND)",
            re.IGNORECASE,
        )
        for line in edu_text.split("\n"):
            m = degree_re.search(line)
            if m:
                education.append(line.strip())
                # Try to parse structured
                degree = m.group(1)
                field = ""
                field_m = re.search(r"(?:in|of|–|-)\s+(.+?)(?:,|\||\(|$)", line[m.end():])
                if field_m:
                    field = field_m.group(1).strip()
                institution = ""
                inst_m = re.search(r"(?:University|College|School|Institute)\s+(?:of\s+)?\w+", line)
                if inst_m:
                    institution = inst_m.group(0).strip()
                structured_education.append({
                    "degree": degree,
                    "field_of_study": field,
                    "institution": institution,
                    "year": None,
                })

    # Extract certifications
    certifications: list[str] = []
    if "certifications" in sections:
        for line in sections["certifications"].split("\n"):
            line = line.strip().lstrip("- •")
            if line and len(line) > 5:
                certifications.append(line)

    # Extract summary
    summary = sections.get("summary", "").strip()[:500]

    # Compute seniority from titles
    seniority = "mid"
    all_text_lower = text.lower()
    if any(w in all_text_lower for w in ["senior", "lead", "principal", "head of", "director"]):
        seniority = "senior"
    elif any(w in all_text_lower for w in ["junior", "graduate", "intern", "entry"]):
        seniority = "entry"

    return {
        "cv_data": {
            "raw_text": text,
            "skills": skills[:30],
            "job_titles": titles[:6],
            "education": education,
            "certifications": certifications,
            "summary": summary,
            "structured_education": structured_education,
            "work_experiences": [],
            "projects": [],
            "total_experience_months": 48,
            "computed_seniority": seniority,
            "linkedin_positions": [],
            "linkedin_skills": [],
            "linkedin_industry": "",
            "github_languages": {},
            "github_topics": [],
            "github_skills_inferred": [],
        },
        "preferences": {
            "target_job_titles": titles[:6],
            "additional_skills": [],
            "excluded_skills": [],
            "preferred_locations": ["London", "Remote", "UK"],
            "industries": [],
            "salary_min": None,
            "salary_max": None,
            "work_arrangement": "",
            "experience_level": seniority,
            "negative_keywords": ["intern"] if seniority != "entry" else [],
            "about_me": summary[:200],
        },
    }


def _save_profile(profile_data: dict) -> None:
    """Save profile to data/user_profile.json."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "user_profile.json").write_text(
        json.dumps(profile_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )


async def _run_search() -> dict:
    """Run the search pipeline and return stats."""
    import gc
    from src.main import run_search
    try:
        return await run_search(no_notify=True)
    finally:
        # Force close any lingering DB connections
        gc.collect()


async def _run_validation(per_source: int = 2) -> dict:
    """Run validation and return JSON results."""
    from src.validation.sampler import sample_jobs
    from src.validation.checker import validate_job, aggregate_by_source
    from src.validation.report import generate_validation_json
    import aiohttp

    db_path = str(DATA_DIR / "jobs.db")
    jobs = await sample_jobs(db_path, per_source=per_source, days=7)
    if not jobs:
        return {"overall_confidence": 0, "total_checked": 0, "per_source": {}}

    sem = asyncio.Semaphore(3)
    async with aiohttp.ClientSession() as session:
        async def _check(job):
            async with sem:
                return await validate_job(session, job)
        results = await asyncio.gather(*[_check(j) for j in jobs])

    results = list(results)
    sources = aggregate_by_source(results)
    return generate_validation_json(results, sources)


def _clear_data() -> None:
    """Clear DB, logs, exports, reports for fresh run."""
    import gc
    import time as _time

    # Force garbage collection to release any lingering DB connections
    gc.collect()

    for pattern in ["jobs.db", "jobs.db-wal", "jobs.db-shm",
                     "exports/*.csv", "logs/job360.log",
                     "reports/report_*.md", "reports/validation_*.md",
                     "reports/validation_*.json"]:
        for f in DATA_DIR.glob(pattern):
            for attempt in range(3):
                try:
                    f.unlink(missing_ok=True)
                    break
                except PermissionError:
                    gc.collect()
                    _time.sleep(1)  # Wait for file lock to release


def run_qa_for_cv(cv_name: str, cv_path: Path) -> dict:
    """Run full QA cycle for a single CV."""
    import gc
    print(f"\n{'='*60}")
    print(f"QA: {cv_name}")
    print(f"{'='*60}")

    # 1. Parse CV
    cv_text = cv_path.read_text(encoding="utf-8")
    profile = _parse_plain_text_cv(cv_text)
    print(f"  Parsed: {len(profile['cv_data']['skills'])} skills, "
          f"{len(profile['cv_data']['job_titles'])} titles, "
          f"{len(profile['cv_data']['education'])} education")

    # 2. Clear data (with retry for Windows file locks)
    gc.collect()
    _clear_data()

    # 3. Save profile
    _save_profile(profile)
    print("  Profile saved")

    # 4. Run search
    print("  Running search pipeline...")
    t0 = time.time()
    stats = asyncio.run(_run_search())
    search_time = time.time() - t0
    total = stats.get("total_found", 0)
    new = stats.get("new_jobs", 0)
    print(f"  Search done: {total} found, {new} stored ({search_time:.0f}s)")

    # 5. Validate
    if new > 0:
        print("  Running validation...")
        val = asyncio.run(_run_validation(per_source=2))
        confidence = val.get("overall_confidence", 0)
        sources_val = len(val.get("per_source", {}))
        print(f"  Validation: {confidence:.0%} confidence ({sources_val} sources)")
    else:
        val = {"overall_confidence": 0, "total_checked": 0, "per_source": {}}
        print("  No jobs to validate")

    # 6. Compile results
    result = {
        "cv_name": cv_name,
        "skills_count": len(profile["cv_data"]["skills"]),
        "titles": profile["cv_data"]["job_titles"],
        "search_time_s": round(search_time, 1),
        "total_found": total,
        "new_stored": new,
        "sources_with_jobs": stats.get("sources_queried", 0),
        "validation_confidence": val.get("overall_confidence", 0),
        "per_source_confidence": {
            k: v.get("confidence", 0)
            for k, v in val.get("per_source", {}).items()
        },
    }

    return result


def main():
    parser = argparse.ArgumentParser(description="Job360 QA Runner")
    parser.add_argument("cv", nargs="?", help="CV name to test (without .txt)")
    parser.add_argument("--list", action="store_true", help="List available CVs")
    args = parser.parse_args()

    cv_files = sorted(QA_PROFILES_DIR.glob("*.txt"))
    cv_files = [f for f in cv_files if f.name != "bristol_cvs_raw.txt"]

    if args.list:
        print("Available CVs:")
        for f in cv_files:
            print(f"  {f.stem}")
        return

    if args.cv:
        cv_files = [f for f in cv_files if f.stem == args.cv]
        if not cv_files:
            print(f"CV not found: {args.cv}")
            return

    # Run QA for each CV
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    all_results: list[dict] = []

    for cv_path in cv_files:
        try:
            result = run_qa_for_cv(cv_path.stem, cv_path)
            all_results.append(result)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            all_results.append({"cv_name": cv_path.stem, "error": str(exc)})

    # Summary
    print(f"\n{'='*60}")
    print("QA SUMMARY")
    print(f"{'='*60}")
    print(f"{'CV':<25} {'Jobs':>6} {'Stored':>7} {'Sources':>8} {'Confidence':>11}")
    print("-" * 60)
    for r in all_results:
        if "error" in r:
            print(f"{r['cv_name']:<25} ERROR: {r['error'][:30]}")
        else:
            conf = f"{r['validation_confidence']:.0%}" if r['validation_confidence'] else "N/A"
            print(f"{r['cv_name']:<25} {r['total_found']:>6} {r['new_stored']:>7} "
                  f"{r['sources_with_jobs']:>8} {conf:>11}")

    # Save results
    results_path = RESULTS_DIR / "qa_results.json"
    results_path.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    print(f"\nResults saved: {results_path}")


if __name__ == "__main__":
    main()
