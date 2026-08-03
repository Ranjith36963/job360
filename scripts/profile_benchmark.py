#!/usr/bin/env python3
"""Run every real profile in User_info/ through extraction and grade the result.

WHY. Extraction is the first stage of the product and the one nothing tested.
The live audit found both real users carrying 130-152 "skills" — over-extraction
that quietly makes matching worse, because when everything is a skill nothing
stands out. But two users is not a sample, and neither is a synthetic fixture:
real CVs are messy in ways nobody invents.

`User_info/` holds 7 real people (CV, LinkedIn, GitHub). That is a benchmark
set. This runs the actual production extractor over all of them and reports,
per person, whether the output is usable.

PRIVACY — THE LOAD-BEARING RULE. `User_info/` is gitignored (.gitignore:53) and
holds real people's CVs. This script therefore:
  * never copies, prints or writes any CV text, name, employer or email
  * reports COUNTS and SHAPES only ("42 skills", "3 sections found")
  * masks every filename to an initial ("Ranjith_CV.pdf" -> "R***")
  * writes only to a gitignored path
A benchmark that leaks its own corpus is worse than no benchmark, and this repo
is public.

COST. Deterministic pass only by default — no LLM, no spend, no keys needed.
`--llm` additionally runs the paid pass, which is the honest comparison but
costs money per CV; it is opt-in for that reason.

Usage:
    python scripts/profile_benchmark.py                    # free, deterministic
    python scripts/profile_benchmark.py --root ../..       # if run from backend/
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# What "good" looks like.
#
# CORRECTION, recorded honestly: the first version of this file called anything
# over 60 skills "over-extracted". That number was invented, not measured. When
# I actually READ the output, one 73-skill profile was entirely legitimate
# (Python, Pandas, OCR, Tesseract, AI Ethics, Linux...) — a dense AI CV really
# does have that many. A raw count cannot tell a rich profile from a noisy one.
#
# So the ceiling is gone and the signal is NOISE RATIO instead: what fraction of
# extracted "skills" are single lowercase common words? Real skill names are
# proper nouns, acronyms, or capitalised terms ("PySpark", "RAG", "Deep
# Learning"). A list full of bare words like "validity", "uniqueness",
# "timeliness", "metrics" is a sentence that was comma-split — which is exactly
# what the genuinely noisy profile in the corpus contained.
MIN_SKILLS = 5
MIN_CHARS = 400
# Above this share of bare-lowercase tokens, the list is prose, not skills.
MAX_NOISE_RATIO = 0.35


def mask(name: str) -> str:
    """Ranjith_CV.pdf -> R*** . Never print a real name."""
    stem = Path(name).stem.split("_")[0]
    return (stem[0] + "***") if stem else "?***"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".", help="repo root holding User_info/")
    # The corpus and the code under test are NOT always in the same checkout:
    # User_info/ lives once in the main working copy, while the extractor being
    # benchmarked may be in a worktree. Conflating them silently benchmarks the
    # WRONG code and reports "no change" after a real fix — which is exactly
    # what happened the first time this ran.
    ap.add_argument("--code", default=None,
                    help="checkout whose backend/ is under test (default: --root)")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    code_root = Path(args.code).resolve() if args.code else root
    ui = root / "User_info"
    if not ui.is_dir():
        print(f"# Profile benchmark\n\n`User_info/` not found under `{root}`.")
        print("Nothing to benchmark — this is an honest empty result, not a pass.")
        return 0

    sys.path.insert(0, str(code_root / "backend"))
    try:
        from src.services.profile.cv_parser import deterministic_cv_fields, extract_text
    except ImportError as exc:
        print(f"::error::cannot import the extractor: {exc}")
        return 2

    cvs = sorted((ui / "CV").glob("*.pdf")) if (ui / "CV").is_dir() else []
    lis = {p.stem.split("_")[0].lower() for p in (ui / "Linkedin_pdf").glob("*.pdf")} \
        if (ui / "Linkedin_pdf").is_dir() else set()
    ghs = {p.stem.split("_")[0].lower() for p in (ui / "github_urls").glob("*.txt")} \
        if (ui / "github_urls").is_dir() else set()

    if not cvs:
        print("# Profile benchmark\n\nNo CVs found in `User_info/CV/`.")
        return 0

    print("# Profile benchmark — real CVs through the real extractor\n")
    print(f"Corpus: **{len(cvs)} people**. Deterministic pass only (no LLM, no cost).\n")
    print("> Names are masked and no CV content is printed. `User_info/` is "
          "gitignored and stays that way.\n")
    print("**What this measures, precisely.** Production runs TWO passes: this free\n"
          "deterministic one, then a paid LLM pass that enhances it. A low count here\n"
          "does NOT mean the user ends up with a bad profile — it means their SAFETY\n"
          "NET is empty. If the LLM key is missing, a provider is down, or the rate\n"
          "cap trips, that person gets nothing at all. This measures the floor.\n")

    rows = []
    for pdf in cvs:
        who = mask(pdf.name)
        key = pdf.stem.split("_")[0].lower()
        try:
            text = extract_text(str(pdf))
        except Exception as exc:  # noqa: BLE001
            rows.append((who, 0, 0, "READ FAILED", f"{type(exc).__name__}", key))
            continue

        chars = len(text or "")
        try:
            det = deterministic_cv_fields(text or "")
        except Exception as exc:  # noqa: BLE001
            rows.append((who, chars, 0, "EXTRACT FAILED", f"{type(exc).__name__}", key))
            continue

        skills = len(det.get("skills") or [])
        summary = len(det.get("summary") or "")

        # Noise = single bare lowercase words. "asyncio" and "pandas" are real
        # and lowercase, so one such token proves nothing; a HIGH SHARE of them
        # is the signal that a sentence got comma-split into fake skills.
        items = det.get("skills") or []
        noise = [x for x in items if len(x.split()) == 1 and x.islower() and len(x) > 2]
        ratio = (len(noise) / len(items)) if items else 0.0

        if chars < MIN_CHARS:
            verdict, why = "**BROKEN**", f"only {chars} chars read from the PDF"
        elif skills < MIN_SKILLS:
            verdict, why = "**no floor**", f"{chars} chars in, {skills} skills out"
        elif ratio > MAX_NOISE_RATIO:
            verdict, why = "noisy", (f"{skills} skills but {int(ratio*100)}% are bare "
                                     "lowercase words — a sentence was comma-split")
        else:
            verdict, why = "ok", f"{skills} skills, {int(ratio*100)}% noise, {summary}-char summary"
        rows.append((who, chars, skills, verdict, why, key))

    print("| person | CV chars | skills | LinkedIn | GitHub | verdict | note |")
    print("|---|---:|---:|:---:|:---:|---|---|")
    for who, chars, skills, verdict, why, key in rows:
        li = "yes" if key in lis else "—"
        gh = "yes" if key in ghs else "—"
        print(f"| {who} | {chars} | {skills} | {li} | {gh} | {verdict} | {why} |")
    print()

    ok = [r for r in rows if r[3] == "ok"]
    over = [r for r in rows if r[3] == "noisy"]
    broken = [r for r in rows if "no floor" in r[3] or "FAILED" in r[3]]
    counts = [r[2] for r in rows if r[2]]

    print("## What this says\n")
    print(f"- Usable extraction: **{len(ok)}/{len(rows)}**")
    print(f"- Noisy (prose split into fake skills): **{len(over)}/{len(rows)}**")
    print(f"- No deterministic floor (fully LLM-dependent): **{len(broken)}/{len(rows)}**")
    if counts:
        print(f"- Skills per CV: min {min(counts)}, median "
              f"{int(statistics.median(counts))}, max {max(counts)}")
    print(f"- Have LinkedIn too: **{len(lis)}/{len(rows)}** · "
          f"have GitHub: **{len(ghs)}/{len(rows)}**")
    print()

    if over:
        print("### Over-extraction is the interesting failure\n")
        print("It is not a crash. Nothing errors, no test fails, and the pipeline "
              "reports success. But when a CV yields 100+ 'skills', every job "
              "matches a little and none matches a lot — the ranking flattens and "
              "the product feels vague. This is the exact shape of bug that only "
              "a human noticing has caught so far.\n")

    if broken:
        print("### No safety net\n")
        print("These people are entirely dependent on the paid LLM pass. If the key "
              "is missing, a provider is down, or the rate cap trips, they get no "
              "profile at all — and therefore no jobs:\n")
        for who, chars, skills, verdict, why, _ in broken:
            print(f"- {who}: {why}")
        print()
        return 1

    if over:
        return 1
    print("Every CV in the corpus extracts to a usable profile.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
