#!/usr/bin/env python3
"""Loop 3 — doc-sync drift check (report-only, read-only by design).

Extracts hard facts from the CODE (source-registry size, unique source classes,
migration head) and compares them against every numeric claim the DOCS make.
Prints a GitHub-issue-ready markdown report.

Exit 0 = docs match code. Exit 1 = drift found. Exit 2 = checker itself broke.

Read-only on purpose: this loop REPORTS drift, it never edits docs
(see docs/maintenance/loop1_safe_reenable.md — "verify=read is safe").
Run: python scripts/doc_sync_check.py   (from the repo root)
"""

from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ["JOB360_ROOT"]) if os.environ.get("JOB360_ROOT") else Path(__file__).resolve().parents[1]

DOC_FILES = [
    "CLAUDE.md",
    "README.md",
    "ARCHITECTURE.md",
    "STATUS.md",
    "backend/CLAUDE.md",
]


def registry_counts() -> tuple[int, int]:
    """(registry entries, unique source classes) from SOURCE_REGISTRY in src/main.py."""
    tree = ast.parse((ROOT / "backend/src/main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            if getattr(t, "id", None) == "SOURCE_REGISTRY" and isinstance(node.value, ast.Dict):
                keys = [k for k in node.value.keys if isinstance(k, ast.Constant)]
                classes = {getattr(v, "id", None) or getattr(v, "attr", None) for v in node.value.values}
                return len(keys), len(classes)
    raise RuntimeError("SOURCE_REGISTRY dict literal not found in backend/src/main.py")


def migration_head() -> int:
    """Highest NNNN prefix among backend/migrations/*.sql filenames."""
    nums = []
    for p in (ROOT / "backend/migrations").glob("*.sql"):
        m = re.match(r"(\d{4})_", p.name)
        if m:
            nums.append(int(m.group(1)))
    if not nums:
        raise RuntimeError("no NNNN_*.sql migrations found")
    return max(nums)


def main() -> int:
    registry, unique_classes = registry_counts()
    mig_head = migration_head()

    # (fact-name, actual-value, regex-with-one-capture-group). Patterns are
    # deliberately SPECIFIC phrases so unrelated numbers never false-match.
    checks = [
        ("registry", registry, r"SOURCE_REGISTRY`?\s*\((\d+)"),
        ("registry", registry, r"aggregates jobs from (\d+) sources"),
        ("registry", registry, r"Current count: \*\*(\d+)\*\*"),
        ("registry", registry, r"[Ll]ist all (\d+) sources"),
        ("registry", registry, r"(\d+) SOURCE_REGISTRY entries"),
        ("unique-classes", unique_classes, r"(\d+) unique source classes"),
        ("migration-head", mig_head, r"0000 → (\d{4})"),
    ]

    drift: list[tuple[str, int, str, str, str]] = []  # file, line, fact, doc-says, code-says
    for rel in DOC_FILES:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        for fact, actual, pattern in checks:
            for i, line in enumerate(lines, start=1):
                for m in re.finditer(pattern, line):
                    claimed = int(m.group(1))
                    if claimed != actual:
                        drift.append((rel, i, fact, str(claimed), str(actual)))

    print("# Doc-sync drift report (Loop 3)\n")
    print(f"Code facts: SOURCE_REGISTRY entries = **{registry}**, "
          f"unique source classes = **{unique_classes}**, "
          f"migration head = **{mig_head:04d}**\n")

    if not drift:
        print("No drift — every doc claim matches the code. ✅")
        return 0

    print("| File | Line | Fact | Doc says | Code says |")
    print("|------|------|------|----------|-----------|")
    for rel, ln, fact, said, actual in drift:
        print(f"| {rel} | {ln} | {fact} | {said} | {actual} |")
    print(f"\n**{len(drift)} drifted claim(s).** Fix the docs (or run `/sync` in a "
          "Claude session) — this check never edits files itself.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — a broken checker must be loud, not a silent pass
        print(f"doc_sync_check crashed: {exc}", file=sys.stderr)
        sys.exit(2)
