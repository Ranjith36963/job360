"""One-shot path-fixer for doc files after the backend/+frontend/src
restructure. Rewrites stale `src/`, `tests/`, `data/`, `frontend/{lib,app,components}/`,
and `src/sources/<name>.py` references to match the post-restructure layout.

Run once across all stale docs, then delete.
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TARGETS = [
    REPO / "ARCHITECTURE.md",
    REPO / "README.md",
    REPO / "STATUS.md",
    REPO / "DEADCODE.md",
    REPO / "docs" / "superpowers" / "plans" / "2026-04-07-fastapi-backend.md",
]

# Same category map used by split_sources_by_category.py
CATEGORIES = {
    "apis_keyed": [
        "adzuna", "careerjet", "findwork", "google_jobs",
        "jooble", "jsearch", "reed",
    ],
    "apis_free": [
        "aijobs", "arbeitnow", "devitjobs", "himalayas", "hn_jobs",
        "jobicy", "landingjobs", "remoteok", "remotive", "yc_companies",
    ],
    "ats": [
        "ashby", "greenhouse", "lever", "personio", "pinpoint",
        "recruitee", "smartrecruiters", "successfactors", "workable", "workday",
    ],
    "feeds": [
        "biospace", "findajob", "jobs_ac_uk", "nhs_jobs",
        "realworkfromanywhere", "uni_jobs", "workanywhere", "weworkremotely",
    ],
    "scrapers": [
        "aijobs_ai", "aijobs_global", "bcs_jobs", "climatebase",
        "eightykhours", "jobtensor", "linkedin",
    ],
    "other": ["hackernews", "indeed", "nofluffjobs", "nomis", "themuse"],
}
name_to_cat = {name: cat for cat, names in CATEGORIES.items() for name in names}


def rewrite(content: str) -> tuple[str, int]:
    """Apply all path rewrites to a document string. Returns (new_content, edit_count)."""
    edits = 0

    # 1. Source files first (most specific) — insert category subfolder.
    for name, cat in name_to_cat.items():
        old = f"src/sources/{name}.py"
        new = f"src/sources/{cat}/{name}.py"
        if old in content:
            edits += content.count(old)
            content = content.replace(old, new)

    # 2. requirements.txt → backend/pyproject.toml (deps were merged)
    content, n = re.subn(
        r"(?<![\w/])requirements\.txt(?![\w])",
        "backend/pyproject.toml",
        content,
    )
    edits += n

    # 3. Frontend subdirs — insert `src/` after `frontend/`.
    for sub in ["lib", "app", "components"]:
        old = f"frontend/{sub}/"
        new = f"frontend/src/{sub}/"
        if old in content:
            edits += content.count(old)
            content = content.replace(old, new)

    # 4. Backend top-level: src/, tests/, data/ → backend/...
    #    Negative lookbehind prevents double-prefixing anything already under backend/.
    for prefix in ["src/", "tests/", "data/"]:
        pattern = rf"(?<!backend/)(?<![\w/]){re.escape(prefix)}"
        content, n = re.subn(pattern, f"backend/{prefix}", content)
        edits += n

    return content, edits


def main() -> None:
    total_files = 0
    total_edits = 0
    for target in TARGETS:
        if not target.exists():
            print(f"  SKIP {target.relative_to(REPO)}: not found")
            continue
        original = target.read_text(encoding="utf-8")
        updated, edits = rewrite(original)
        if edits:
            target.write_text(updated, encoding="utf-8")
            total_files += 1
            total_edits += edits
            print(f"  {target.relative_to(REPO)}: {edits} rewrites")
        else:
            print(f"  {target.relative_to(REPO)}: no changes")
    print(f"Done. {total_edits} total rewrites across {total_files} file(s).")


if __name__ == "__main__":
    main()
