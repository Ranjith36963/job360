#!/usr/bin/env python3
"""Deterministic rule checker for Job360 core rules.

Pure stdlib — no src/ imports. Exits 1 on any violation.

Checks:
  Rule 1: All keywords must be dynamic and personalized — nothing hard-coded.
          No hardcoded keyword lists in sources AND no static keyword imports.
  Rule 2: CV is mandatory (+ preferences, LinkedIn, GitHub are primary).
          No CV = no search. main.py must load_profile() and early-return.
  Rule 3: Single scoring path — only JobScorer(config).score().
          No rogue score_job() functions, no from_defaults() bypasses.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAIL = False


def fail(rule: str, msg: str) -> None:
    global FAIL
    FAIL = True
    print(f"  FAIL [{rule}]: {msg}")


def ok(rule: str) -> None:
    print(f"  OK   [{rule}]")


# ── Rule 1: All keywords dynamic and personalized — nothing hard-coded ──

def check_rule1():
    """No hardcoded keyword lists AND no static keyword imports in sources."""
    source_dir = ROOT / "src" / "sources"
    violations = []

    # Part A: No hardcoded keyword lists (KEYWORDS = [...], etc.)
    hardcoded_patterns = [
        re.compile(r'^\s*KEYWORDS\s*=\s*\[', re.MULTILINE),
        re.compile(r'^\s*DEFAULT_KEYWORDS\s*=\s*\[', re.MULTILINE),
        re.compile(r'^\s*SEARCH_TERMS\s*=\s*\[', re.MULTILINE),
    ]

    # Part B: No importing static keyword lists from keywords.py
    import_pattern = re.compile(
        r'from\s+src\.config\.keywords\s+import\s+.*(?:KEYWORDS|SEARCH_TERMS|JOB_TITLES)',
        re.IGNORECASE,
    )

    for py in sorted(source_dir.glob("*.py")):
        if py.name in ("__init__.py", "base.py"):
            continue
        text = py.read_text(encoding="utf-8", errors="replace")

        # Check hardcoded lists
        for pat in hardcoded_patterns:
            if pat.search(text):
                violations.append(f"{py.name}: hardcoded keyword list ({pat.pattern})")

        # Check static imports
        if import_pattern.search(text):
            violations.append(f"{py.name}: imports static keywords from keywords.py")

    if violations:
        for v in violations:
            fail("Rule 1", v)
    else:
        ok("Rule 1: All keywords dynamic and personalized")


# ── Rule 2: CV mandatory, preferences/LinkedIn/GitHub primary ──

def check_rule2():
    """main.py must load_profile() and early-return when no profile."""
    main_py = ROOT / "src" / "main.py"
    text = main_py.read_text(encoding="utf-8")
    has_load_profile = "load_profile()" in text
    has_early_return = bool(re.search(
        r'if\s+not\s+profile.*:.*\n(?:.*\n)*?\s*return\s*\{',
        text,
        re.MULTILINE,
    ))
    if not has_load_profile:
        fail("Rule 2", "main.py missing load_profile() call")
    elif not has_early_return:
        fail("Rule 2", "main.py missing early return when no profile")
    else:
        ok("Rule 2: CV mandatory, no profile = no search")


# ── Rule 3: Single scoring path ──

def check_rule3():
    """No rogue scoring functions. Only JobScorer(config).score()."""
    violations = []
    for py in sorted((ROOT / "src").rglob("*.py")):
        if py.name == "skill_matcher.py":
            continue  # scorer lives here, that's expected
        text = py.read_text(encoding="utf-8", errors="replace")
        # Module-level def score_job(
        if re.search(r'^def\s+score_job\s*\(', text, re.MULTILINE):
            violations.append(f"{py.relative_to(ROOT)}: module-level score_job()")
        # SearchConfig.from_defaults
        if "SearchConfig.from_defaults" in text or "from_defaults()" in text:
            violations.append(f"{py.relative_to(ROOT)}: uses from_defaults()")
    if violations:
        for v in violations:
            fail("Rule 3", v)
    else:
        ok("Rule 3: Single scoring path")


# ── Main ──

def main():
    print("validate_rules.py — checking 3 core rules")
    print("-" * 50)
    check_rule1()
    check_rule2()
    check_rule3()
    print("-" * 50)
    if FAIL:
        print("RESULT: FAIL — violations found")
        sys.exit(1)
    else:
        print("RESULT: PASS — all rules satisfied")
        sys.exit(0)


if __name__ == "__main__":
    main()
