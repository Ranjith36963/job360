#!/usr/bin/env python3
"""Loop 3 — doc-sync drift check (report-only, read-only by design).

Extracts hard facts from the CODE and compares them against the claims the
DOCS make. Prints a GitHub-issue-ready markdown report.

Exit 0 = docs match code. Exit 1 = drift found. Exit 2 = checker itself broke.

Fails LOUD, never silently green (reviewer findings 2026-07-15):
- a fact whose claim can no longer be found in ANY doc is drift (reworded or
  deleted claims must not slip past the regexes);
- a missing doc file is drift;
- forbidden stale phrases (prose lies numbers can't catch) are drift;
- LIVING docs must carry a fresh `<!-- last-verified: YYYY-MM-DD ... -->`
  stamp (written by /sync) — missing or older than STALE_DAYS is drift.

Deliberately NOT checked: the collected-test count (needs Postgres, and
parametrization makes any cheap def-count tolerance flaky — a false-alarming
check trains people to ignore the loop).

Read-only on purpose: this loop REPORTS drift, it never edits docs
(see docs/harness/maintenance/loop1_safe_reenable.md — "verify=read is safe").
Run: python scripts/doc_sync_check.py   (from the repo root)
"""

from __future__ import annotations

import ast
import datetime as _dt
import os
import re
import sys
from pathlib import Path

ROOT = Path(os.environ["JOB360_ROOT"]) if os.environ.get("JOB360_ROOT") else Path(__file__).resolve().parents[1]

STALE_DAYS = 45

# LIVING docs (DOC-MAINTENANCE.md §1): must match code AND carry a fresh stamp.
LIVING_DOCS = [
    "CLAUDE.md",
    "README.md",
    "ARCHITECTURE.md",
    "STATUS.md",
    "backend/CLAUDE.md",
    # Added 2026-08-11. frontend/CLAUDE.md was NOT here, so the one file carrying
    # a stale "The 28 hard rules" was the one file this checker could not see --
    # a guard blind to the exact bug it was written for.
    "frontend/CLAUDE.md",
    "frontend/README.md",
    # Added 2026-08-24 by the coverage half of doc_sync_mutation_test.py, which
    # asks the question this list could never ask itself: which docs state a
    # guarded fact while sitting OUTSIDE it? All three were lying.
    #   CONTRIBUTING.md         "the 26 hard rules"        (31)
    #   backend/README.md       "list all 47 sources"      (41)
    #   .../03-job-providers.md "SOURCE_REGISTRY (47"      (41), in ten places
    # The last one is the worst: root CLAUDE.md calls docs/product/pillars/ the
    # AUTHORITATIVE code-verified architecture reference, and it carried the
    # pre-2026-08-17 source counts that started this whole audit.
    "CONTRIBUTING.md",
    "backend/README.md",
    "docs/product/pillars/03-job-providers.md",
    # Added 2026-08-24 (second batch). Root CLAUDE.md calls docs/product/pillars/
    # the AUTHORITATIVE code-verified reference, yet only 03 was watched -- and
    # only since this morning. The glossary is the densest concentration of
    # countable facts in the repo (registry size, instance count, RATE_LIMITS,
    # JobEnrichment shape, Job field count) and not one of them had ever been
    # checked: it claimed 18 fields / 8 enums against 16 / 7, and ~256 ATS slugs
    # against 302.
    "docs/product/pillars/01-user-pillar.md",
    "docs/product/pillars/02-search-and-match-engine.md",
    "docs/product/pillars/glossary.md",
]

# Prose lies that numbers can't catch. Each = (forbidden phrase, why).
FORBIDDEN_PHRASES = [
    ("async SQLite", "the DB is Postgres via psycopg3 since 2026-07-02 (pg.py shim)"),
]

# Header spec (DOC-MAINTENANCE.md): one HTML comment near the top of a doc,
# e.g.  <!-- doc: LIVING | last-verified: 2026-07-15 by /sync -->
#       <!-- doc: PLAN | status: ACTIVE -->
_STAMP_RE = re.compile(r"last-verified:\s*(\d{4}-\d{2}-\d{2})")
_TYPE_RE = re.compile(r"<!--\s*doc:\s*(LIVING|PLAN|LOG|REFERENCE)\b")

