"""One-shot migration: split backend/src/sources/*.py into category subfolders.

Moves each source file into src/sources/{category}/ based on the `category`
attribute already declared in the class. Updates src/main.py and
tests/test_sources.py import paths accordingly.

Run once, then delete this file.
"""
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCES_DIR = REPO / "backend" / "src" / "sources"

# Category → list of module names (filename stems without .py)
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


def main() -> None:
    # Flatten: stem → category
    name_to_cat = {name: cat for cat, names in CATEGORIES.items() for name in names}
    total = len(name_to_cat)
    print(f"Splitting {total} sources into {len(CATEGORIES)} categories...")

    # 1. Create category subdirs with __init__.py
    for cat in CATEGORIES:
        subdir = SOURCES_DIR / cat
        subdir.mkdir(exist_ok=True)
        init = subdir / "__init__.py"
        if not init.exists():
            init.write_text("", encoding="utf-8")

    # 2. git mv each source file into its category subfolder
    moved = 0
    for name, cat in name_to_cat.items():
        src = SOURCES_DIR / f"{name}.py"
        dst = SOURCES_DIR / cat / f"{name}.py"
        if not src.exists():
            print(f"  SKIP {name}: source file not found at {src}")
            continue
        result = subprocess.run(
            ["git", "mv", str(src), str(dst)],
            cwd=str(REPO), capture_output=True, text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git mv failed for {name}: {result.stderr}")
        moved += 1
    print(f"  Moved {moved}/{total} source files")

    # 3. Rewrite imports in main.py and test_sources.py
    targets = [
        REPO / "backend" / "src" / "main.py",
        REPO / "backend" / "tests" / "test_sources.py",
    ]
    for target in targets:
        if not target.exists():
            print(f"  SKIP {target}: not found")
            continue
        content = target.read_text(encoding="utf-8")
        rewrites = 0
        for name, cat in name_to_cat.items():
            old = f"from src.sources.{name} import"
            new = f"from src.sources.{cat}.{name} import"
            if old in content:
                content = content.replace(old, new)
                rewrites += 1
        target.write_text(content, encoding="utf-8")
        print(f"  Rewrote {rewrites} imports in {target.relative_to(REPO)}")

    print("Done.")


if __name__ == "__main__":
    main()