# Relative markdown links to check for dead targets: [text](path.md) style.
_LINK_RE = re.compile(r"\]\(([^)#\s]+?\.md)(?:#[^)]*)?\)")

# Dirs holding docs whose links we sweep (node_modules etc. excluded).
LINK_SWEEP_DIRS = [".", "docs", "backend", "frontend"]


def registry_counts() -> tuple[int, int]:
    """(registry entries, unique source classes) from SOURCE_REGISTRY in src/main.py.

    Strict on purpose: only a module-top-level plain dict literal of
    Name/Attribute values counts. Anything cleverer must crash the checker
    (exit 2) rather than silently under-count.
    """
    tree = ast.parse((ROOT / "backend/src/main.py").read_text(encoding="utf-8"))
    for node in tree.body:  # top level only — never a dict inside a function
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            if getattr(t, "id", None) == "SOURCE_REGISTRY":
                if not isinstance(node.value, ast.Dict):
                    raise RuntimeError("SOURCE_REGISTRY is not a plain dict literal")
                classes = set()
                for k, v in zip(node.value.keys, node.value.values):
                    if k is None or not isinstance(k, ast.Constant):
                        raise RuntimeError("SOURCE_REGISTRY has a non-literal / **spread key")
                    name = getattr(v, "id", None) or getattr(v, "attr", None)
                    if not name:
                        raise RuntimeError("SOURCE_REGISTRY value is not a class Name/Attribute")
                    classes.add(name)
                return len(node.value.keys), len(classes)
    raise RuntimeError("SOURCE_REGISTRY dict literal not found at top level of backend/src/main.py")


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


def rate_limit_count() -> int:
    """Entries in RATE_LIMITS. Same AST-strict style as registry_counts().

    Promoted from the nightly routine 2026-08-24 (second batch). Docs claimed
    46 and 47 in three places while the dict held 41. It must equal the
    registry key count -- every source needs a limit -- so a mismatch between
    THIS and registry is itself a bug worth seeing.
    """
    tree = ast.parse((ROOT / "backend/src/core/settings.py").read_text(encoding="utf-8"))
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            if getattr(t, "id", None) == "RATE_LIMITS":
                if not isinstance(node.value, ast.Dict):
                    raise RuntimeError("RATE_LIMITS is not a plain dict literal")
                return len(node.value.keys)
    raise RuntimeError("RATE_LIMITS dict literal not found in backend/src/core/settings.py")


def source_subclass_count() -> int:
    """Files declaring `class X(BaseJobSource)`.

    Should equal unique source classes. The docs said "all 49 subclasses" in
    rule #2's neighbourhood while 40 existed -- an over-count that makes the
    five-surface rule read as bigger than it is.
    """
    n = 0
    for p in (ROOT / "backend/src/sources").rglob("*.py"):
        n += len(re.findall(r"^class \w+\(BaseJobSource\)", p.read_text(encoding="utf-8"), re.M))
    return n


def workflow_count() -> int:
    """Number of GitHub Actions workflow files.

    Promoted from the nightly doc-truth routine 2026-08-24, which found the
    docs claiming 26 while three more had landed. A countable fact an LLM
    re-discovers every night is a fact this script should hold for free.
    """
    return len(list((ROOT / ".github/workflows").glob("*.yml")))


def test_file_count() -> int:
    """Number of backend test_*.py files.

    NOT the collected-test count. Collection imports conftest, which wants a
    live Postgres, and this checker runs in a blocking CI step that must stay
    fast and offline. The collected number stays the routine's job; the file
    count is filesystem-only and cannot lie.
    """
    return len(list((ROOT / "backend/tests").glob("test_*.py")))


def frontend_versions() -> tuple[str, str]:
    """(next, react) exact versions from frontend/package.json.

    Also promoted 2026-08-24: docs said Next.js 16.2.2 / React 19.2.4 while
    package.json pinned 16.3.0 / 19.2.8. Only FULL x.y.z claims are matched,
    so prose like "Next.js 16" stays legal.
    """
    import json

    pkg = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    deps = pkg.get("dependencies", {})
    clean = lambda v: str(v).lstrip("^~>=< ")  # noqa: E731
    return clean(deps.get("next", "")), clean(deps.get("react", ""))


def scorer_version() -> int:
    """SCORER_VERSION from backend/src/services/skill_matcher.py.

    Added 2026-08-23. CLAUDE.md rule #19 stated 4 while the code had already
    moved to 7 — a load-bearing constant that rotted through three bumps
    because no pattern here guarded it, and the daily tripwire stayed green
    the whole time. Same lesson as the 2026-08-03 batch: this checker only
    ever guards what someone remembered to teach it.
    """
    tree = ast.parse(
        (ROOT / "backend/src/services/skill_matcher.py").read_text(encoding="utf-8")
    )
    for node in tree.body:  # top level only
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            if getattr(t, "id", None) == "SCORER_VERSION":
                # CodeRabbit: `isinstance(True, int)` is True in Python, so
                # `SCORER_VERSION = True` slipped through a check whose own
                # error message promises "a plain int literal" — and then
                # compared equal to a documented 1. `type(...) is int`
                # rejects bool without rejecting anything else.
                if not isinstance(node.value, ast.Constant) or (
                    type(node.value.value) is not int
                ):
                    raise RuntimeError("SCORER_VERSION is not a plain int literal")
                return node.value.value
    raise RuntimeError(
        "SCORER_VERSION not found at top level of backend/src/services/skill_matcher.py"
    )


def dead_links() -> list[tuple[str, str]]:
    """(doc, broken target) for every relative .md link pointing at a missing file."""
    files: list[Path] = []
    for d in LINK_SWEEP_DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        files.extend(base.rglob("*.md") if d == "docs" else base.glob("*.md"))
    broken = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in _LINK_RE.finditer(text):
            target = m.group(1)
            if "://" in target or target.startswith("mailto:"):
                continue
            if not (f.parent / target).exists():
                broken.append((str(f.relative_to(ROOT)).replace("\\", "/"), target))
    return broken


def hard_rule_count() -> int:
    """How many numbered Hard Rules CLAUDE.md actually defines.

    backend/CLAUDE.md said "the 26 hard rules" while the root defined 28 — a
    pointer doc quietly disagreeing with the thing it points at. Cheap to check,
    and exactly the class of number that rots every time a rule is added.
    """
    text = (ROOT / "CLAUDE.md").read_text(encoding="utf-8", errors="replace")
    # Count distinct rule NUMBERS, not list lines. Two rules that share one entry
    # ("11 + 16. **Never import ...") are still two rules to the agent reading them,
    # and the agent is this number's consumer. Counting lines made the checker
    # report 29 while the document visibly numbered up to 31.
    numbers: set[int] = set()
    for m in re.finditer(r"^(\d+(?:\s*\+\s*\d+)*)\. \*\*", text, re.M):
        numbers.update(int(n) for n in re.findall(r"\d+", m.group(1)))
    return len(numbers)


def route_module_count() -> int:
    """Route modules under backend/src/api/routes/, excluding __init__."""
    d = ROOT / "backend" / "src" / "api" / "routes"
    if not d.is_dir():
        return -1
    return len([p for p in d.glob("*.py") if p.name != "__init__.py"])


def endpoint_count() -> int:
    """`@router.<verb>` decorators across the route modules."""
    d = ROOT / "backend" / "src" / "api" / "routes"
    if not d.is_dir():
        return -1
    n = 0
    for p in d.glob("*.py"):
        n += len(re.findall(r"@router\.(?:get|post|put|patch|delete)\b",
                            p.read_text(encoding="utf-8", errors="replace")))
    return n


def dead_path_claims() -> list[tuple[str, str]]:
    """Paths a LIVING doc names that DO NOT EXIST on disk.

    THE MOST DANGEROUS DRIFT OF ALL, and the one nothing checked. STATUS.md
    pointed at `backend/src/profile/` and `backend/src/filters/` — both moved to
    `src/services/` in the clean-architecture restructure, and both gone for
    months. An agent told to "fix the CV parser per STATUS.md" would create
    files at paths that no longer exist, in a directory nothing imports.

    Deliberately narrow: only `backend/src/...` and `frontend/src/...` prefixes,
    only inside backticks, so prose and historical references never false-match.
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    # `docs/` added 2026-08-24. The pillar docs MOVED to docs/product/pillars/
    # and four LIVING docs went on naming `docs/pillars/` as fact for weeks --
    # including the line calling it the AUTHORITATIVE architecture reference.
    # dead_links() could not see it (that only matches `](...md)` targets) and
    # this function's prefix list stopped at backend/frontend. A doc pointing an
    # agent at a directory that does not exist is exactly what this was written
    # for; it just could not look where the damage was.
    pat = re.compile(r"`((?:backend|frontend)/src/[A-Za-z0-9_./-]+|docs/[A-Za-z0-9_./-]+)`")
    # README.md is a HOW-TO. It is full of "create `backend/src/sources/
    # yoursource.py`" examples - instructions, not claims that a file exists.
    # Flagging those is a permanent false alarm, and a permanent alarm is how a
    # loop dies. In CLAUDE.md / STATUS.md / ARCHITECTURE.md a path IS a factual
    # claim, and a wrong one sends an agent to edit a file that is not there.
    # (A word-based "is this an instruction?" filter was tried first and is
    # strictly worse: it silences real claims that merely contain "add".)
    factual_docs = [d for d in LIVING_DOCS if not d.endswith("README.md")]
    for rel in factual_docs:
        path = ROOT / rel
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in pat.finditer(line):
                claimed = m.group(1).rstrip("/")
                # A trailing-slash claim is a directory; otherwise allow either.
                target = ROOT / claimed
                if target.exists():
                    continue
                key = (rel, claimed)
                if key in seen:
                    continue
                seen.add(key)
                out.append((f"{rel}:{i}", claimed))
    return out


def size_budget_drift() -> list[tuple[str, str, str, str, str]]:
    """CLAUDE.md against the size budget it declares in its own header.

    The budget is a HARD invariant, not a "fix it soon" signal: the number is
    deterministic, the fix is always the same (move detail out, leave a pointer),
    and the file is auto-loaded into every session, so an over-budget file taxes
    every future turn. It is therefore split out from the drift report -- drift is
    reported daily and repaired by the fixer loop (deliberately non-blocking), while
    this one gates the PR. Run it alone with: doc_sync_check.py --budget-only
    """
    claude_md = ROOT / "CLAUDE.md"
    if not claude_md.is_file():
        return []
    text = claude_md.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"SIZE BUDGET:\s*<=\s*([\d,]+)\s*words", text)
    if not m:
        return []
    budget = int(m.group(1).replace(",", ""))
    words = len(text.split())
    if words <= budget:
        return []
    return [
        ("CLAUDE.md", "-", "size-budget", f"{words} words",
         f"<= {budget} -- move the detail into the doc it belongs to and leave a "
         f"one-line pointer"),
    ]


def budget_only() -> int:
    """Blocking PR gate. Prints and exits non-zero ONLY on a budget breach."""
    rows = size_budget_drift()
    if not rows:
        print("CLAUDE.md is within its declared size budget. OK")
        return 0
    _, _, _, said, want = rows[0]
    print(f"::error file=CLAUDE.md::CLAUDE.md is {said}, budget {want}")
    print(f"CLAUDE.md size budget EXCEEDED: {said} (budget {want}).")
    return 1


def build_checks() -> tuple[list[tuple[str, int, str]], list[tuple[str, str, str]]]:
    """Return (numeric checks, text checks).

    Extracted from main() 2026-08-24 so scripts/doc_sync_mutation_test.py can
    read the real patterns instead of keeping its own copy. A drill with a
    duplicated pattern list stops testing this checker the moment the two
    drift apart — which is the exact failure mode this whole file exists for.
    """
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
        # Added 2026-08-24 by scripts/doc_sync_mutation_test.py on its first
        # full run. CLAUDE.md:44 phrases it "`SOURCE_REGISTRY` has 41 entries",
        # which matched none of the five patterns above. Other docs DID claim
        # it in a matching form, so the "nobody claims this fact" alarm stayed
        # quiet and the number in the most-read doc in the repo was guarded by
        # nothing at all.
        ("registry", registry, r"SOURCE_REGISTRY`?\s+has\s+(\d+)\s+entries"),
        ("unique-classes", unique_classes, r"(\d+) unique source classes"),
        ("migration-head", mig_head, r"0000 → (\d{4})"),
        # Added 2026-08-03. The checker tracked THREE facts, so every other
        # number in the docs rotted silently — an audit found the rule count,
        # the route/endpoint counts and three mutually-contradictory test counts
        # all wrong at once. A drift checker that only guards what it already
        # guarded is how a doc estate dies while its tripwire stays green.
        ("hard-rules", hard_rule_count(), r"the (\d+) hard rules"),
        ("route-modules", route_module_count(), r"(\d+) route modules"),
        ("endpoints", endpoint_count(), r"\((\d+) endpoints"),
        # Added 2026-08-23. Docs said SCORER_VERSION = 4, code said 7. Every
        # score-affecting change is supposed to bump it, so a stale claim here
        # misleads on exactly the constant that decides whether a user's feed
        # gets re-scored. `\*{0,2}` because the docs bold it.
        ("scorer-version", scorer_version(), r"SCORER_VERSION`?\s*=\s*\*{0,2}(\d+)"),
        # Promoted from the nightly doc-truth routine 2026-08-24. Each of these
        # was drift the LLM found by reading; they are countable, so they belong
        # here where they cost nothing and are caught on every push.
        ("workflows", workflow_count(), r"(\d+) workflows in"),
        ("test-files", test_file_count(), r"across (\d+) `?test_\*\.py`? files"),
        # Second promotion batch, 2026-08-24, all found by the nightly routine
        # in the pillar docs and glossary -- the densest concentration of
        # countable facts in the repo, and until today none of them watched.
        ("rate-limits", rate_limit_count(), r"`?RATE_LIMITS`?[^.\n]{0,40}?\((\d+) entries"),
        ("rate-limits", rate_limit_count(), r"`?RATE_LIMITS`? dict in `?settings\.py`? \((\d+) entries"),
        ("subclasses", source_subclass_count(), r"checking all (\d+) subclasses"),
        ("registry", registry, r"from a (\d+)-key `?SOURCE_REGISTRY"),
        ("registry", registry, r"The (\d+)-key dict in `?main\.py`?"),
        ("unique-classes", unique_classes, r"[Bb]uilds (\d+) instances"),
        # Deliberately NOT matching `SOURCE_INSTANCE_COUNT = N`. That is a
        # verbatim code quote, and docs legitimately cite it -- including dated
        # decision notes that are correct at the time of writing. Guarding it
        # fires on every honest citation, and a permanent false alarm is how a
        # loop dies. The prose form above covers the claim that matters.
    ]

    # String-valued facts. Kept separate because the numeric loop below does
    # int(m.group(1)); a version like "16.3.0" is not an int and must be
    # compared as text.
    next_ver, react_ver = frontend_versions()
    text_checks = [
        # Only FULL x.y.z claims match, so prose like "Next.js 16" stays legal.
        ("nextjs-version", next_ver, r"Next\.js (\d+\.\d+\.\d+)"),
        ("react-version", react_ver, r"React (\d+\.\d+\.\d+)"),
    ]
    return checks, text_checks


def main() -> int:
    checks, text_checks = build_checks()
    registry, unique_classes = registry_counts()
    mig_head = migration_head()

    drift: list[tuple[str, str, str, str, str]] = []  # file, line, fact, doc-says, code-says
    matches_per_fact: dict[str, int] = {}
    today = _dt.date.today()

    for rel in LIVING_DOCS:
        path = ROOT / rel
        if not path.exists():
            # A vanished LIVING doc is exactly the stale-merge disaster case.
            drift.append((rel, "-", "missing-doc", "file deleted/renamed", "must exist"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()

        for fact, actual, pattern in checks:
            for i, line in enumerate(lines, start=1):
                # Case-INSENSITIVE. frontend/CLAUDE.md:5 begins the sentence, so
                # it reads "The 28 hard rules" while the pattern said "the (\d+)".
                # One capital letter hid a stale number from its own tripwire.
                for m in re.finditer(pattern, line, re.IGNORECASE):
                    matches_per_fact[fact] = matches_per_fact.get(fact, 0) + 1
                    claimed = int(m.group(1))
                    if claimed != actual:
                        drift.append((rel, str(i), fact, str(claimed), str(actual)))

        for fact, actual_text, pattern in text_checks:
            for i, line in enumerate(lines, start=1):
                for m in re.finditer(pattern, line, re.IGNORECASE):
                    matches_per_fact[fact] = matches_per_fact.get(fact, 0) + 1
                    if m.group(1) != actual_text:
                        drift.append((rel, str(i), fact, m.group(1), actual_text))

        for phrase, why in FORBIDDEN_PHRASES:
            for i, line in enumerate(lines, start=1):
                if phrase.lower() in line.lower():
                    drift.append((rel, str(i), "stale-phrase", f'"{phrase}"', why))

        stamp = _STAMP_RE.search(text)
        if not stamp:
            drift.append((rel, "-", "freshness", "no last-verified stamp", "run /sync (stamps + verifies)"))
        else:
            age = (today - _dt.date.fromisoformat(stamp.group(1))).days
            if age > STALE_DAYS:
                drift.append((rel, "-", "freshness", f"verified {age} days ago", f"max {STALE_DAYS} — run /sync"))

        type_tag = _TYPE_RE.search(text)
        if not type_tag:
            drift.append((rel, "-", "doc-type", "no doc-type header", "needs <!-- doc: LIVING ... -->"))
        elif type_tag.group(1) != "LIVING":
            drift.append((rel, "-", "doc-type", f"tagged {type_tag.group(1)}", "this file is a LIVING doc"))

    # Dead relative links anywhere in the doc tree (archive moves, renames).
    for doc, target in dead_links():
        drift.append((doc, "-", "dead-link", f"→ {target}", "target file does not exist"))

    # A fact nobody claims anymore = the claim was reworded/deleted and this
    # checker just went blind to it. That is drift, not success.
    for fact in {c[0] for c in checks} | {c[0] for c in text_checks}:
        if matches_per_fact.get(fact, 0) == 0:
            drift.append(("(all docs)", "-", fact, "claim not found in any doc",
                          "reworded/removed — update checker patterns or restore the claim"))
    # Paths a doc names that do not exist. Checked ONCE, after every doc has
    # been read, because it is a property of the estate rather than of a line.
    for where, claimed in dead_path_claims():
        f, ln = where.rsplit(":", 1)
        drift.append((f, ln, "dead-path", claimed, "this path does not exist"))


    drift.extend(size_budget_drift())

    print("# Doc-sync drift report (Loop 3)\n")
    print(f"Code facts: SOURCE_REGISTRY entries = **{registry}**, "
          f"unique source classes = **{unique_classes}**, "
          f"migration head = **{mig_head:04d}**\n")

    if not drift:
        print("No drift — every doc claim matches the code, all stamps fresh. OK")
        return 0

    print("| File | Line | Fact | Doc says | Code says |")
    print("|------|------|------|----------|-----------|")
    for rel, ln, fact, said, actual in drift:
        print(f"| {rel} | {ln} | {fact} | {said} | {actual} |")
    print(f"\n**{len(drift)} drifted claim(s).** Fix the docs (or run `/sync` in a "
          "Claude session) — this check never edits files itself.")
    return 1


if __name__ == "__main__":
    if "--budget-only" in sys.argv[1:]:
        sys.exit(budget_only())
    # Windows consoles default to cp1252; arrows in doc text or this output
    # would raise UnicodeEncodeError -> bogus exit 2. Force utf-8 stdout.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — very old runtimes; keep going
        pass
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 — a broken checker must be loud, not a silent pass
        print(f"doc_sync_check crashed: {exc}", file=sys.stderr)
        sys.exit(2)
