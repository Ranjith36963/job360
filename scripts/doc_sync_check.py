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
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["JOB360_ROOT"]) if os.environ.get("JOB360_ROOT") else Path(__file__).resolve().parents[1]

STALE_DAYS = 45

# LIVING docs (DOC-MAINTENANCE.md §1): must match code AND carry a fresh stamp.
LIVING_DOCS = [
    "CLAUDE.md",
    # Added 2026-08-25 with the CLAUDE.md diet: the hard rules moved here so
    # they load on demand instead of before every session. They carried
    # guarded facts, so the checker has to follow them -- the checker
    # reported "claim not found in any doc" the moment they moved, which is
    # the guard-watches-nothing alarm working.
    ".claude/skills/hard-rules/SKILL.md",
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
    # guarded fact while sitting OUTSIDE it? Two were lying.
    #   CONTRIBUTING.md         "the 26 hard rules"        (31)
    #   backend/README.md       "list all 47 sources"      (41)
    # A third pillar doc was worse still, carrying a stale source-registry
    # count in ten places while root CLAUDE.md called docs/product/pillars/
    # the AUTHORITATIVE code-verified architecture reference. That whole doc
    # was archived 2026-09-05 with the code it described (slice 5, #483).
    "CONTRIBUTING.md",
    "backend/README.md",
    # 01-user-pillar.md, 02-search-and-match-engine.md, 03-job-providers.md,
    # glossary.md, runbook.md, CATALOG_STATE.md, SHELF_FILL_MEASURED.md and
    # UNIVERSAL_SHELF.md were all watched here until 2026-09-05 (slice 5,
    # #483), when they were archived to docs/_archive/sourcing-era/ with the
    # FROZEN header — they described the job-search-and-score product that no
    # longer exists. `pillars_fully_watched()` below asserts every remaining
    # *.md under docs/product/pillars/ is on this list, so the folder is now
    # just the one pointer doc.
    "docs/product/pillars/README.md",
]

# Prose lies that numbers can't catch. Each = (forbidden phrase, why).
FORBIDDEN_PHRASES = [
    ("async SQLite", "the DB is Postgres via psycopg3 since 2026-07-02 (pg.py shim)"),
    # Added 2026-08-24 by the nightly routine. 01-user-pillar.md described
    # profile storage as "SQLite" and walked straight past this list, because
    # the single entry above pins one exact two-word phrase.
    #
    # Deliberately NOT a bare "SQLite": pg.py is honestly documented as an
    # aiosqlite-SHAPED driver, and the migrations legitimately discuss SQLite
    # DDL limits. A bare match would fire on both forever, and a permanent
    # false alarm is how a loop dies.
    ("SQLite table", "the DB is Postgres via psycopg3 — pg.py is aiosqlite-SHAPED, not SQLite"),
    ("SQLite database", "the DB is Postgres via psycopg3 — pg.py is aiosqlite-SHAPED, not SQLite"),
    ("stored in SQLite", "the DB is Postgres via psycopg3 since 2026-07-02 (pg.py shim)"),
    # Added 2026-08-24. The four entries above pin PROSE. runbook.md carried
    # five `sqlite3 data/jobs.db` COMMANDS -- an operator following them gets an
    # error, not a wrong sentence. A stale command is worse than a stale claim
    # because it is meant to be executed.
    ("sqlite3 data/", "the DB is Postgres — use `railway run -s Postgres psql` or psycopg3"),
    ("sqlite3 backend/data/", "the DB is Postgres — use `railway run -s Postgres psql` or psycopg3"),
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


def _migration_pairs() -> dict[int, str]:
    """{NNNN: stem} for migrations the RUNNER will actually apply.

    Mirrors ``migrations/runner.py::_discover_pairs``, which includes a stem only
    when BOTH ``.up.sql`` and ``.down.sql`` exist. CodeRabbit, on PR #394: the
    two guards below used to glob the filesystem independently -- ``*.sql`` for
    the head, ``*.up.sql`` for the count -- so a lone ``0031_x.up.sql`` with no
    matching down-file would push both numbers to a schema state the runner
    silently ignores. A guard that validates a migration the app will never
    apply is measuring the directory, not the database.

    One inventory now feeds both, and the pairing rule is the runner's own.
    """
    d = ROOT / "backend/migrations"
    seen: dict[int, str] = {}
    for u in sorted(d.glob("*.up.sql")):
        stem = u.name[: -len(".up.sql")]
        m = re.match(r"(\d{4})_.+$", stem)
        if not m:
            raise RuntimeError(
                f"migration {u.name} does not match NNNN_<name>.up.sql — "
                "a file the runner cannot order is not a migration"
            )
        if not (d / f"{stem}.down.sql").exists():
            raise RuntimeError(
                f"migration {u.name} has no matching {stem}.down.sql — "
                "the runner skips unpaired migrations, so this one never applies"
            )
        num = int(m.group(1))
        if num in seen:
            raise RuntimeError(
                f"duplicate migration prefix {m.group(1)}: {seen[num]} and {stem}"
            )
        seen[num] = stem
    if not seen:
        raise RuntimeError("no paired NNNN_*.up.sql/.down.sql migrations found")
    head = max(seen)
    missing = sorted(set(range(head + 1)) - set(seen))
    if missing:
        raise RuntimeError(
            "migration sequence has gaps at "
            + ", ".join(f"{n:04d}" for n in missing)
            + f" (head is {head:04d}) — the schema cannot be rebuilt from 0000"
        )
    return seen


def migration_head() -> int:
    """Highest NNNN the runner will apply (paired migrations only)."""
    return max(_migration_pairs())


def landing_page_source_claims() -> list[tuple[int, int]]:
    """(line, claimed count) for any lingering source-count claim on user-facing copy.

    RETIRED as a code-vs-doc comparison 2026-09-05 (slice 5, #483): Job360 no
    longer sources, ranks or recommends a job (product rule 4), so there is no
    live registry to compare a claim against any more. The regression guard
    stays, in a stricter form -- no page may EVER advertise a job-source count
    again. The frontend has its own backstop
    (`frontend/src/app/__tests__/landing-sources-count.test.tsx`, added by the
    application-spine slice, R14); this is the doc-sync-side net.

    Was added 2026-08-24 because the doc was RIGHT and the code was LYING:
    01-user-pillar.md faithfully quoted the landing page as "47 sources", and
    the page really did say 47 -- to every visitor of job360.uk -- while the
    registry held 41. Doc-sync found it by looking at the seam between doc and
    code; the nightly routine never could, because it may only edit *.md.
    """
    # Not just the landing page. The first sweep found five copies there and
    # stopped; a wider grep then found the SAME stale number in the site
    # metadata (the description Google and every social card show) and in the
    # footer strapline that renders on every page. Reach was larger than the
    # page that was fixed first, so the guard watches all three.
    targets = [
        "frontend/src/app/page.tsx",
        "frontend/src/app/layout.tsx",
        "frontend/src/components/layout/Footer.tsx",
    ]
    out: list[tuple[int, int]] = []
    pat = re.compile(r"(\d+)\s+(?:[Jj]ob\s+)?[Ss]ources?\b|SOURCE_COUNT\s*=\s*(\d+)")
    for rel in targets:
        f = ROOT / rel
        if not f.exists():
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            # Case-INSENSITIVE. The first draft tested `"ource" not in line`,
            # which skipped `SOURCE_COUNT = 41` -- the constant every other
            # site reads from -- because that spelling is uppercase. Setting it
            # to 99 left the guard green. Exactly the failure this file already
            # records for the hard-rule count: one capital letter hiding a
            # stale number from its own tripwire.
            if "ource" not in line.lower():
                continue
            # Prose in a comment explaining the old bug legitimately says 47.
            if line.lstrip().startswith(("*", "//", "#")):
                continue
            for m in pat.finditer(line):
                claimed = m.group(1) or m.group(2)
                if claimed:
                    out.append((i, int(claimed)))
    return out


DOC_KINDS = ("LIVING", "PLAN", "LOG", "REFERENCE", "FROZEN")
_LIVING_STAMPED_CACHE: list[str] | None = None


LINE_CITATION_BASELINE = ROOT / "scripts" / "line_citation_baseline.txt"
SURFACE_CEILING = ROOT / "scripts" / "living_surface_ceiling.txt"


def surface_regression() -> list[str]:
    """The LIVING surface may not GROW. A ratchet on the haystack itself.

    Added 2026-08-25, after cycle 16 became the first run in sixteen to shrink
    the surface (8,331 -> 8,317). Until then "delete, do not reword" lived only
    in the routine's prompt, and a prompt is a request, not an enforcement --
    the next writer, human or model, adds a paragraph and the haystack grows
    back without anyone noticing.

    The ceiling is the honest number to guard because it is the one that cannot
    be gamed by the scout: findings-per-night falls if the scout gets lazy, but
    lines only fall if prose is actually removed.

    Deliberately NOT a hard cap on new prose. A doc explaining WHY -- the
    product rules, the reasoning code cannot hold -- is the content worth
    having, and this guard would block it. So the ceiling is raised by editing
    the file, in a commit that has to say what was added and why. The cost is
    one deliberate line; the benefit is that growth is never accidental.
    """
    docs, lines = living_surface()
    if not docs or not SURFACE_CEILING.exists():
        return []
    for row in SURFACE_CEILING.read_text(encoding="utf-8").splitlines():
        row = row.strip()
        if not row or row.startswith("#"):
            continue
        try:
            ceiling = int(row.replace(",", ""))
        except ValueError:
            continue
        if lines > ceiling:
            return [
                f"LIVING surface grew to {lines:,} lines, ceiling is {ceiling:,}. "
                f"Close findings by DELETING prose or pointing at a symbol, not by "
                f"rewording. If the new lines are genuinely WHY (not a restatement "
                f"of code), raise the ceiling in scripts/living_surface_ceiling.txt "
                f"and say what you added."
            ]
        return []
    return []


def living_surface() -> tuple[int, int]:
    """(docs, lines) of prose that MUST be true — the haystack, measured.

    Reported every run, never a failure. Added 2026-08-25 with the deletion
    contract, because fifteen consecutive nightly runs found real drift and
    none found zero -- and the reason was not sloppy writing. 46 LIVING docs
    hold ~8,278 lines restating what code already says, and every one of those
    lines is a claim that can become false.

    Detection cannot win against a haystack that never shrinks. The number
    that says whether this is being won is therefore NOT "findings per night"
    -- that can fall because the scout got lazy -- it is this one. A line
    deleted is a lie that can never be told again; a line reworded is a lie
    with a fresh expiry date. Watch the trend, not any single run.
    """
    # Swallows the blank line that follows the block too: a generated
    # region must cost ZERO surface lines, or converting prose into one
    # still nudges the number up and the incentive breaks at the margin.
    GENERATED = re.compile(
        r"<!--\s*generated:.*?-->.*?<!--\s*/generated\s*-->\n?", re.S)
    docs = lines = 0
    for rel in living_stamped_docs():
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        # Generated regions are EXCLUDED, and the incentive is the point.
        # This number is meant to count prose that CAN become false; a block
        # written from the code every build cannot. Counting it would punish
        # the one fix that ends drift instead of merely reporting it -- adding
        # a generated table would raise the surface and fail the ratchet, so
        # nobody would ever do it. Excluded, converting prose into a generated
        # block makes the number FALL, which is the behaviour worth rewarding.
        text = GENERATED.sub("", text)
        docs += 1
        lines += len(text.splitlines())
    return (docs, lines)


def line_number_citations() -> dict[str, int]:
    """{doc: count} of `file.py:NN` citations in LIVING docs. A RATCHET.

    A raw line number is the fastest-rotting reference form there is: it
    breaks on any unrelated edit ABOVE it, silently, and nothing about the doc
    looks wrong afterwards. A symbol name (`services/uk_gate.check_uk`)
    survives every edit that does not delete the thing itself.

    There are ~107 of these today, so a guard that simply reported them would
    fire 107 times on day one and be ignored inside a week -- and a guard that
    cries wolf is worse than no guard, because it teaches everyone to skip the
    report. So this is a RATCHET, the shape the mypy gate already uses: the
    current count is a ceiling that may only fall. New line numbers are
    refused; the existing ones drain as the docs shrink.
    """
    pat = re.compile(r"\b[A-Za-z_][A-Za-z0-9_/]*\.(?:py|ts|tsx|yml|sh):\d+")
    out: dict[str, int] = {}
    for rel in living_stamped_docs():
        path = ROOT / rel
        if not path.exists():
            continue
        n = len(pat.findall(path.read_text(encoding="utf-8", errors="replace")))
        if n:
            out[rel] = n
    return out


def line_citation_regressions() -> list[str]:
    """Docs that GAINED line-number citations since the baseline."""
    if not LINE_CITATION_BASELINE.exists():
        return []
    base: dict[str, int] = {}
    for row in LINE_CITATION_BASELINE.read_text(encoding="utf-8").splitlines():
        row = row.strip()
        if not row or row.startswith("#"):
            continue
        rel, _, num = row.rpartition(" ")
        try:
            base[rel.strip()] = int(num)
        except ValueError:
            continue

    out: list[str] = []
    for rel, n in sorted(line_number_citations().items()):
        was = base.get(rel, 0)
        if n > was:
            out.append(f"{rel}: {was} -> {n} (cite a SYMBOL name — a line "
                       f"number rots on any edit above it)")
    return out


def living_stamped_docs() -> list[str]:
    """Every doc that STAMPS itself LIVING — not just the hand-kept list.

    Proposed by cycle 15, which found the hole by measuring: `LIVING_DOCS` has
    15 entries while 46 docs carry `<!-- doc: LIVING -->`. The route guard's
    LOGIC was right; its INPUT was narrower than the thing it judged, so
    `.claude/skills/health/SKILL.md:31` could tell an agent to call a route
    (`GET /api/me`) that has never existed, and the guard never looked.

    That is the third appearance of one shape today -- a 400-char stamp window
    that missed stamps below long front matter, a doc-vs-doc check that could
    only see disagreement, and this. A guard is only as wide as what you feed
    it, and the width is the part nobody re-reads.

    LIVING_DOCS stays for the COUNTABLE guards: those need a curated list,
    because a number in an unexpected file is usually a quotation, not a claim.
    The structural guards take a doc at its word instead -- a file that says it
    is LIVING is asserting it is checkable.

    Asks git, not the filesystem. The first cut used `ROOT.rglob("*.md")` and
    filtered `node_modules` out afterwards -- but rglob WALKS a directory
    before the filter can reject it, and this check runs in the blocking CI
    step. It went from under a second to over two minutes. `git ls-files`
    never descends there, and it agrees with `unstamped_docs()` about what
    "tracked" means, so the two guards cannot disagree about the estate.

    Memoised: three guards call this now, and the answer cannot change inside
    a run.
    """
    global _LIVING_STAMPED_CACHE
    if _LIVING_STAMPED_CACHE is not None:
        return _LIVING_STAMPED_CACHE

    try:
        listing = subprocess.run(
            ["git", "ls-files", "*.md"], cwd=ROOT,
            capture_output=True, encoding="utf-8", check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        _LIVING_STAMPED_CACHE = list(LIVING_DOCS)   # fall back to the curated list
        return _LIVING_STAMPED_CACHE

    stamp = re.compile(r"<!--\s*doc:\s*LIVING")
    out: list[str] = []
    for rel in listing.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        path = ROOT / rel
        if not path.exists():
            continue
        if stamp.search(path.read_text(encoding="utf-8", errors="replace")[:4000]):
            out.append(rel)
    _LIVING_STAMPED_CACHE = out
    return out


def unstamped_docs() -> list[str]:
    """Tracked .md files that never say what they are.

    Added 2026-08-25, and it is the structural reason thirteen nightly cycles
    never returned zero. Only 20 of 148 docs carried a `<!-- doc: KIND -->`
    stamp, so the routine could not tell a FROZEN 2026-06 audit from a LIVING
    spec: both are just prose. A stale number in a dated record is CORRECT for
    its date, but every pass re-litigated it, and the finding count could never
    reach zero however many real fixes landed.

    Only LIVING can drift. PLAN/LOG/REFERENCE/FROZEN are records of a moment,
    and the routine must leave them alone. That makes the checkable surface
    finite -- which is what makes converging possible at all.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.md"], cwd=ROOT,
            capture_output=True, encoding="utf-8", check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    stamp = re.compile(r"<!--\s*doc:\s*([A-Z]+)")
    bad: list[str] = []
    for rel in out.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        path = ROOT / rel
        if not path.exists():
            continue
        # 4000, not 400: moving the stamp BELOW YAML front matter (which it
        # must be, or it breaks the file) pushed it past a 400-char window in
        # two files with long front matter, and they reported as unstamped.
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        m = stamp.search(head)
        if not m:
            bad.append(rel)
        elif m.group(1) not in DOC_KINDS:
            bad.append(f"{rel} (unknown kind {m.group(1)})")
        else:
            # A stamp ABOVE YAML front matter breaks the file it labels.
            # The first stamping pass put one on line 1 of nine SKILL.md and
            # agent files, so `---` no longer opened the document and the
            # front matter stopped parsing -- the skills and agents silently
            # stopped loading. CodeRabbit caught it on the PR. A guard that
            # labels a file must not break it, so placement is checked too.
            lines = head.split("\n")
            if lines and lines[0].startswith("<!-- doc:") and \
                    len(lines) > 1 and lines[1].strip() == "---":
                bad.append(f"{rel} (stamp sits ABOVE YAML front matter)")
    return bad


def missing_reader_banner() -> list[str]:
    """Non-LIVING docs whose reader cannot SEE that they are non-LIVING.

    Added 2026-08-25 after Fable 5 found the hole in the stamping pass:
    `<!-- doc: PLAN -->` is an HTML COMMENT. It tells this checker to skip the
    file. It tells the reader nothing -- it does not render, and an agent
    skimming for an answer walks straight past it.

    docs/product/PRD.md:65 was the proof: "Jobs scoring below 30/100 are
    silently dropped", false of the shipped system, stamped PLAN, therefore
    permanently invisible to the routine while still being read by anyone who
    opened the file. Quarantining a lie from the CHECKER is not retiring it.

    So every PLAN/LOG/REFERENCE/FROZEN doc carries a visible banner saying what
    it is, and this guard keeps that true for files added later.
    """
    kinds = {"PLAN", "LOG", "REFERENCE", "FROZEN"}
    stamp = re.compile(r"<!--\s*doc:\s*([A-Z]+)\s*-->")
    try:
        listing = subprocess.run(
            ["git", "ls-files", "*.md"], cwd=ROOT,
            capture_output=True, encoding="utf-8", check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    bad: list[str] = []
    for rel in listing.split():
        path = ROOT / rel
        if not path.exists():
            continue
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        m = stamp.search(head)
        if m and m.group(1) in kinds and "<!-- banner: auto -->" not in head:
            bad.append(f"{rel} ({m.group(1)})")
    return bad


def unreadable_env_vars() -> list[tuple[str, str, str]]:
    """(doc, line, NAME) for a row under an env-var heading that no code reads.

    Added 2026-08-25. ARCHITECTURE.md's "Environment Variables (.env)" table
    listed SEVEN names that are plain constants in `core/settings.py` with no
    `os.getenv` anywhere: MIN_MATCH_SCORE, MAX_RESULTS_PER_SOURCE,
    MAX_DAYS_OLD, MAX_RETRIES, RETRY_BACKOFF, REQUEST_TIMEOUT, USER_AGENT.

    This is the actively misleading kind rather than the merely stale kind.
    Someone sets MAX_DAYS_OLD=30 in .env, restarts, sees no change, and has no
    way to discover why -- the doc told them it was a knob and the code never
    looks. All seven were deleted; the fact lives in the code that holds them.

    ONE DIRECTION ONLY, deliberately. "Listed but never read" is decidable from
    a name search. The reverse -- "read but not documented" -- is not: 105
    names are read across the tree, most of them incidental (CI, tooling,
    third-party libraries), and demanding every one appear in a hand-written
    table would be noise, then an ignored report, then a dead guard.
    """
    heading = re.compile(r"^#{2,4}\s+Environment Variables", re.I)
    row = re.compile(r"^\|\s*`([A-Z][A-Z0-9_]{3,})`")
    out: list[tuple[str, str, str]] = []

    sources = ""
    for sub, globs in (("backend/src", ("*.py",)), ("frontend/src", ("*.ts", "*.tsx"))):
        base = ROOT / sub
        if not base.exists():
            continue
        for g in globs:
            for f in base.rglob(g):
                sources += f.read_text(encoding="utf-8", errors="replace")
    if not sources:
        return []

    for rel in living_stamped_docs():
        path = ROOT / rel
        if not path.exists():
            continue
        in_section = False
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.startswith("#"):
                in_section = bool(heading.match(line))
                continue
            if not in_section:
                continue
            m = row.match(line)
            if not m:
                continue
            name = m.group(1)
            if f'"{name}"' in sources or f"'{name}'" in sources \
                    or f"process.env.{name}" in sources:
                continue
            out.append((rel, str(i), name))
    return out


def doc_tree_dead_paths() -> list[tuple[str, str, str]]:
    """(doc, line, path) for a directory-tree entry naming a file that is gone.

    Added 2026-08-25. ARCHITECTURE.md carries a 69-entry tree of the repo, and
    a tree is the purest restatement there is -- it is a copy of `ls`. The
    honest first instinct was to delete it, and the second was to generate it,
    but neither is right: the tree is CURATED. It names the ~69 paths that
    matter out of thousands, and that selection is real editorial judgement a
    generator cannot reproduce and a deletion would throw away.

    What is checkable is every path it names. A tree rots one way -- a file
    moves or dies and the entry stays, sending a reader (or an agent) to a path
    that is not there. `LOCATIONS (25)` lived in this tree, wrong, for weeks.

    Depth comes from where the ``+--`` marker sits: four columns per level.
    Only entries under a directory the tree itself introduced are resolved, so
    a bare filename never gets checked against the repo root by accident.
    """
    entry = re.compile(r"^(?P<indent>[\s│]*)[├└]──\s+"
                       r"(?P<name>[A-Za-z0-9_.\-/]+)")
    out: list[tuple[str, str, str]] = []

    for rel in living_stamped_docs():
        path = ROOT / rel
        if not path.exists():
            continue
        stack: dict[int, str] = {}
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = entry.match(line)
            if not m:
                continue
            depth = len(m.group("indent")) // 4
            name = m.group("name").rstrip("/")
            parent = stack.get(depth - 1, "") if depth else ""
            full = f"{parent}/{name}" if parent else name
            stack[depth] = full
            for deeper in [d for d in stack if d > depth]:
                stack.pop(deeper, None)
            # A trailing-slash or extension-less entry is a directory; either
            # way the question is the same: is anything there?
            # Only trust the verdict when the PARENT resolves. A tree whose
            # root is a bare header line ("frontend/src/components/") rather
            # than a ├── entry gives its children no prefix, and they then
            # resolve against the repo root: the first cut reported 142 dead
            # paths, of which the overwhelming majority were `ui`, `jobs`,
            # `profile` -- my parsing being wrong, not the doc being wrong.
            # 142 false alarms would have buried the handful of real ones and
            # taught everyone to skip the report.
            # COVERAGE BOUND, stated plainly: only entries whose parent was
            # established INSIDE the tree are checked. A depth-0 entry in a
            # tree introduced by a bare header ("frontend/src/components/")
            # has no prefix at all, so `src` or `ui` would be resolved against
            # the repo root and reported dead when the doc is fine. This guard
            # therefore watches nested entries, not every line of every tree —
            # a smaller true claim beats a larger false one.
            if not parent or not (ROOT / parent).is_dir():
                continue
            if not (ROOT / full).exists():
                out.append((rel, str(i), full))
    return out


def control_chars_in_guards() -> list[tuple[str, str, str]]:
    """(file, line, byte) for any C0 control character in this repo's guards.

    Added 2026-08-25 after a real, expensive miss. Writing the route guard I
    produced `r"\\b(there is no...` where the `\\b` was a single 0x08 BACKSPACE
    byte, not backslash-plus-b -- a non-raw patch string collapsed it before it
    ever reached disk. The regex then required a literal backspace in the text,
    so it never matched and the negation branch was dead code.

    What makes this class worth a permanent guard: `sed`, `grep`, `cat` and
    even `inspect.getsource` ALL render 0x08 as `\\b`, identical to the word
    boundary intended. Every instrument I reached for agreed with the mistake.
    Only `od -c` disagreed. A guard whose regex silently cannot match is
    indistinguishable from a guard that is passing, which is the exact failure
    doc-sync exists to prevent -- so the bytes get checked, not the rendering.

    Tabs, newlines and carriage returns are legitimate and skipped.
    """
    allowed = {0x09, 0x0A, 0x0D}
    out: list[tuple[str, str, str]] = []
    for rel in ("scripts/doc_sync_check.py", "scripts/doc_sync_mutation_test.py",
                "scripts/encoding_guard.py", "scripts/drill_registry.py"):
        path = ROOT / rel
        if not path.exists():
            continue
        for i, raw in enumerate(path.read_bytes().split(b"\n"), 1):
            for b in raw:
                if b < 0x20 and b not in allowed:
                    out.append((rel, str(i), f"0x{b:02x}"))
                    break
    return out


def documented_routes_exist() -> list[tuple[str, str, str]]:
    """(doc, line, route) for `METHOD /api/...` a doc names that no router declares.

    Added 2026-08-25, closing the class cycle 13 found by reading:
    01-user-pillar.md documented `POST /api/pipeline/applications {"job_id": 42}`
    in THREE places. The real route is `POST /api/pipeline/{job_id}` with no
    body, and no `/applications` collection route has ever existed. Anyone
    integrating from that doc gets a 404 -- the most expensive kind of doc lie,
    because it looks like a contract.

    Path PARAMETERS are normalised, so `/pipeline/{job_id}` in the code matches
    `/pipeline/42` or `/pipeline/{id}` in a doc: the guard is about whether the
    ENDPOINT exists, not whether the placeholder is spelled the same.
    """
    routes_dir = ROOT / "backend/src/api/routes"
    if not routes_dir.exists():
        return []

    decl = re.compile(r"@router\.(get|post|put|patch|delete)\(\s*[\"']([^\"']*)")
    # A FastAPI path is assembled in THREE places: APIRouter(prefix=...), the
    # decorator, and include_router(prefix="/api"). Reading only the decorator
    # sees a third of the truth -- the first cut of this guard did exactly that
    # and reported five REAL routes (/api/auth/me, /api/settings/channels) as
    # missing. A guard that cries wolf gets ignored, which kills the loop.
    router_prefix = re.compile(r"APIRouter\(\s*prefix=[\"']([^\"']+)")
    known: set[tuple[str, str]] = set()
    for p in routes_dir.glob("*.py"):
        body = p.read_text(encoding="utf-8", errors="replace")
        pm = router_prefix.search(body)
        prefix = pm.group(1).rstrip("/") if pm else ""
        for m in decl.finditer(body):
            method, path = m.group(1).upper(), prefix + m.group(2)
            known.add((method, re.sub(r"\{[^}]+\}", "{}", path.rstrip("/")) or "/"))

    # Docs write the /api prefix that main.py mounts; the routers do not.
    NEGATED_ROUTE = re.compile(
        r"\b(there is no|no such|does not exist|never existed|is not a route|"
        r"was removed|no longer|instead of|not\s+`?[A-Z]{3,6}\s+/api)\b", re.I)
    cited = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\s+`?(/api/[A-Za-z0-9_{}/\-]+)")
    out: list[tuple[str, str, str]] = []
    for rel in living_stamped_docs():
        path = ROOT / rel
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in cited.finditer(line):
                method, doc_path = m.group(1), m.group(2)
                # A doc that says "There is no bare `GET /api/runs`" is CORRECT
                # (runbook.md:51 exists precisely to record that absence, and
                # the guard would have deleted its own evidence). But scanning
                # the WHOLE line for a negation silenced a REAL finding at
                # 01-user-pillar.md:505, whose far-right cell holds the UI copy
                # "Job no longer available". A false negative is worse than a
                # false alarm, so the negation must sit just BEFORE the route.
                if NEGATED_ROUTE.search(line[max(0, m.start() - 60):m.start()]):
                    continue
                tail = line[m.end():m.end() + 2]
                if doc_path.endswith("/") or tail.startswith(("*", "<")):
                    continue  # a wildcard/placeholder family, not one endpoint
                stripped = doc_path[len("/api"):].rstrip("/") or "/"
                norm = re.sub(r"\{[^}]+\}", "{}", stripped)
                # A concrete id in the doc (/pipeline/42) matches a param route.
                numeric = re.sub(r"/\d+", "/{}", norm)
                if (method, norm) in known or (method, numeric) in known:
                    continue
                out.append((rel, str(i), f"{method} {doc_path}"))
    return out


def constant_disagreements() -> list[tuple[str, str, str, str]]:
    """(const, doc:line, claimed, disagrees-with) where two docs give one CONSTANT different values.

    Added 2026-08-25. Every other guard here asks "does this doc match the
    code?". Only suite-baseline asks "do two docs disagree?" -- and in the
    cycle that prompted this, SIX of ten findings were internal contradictions:

      02-search-and-match-engine.md:297 called ENRICHMENT_THRESHOLD=60 the
      enrichment gate, while line 165 OF THE SAME FILE already described the
      real one (MIN_SCORE 10 + MAX_JOBS 20). glossary.md:91 said ESCO
      normalisation runs; the flags entry six lines below said it never has.

    Those need no code access to catch. A doc that argues with another doc is
    wrong somewhere by construction, and cheaper to detect than either half is
    to verify.

    Scope, deliberately narrow so this cannot become a permanent false alarm:
      * only ALL_CAPS_UNDERSCORE identifiers -- env vars and module constants,
        never prose;
      * only a number within ~40 chars after the name, so "X is unrelated to
        the 5 sources" does not bind;
      * a name claimed in ONE place is never reported: this guard is about
        disagreement, not about correctness. The code-vs-doc guards own that.
    """
    # A BINDING TOKEN is required between the name and the number: `=`, `|`
    # (env table), `(`, or the word "default". The first draft allowed any 40
    # characters, and immediately produced four false alarms -- `LIMIT 50` in
    # one SQL example against `LIMIT 20` in another (different queries, both
    # right), and `PILLAR 1/2/3` (three real pillars). A number that merely
    # FOLLOWS a word is not a claim about that word's value.
    pat = re.compile(
        r"\b([A-Z][A-Z0-9_]{4,})`?\s*(?:=|\||\(|:\s|—\s|defaults? (?:to )?)\s*\*{0,2}(\d{1,6})\b"
    )
    # SQL/markdown keywords that are never constants whose value can disagree.
    NOT_CONSTANTS = {"LIMIT", "OFFSET", "PILLAR", "SELECT", "WHERE", "ORDER", "GROUP", "STEP", "PHASE", "BATCH"}
    # A line explaining a RETIRED value must name it. "the old
    # ENRICHMENT_THRESHOLD=60 gate never fired" is correct documentation, not a
    # contradiction -- exactly the tombstone problem that made a dead-name
    # guard unshippable earlier: a doc recording an absence has to say what is
    # absent.
    # NOTE the absence of bare "was" / "were" / "deleted". The first draft had
    # them and the drill immediately proved the guard blind: ARCHITECTURE.md:10
    # reads "keywords.py WAS emptied", so a whole line of live claims was being
    # skipped. Ordinary past tense is not a historical marker -- it appears in
    # most prose. Only phrases that specifically flag a RETIRED VALUE qualify.
    HISTORICAL = re.compile(
        r"\b(the old|no longer|retired|legacy|back-compat|never fired|"
        r"superseded|deprecated|formerly|previously|used to be)\b",
        re.IGNORECASE,
    )
    # A CONDITIONAL value is not a claim about the default. "or
    # `ENRICHMENT_MAX_JOBS=0` — a zero budget selects nothing" documents a
    # failure mode and is correct; flagging it against the real default (20)
    # would be the fourth false-positive class this guard has had to learn,
    # after loose binding, bare "was", and bare "legacy". Same shape as the
    # retired-value tombstone: a doc describing when something breaks has to
    # name the value that breaks it.
    CONDITIONAL = re.compile(
        r"\b(if |when |unless |never runs|selects nothing|set to|setting|"
        r"would|could|suppose|e\.g\.|example)\b",
        re.IGNORECASE,
    )
    seen: dict[str, list[tuple[str, str]]] = {}
    for rel in LIVING_DOCS:
        path = ROOT / rel
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in pat.finditer(line):
                name, val = m.group(1), m.group(2)
                if name in NOT_CONSTANTS:
                    continue
                # Scope the historical test to the 40 characters BEFORE this
                # claim, not the whole line. A line-level filter blinded this
                # guard twice: ARCHITECTURE.md:10 says "keywords.py was
                # emptied ... the LEGACY module-level score_job() path" AND
                # states LOCATIONS (25) — one historical aside was suppressing
                # a live claim beside it. Third time a line-level filter has
                # done this (write-verbs on skill paths, bare "was", "legacy").
                # Look BOTH ways. The qualifier can precede the value ("if
                # X=0 …") or follow it ("`X=0` — a zero budget selects
                # nothing"). Checking only the left side left that second shape
                # firing, which is how this guard learned its fourth
                # false-positive class.
                before = line[max(0, m.start() - 40):m.start()]
                after = line[m.end():m.end() + 40]
                if HISTORICAL.search(before) or HISTORICAL.search(after):
                    continue
                if CONDITIONAL.search(before) or CONDITIONAL.search(after):
                    continue
                seen.setdefault(name, []).append((f"{rel}:{i}", val))

    out: list[tuple[str, str, str, str]] = []
    for name, claims in seen.items():
        values = {v for _, v in claims}
        if len(values) < 2:
            continue
        winner = max(values, key=lambda v: sum(1 for _, x in claims if x == v))
        for where, val in claims:
            if val != winner:
                others = ", ".join(w for w, x in claims if x == winner)
                out.append((name, where, val, f"{winner} at {others}"))
    return out


def collected_baseline_claims() -> list[tuple[str, str, int]]:
    """(doc, line, collected-count) for every stated test-suite baseline.

    NOT a check against a live collection. Collecting the suite imports conftest,
    which wants a Postgres on 5433, and this runs in a blocking CI step that must
    stay fast and offline — test_file_count() exists for what the filesystem can
    answer.

    This asks a different question, and the only one every other guard here is
    structurally unable to ask: DO TWO DOCS DISAGREE WITH EACH OTHER?

    Every guard above compares a doc to the code. None of them notices when six
    docs say 3,297 and two say ~1,409 — which is exactly what happened, with
    README.md contradicting ITSELF on one page (3,297 at :124, ~1,409 at :402)
    while every check stayed green. Root CLAUDE.md already warns about this
    exact failure: "three docs once disagreed by 400-800 tests".

    Consistency is checkable without a database. One baseline, stated the same
    everywhere, or red.
    """
    # "N collected" was the only phrasing watched, and DEPLOY.md:35 wrote
    # "1608 tests green" -- a test count in a doc that had never been read
    # against the others, missing this guard twice over: wrong words AND
    # outside the list it swept. Cycle 15 found it. Both holes closed here.
    pat = re.compile(r"([\d,]{3,})\s+(?:collected|tests?\s+(?:green|passing))")
    # ~~struck~~ text is RETRACTED by definition. Widening this guard to catch
    # "N tests green" made it fire on MONETIZATION_GAPS.md:29, where the old
    # count is struck through and annotated "long stale" -- a doc doing exactly
    # the right thing with a number it no longer claims. A guard that punishes
    # the correct way to retire a fact teaches people to delete the history
    # instead, which is worse than the drift it was written to catch.
    struck = re.compile(r"~~.*?~~", re.S)
    out: list[tuple[str, str, int]] = []
    for rel in living_stamped_docs():
        path = ROOT / rel
        if not path.exists():
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            spans = [m.span() for m in struck.finditer(line)]
            for m in pat.finditer(line):
                if any(a <= m.start() < b for a, b in spans):
                    continue  # retracted, not claimed
                try:
                    out.append((rel, str(i), int(m.group(1).replace(",", ""))))
                except ValueError:
                    continue
    return out


def skill_dead_paths() -> list[tuple[str, str, str]]:
    """(skill file, line, dead path) for repo paths .claude/skills/ names that do not exist.

    Added 2026-08-24. These files are INSTRUCTIONS AGENTS EXECUTE, not prose a
    human reads and mentally corrects, so a wrong path here is worse than a
    wrong sentence in a doc: the scout skill told an agent to append its
    findings to `D:\\dev\\job360\\docs\\maintenance\\MISSIONS.md`, a directory that
    has not existed since the maintenance docs moved under docs/harness/. Every
    scout pass following that rule wrote nowhere.

    scout/SKILL.md contradicted ITSELF -- line 9 cited the correct path in
    prose, line 23 (the operative rule) cited the dead one. Four skills carried
    the same stale root.

    Deliberately narrow: only `docs/...`-shaped paths, in backticks, and the
    Windows absolute form the skills actually use. Prose and example paths in
    other shapes never match.
    """
    skills = ROOT / ".claude/skills"
    if not skills.exists():
        return []
    pat = re.compile(
        r"`(?:D:\\dev\\job360\\)?((?:docs)[\\/][A-Za-z0-9_./\\-]+\.md)`"
    )
    # THE TEST IS THE PARENT DIRECTORY, not the file.
    #
    # A skill's OUTPUT files do not exist until it first runs -- DOC-HEALTH.md
    # and SCOUT-NOTES.md are created by the very skills that name them, and
    # flagging those is a permanent false alarm.
    #
    # The first attempt excluded any line containing a write verb. That was
    # worse than useless: scout/SKILL.md line 23 reads "Your only WRITABLE
    # files ... MISSIONS.md ... and SCOUT-NOTES.md", so the verb filter skipped
    # the whole line and took the read-target down with the write-target. The
    # drill caught it -- pointing MISSIONS.md at a dead directory left the
    # report green.
    #
    # A missing file whose FOLDER exists is a pending write. A path whose
    # folder does not exist is a real bug: the write fails too. That is exactly
    # the defect this guard was born for -- docs/maintenance/ no longer exists.
    out: list[tuple[str, str, str]] = []
    for p in sorted(skills.rglob("*.md")):
        rel_skill = p.relative_to(ROOT).as_posix()
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in pat.finditer(line):
                claimed = m.group(1).replace("\\", "/")
                if not (ROOT / claimed).parent.is_dir():
                    out.append((rel_skill, str(i), claimed))
    return out


def pillars_fully_watched() -> list[str]:
    """Every *.md under docs/product/pillars/ must be in LIVING_DOCS.

    Root CLAUDE.md calls that folder the AUTHORITATIVE code-verified
    architecture reference. A file sitting in it that nothing checks is a
    contradiction in terms, and it has now happened twice: 03-job-providers.md
    carried the pre-prune source counts for a week, and runbook.md told
    operators to run `sqlite3 data/jobs.db` against a Postgres database.

    Both times the fix was "add that file to the list", which fixes one file and
    leaves the next one exposed -- four of six were watched when the second bug
    landed in one of the other two. This asserts the WHOLE folder instead, so a
    new pillar doc is guarded the day it appears rather than the day someone
    remembers it.
    """
    folder = ROOT / "docs/product/pillars"
    if not folder.exists():
        return []
    watched = {w.replace("\\", "/") for w in LIVING_DOCS}
    missing = []
    for p in sorted(folder.glob("*.md")):
        rel = p.relative_to(ROOT).as_posix()
        if rel not in watched:
            missing.append(rel)
    return missing


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


def migration_file_count() -> int:
    """Number of forward-migration files, as a VALIDATED ``0000..head`` sequence.

    Distinct from ``migration_head()``: head is the highest NNNN, this is how
    many actually exist. Promoted 2026-08-24 by the nightly routine, which found
    ARCHITECTURE.md saying ``25-migration forward-compat schema`` and the
    search-and-match-engine pillar saying ``14-migration`` while 31 forward
    migrations existed. Six doc bumps (0025→0030) landed with the
    migration-head guard staying green, because that guard only watches
    ``0000 → NNNN`` phrasing, not the total.

    CodeRabbit, on the PR that added this: the first draft globbed every
    ``*.up.sql`` and merely DOCUMENTED the ``NNNN_`` shape. Deleting
    ``0020_....up.sql`` and adding ``notes.up.sql`` left both the count and the
    head unchanged, so a schema with a hole in it stayed green -- a count that
    does not measure the thing that breaks. The prefixes are now parsed and the
    run is required to be contiguous ``0000..head`` with no duplicates; a
    malformed or gapped set raises (exit 2), which is this file's contract:
    fail LOUD, never silently green.

    Second CodeRabbit round: it now shares ``_migration_pairs()`` with
    ``migration_head()``, so both read the RUNNER's definition of a migration
    (an ``.up.sql`` with a matching ``.down.sql``) rather than each globbing the
    directory its own way.
    """
    return len(_migration_pairs())


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
    # Moved 2026-08-25: the rules now live in a SKILL, which loads on demand.
    # They were 42% of a file that loads before every session, and Anthropic's
    # own guidance is that a bloated CLAUDE.md is why rules get ignored. The
    # count is still guarded — it just reads the file that now holds them.
    rules_file = ROOT / ".claude/skills/hard-rules/SKILL.md"
    if not rules_file.exists():
        rules_file = ROOT / "CLAUDE.md"
    text = rules_file.read_text(encoding="utf-8", errors="replace")
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
    # (fact-name, actual-value, regex-with-one-capture-group). Patterns are
    # deliberately SPECIFIC phrases so unrelated numbers never false-match.
    #
    # Every job-source / scoring-engine / ATS-catalog / dataclass-shape fact
    # this file used to guard was RETIRED 2026-09-05 (slice 5, #483) along
    # with the code it measured — Job360 no longer sources, ranks or
    # recommends a job (product rule 4), so there is nothing left in the old
    # orchestrator, the rate-limit table, the ATS company catalog or the
    # source classes to count. `landing-source-count` below is the one
    # survivor: it no longer compares to a live registry, it just refuses any
    # source-count claim from ever reappearing.
    checks = [
        # Added 2026-08-03. The checker tracked THREE facts, so every other
        # number in the docs rotted silently — an audit found the rule count,
        # the route/endpoint counts and three mutually-contradictory test counts
        # all wrong at once. A drift checker that only guards what it already
        # guarded is how a doc estate dies while its tripwire stays green.
        ("hard-rules", hard_rule_count(), r"the (\d+) hard rules"),
        ("route-modules", route_module_count(), r"(\d+) route modules"),
        ("endpoints", endpoint_count(), r"\((\d+) endpoints"),
        # Promoted from the nightly doc-truth routine 2026-08-24. Each of these
        # was drift the LLM found by reading; they are countable, so they belong
        # here where they cost nothing and are caught on every push.
        ("workflows", workflow_count(), r"(\d+) workflows in"),
        ("test-files", test_file_count(), r"across (\d+) `?test_\*\.py`? files"),
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

    # A doc that never says what it is cannot be checked OR left alone.
    for rel in unstamped_docs():
        drift.append((
            rel, "1", "unstamped-doc", "no <!-- doc: KIND -->",
            "a doc that never declares LIVING/PLAN/LOG/REFERENCE/FROZEN "
            "gets re-litigated every cycle and never converges",
        ))

    # The haystack itself may not grow. "Delete, do not reword" was only a line
    # in the routine's prompt until now, and a prompt is a request.
    for row in surface_regression():
        drift.append(("scripts/living_surface_ceiling.txt", "1", "surface-ratchet",
                      f"{living_surface()[1]:,} lines", row))

    # New raw line numbers in LIVING prose are refused (ratchet, may only fall).
    for row in line_citation_regressions():
        rel, _, detail = row.partition(": ")
        drift.append((rel, "1", "line-citation-ratchet", detail,
                      "a line number rots on any edit above it — cite a symbol"))

    # An env var the code never reads is a knob that does nothing.
    for doc, line_no, name in unreadable_env_vars():
        drift.append((doc, line_no, "env-var-not-read", name,
                      "listed as an environment variable, but no code reads it"))

    # A tree entry pointing at a file that is gone sends a reader, or an
    # agent, to a path that is not there.
    for doc, line_no, dead in doc_tree_dead_paths():
        drift.append((doc, line_no, "tree-dead-path", dead,
                      "directory-tree entry names a path that does not exist"))

    # A stamp the reader cannot see does not retire the claim under it.
    for rel in missing_reader_banner():
        drift.append((
            rel, "1", "no-reader-banner", "stamp is invisible",
            "an HTML-comment stamp hides the doc from the CHECKER while the "
            "reader still believes it",
        ))

    # A control byte inside a guard's own regex silently disables it.
    for gfile, line_no, byte in control_chars_in_guards():
        drift.append((
            gfile, line_no, "control-char", byte,
            "a control byte in a guard's regex makes it unable to ever match",
        ))

    # A documented endpoint that no router declares is a contract that 404s.
    for doc, line_no, route in documented_routes_exist():
        drift.append((
            doc, line_no, "route-not-found", route,
            "no @router declares this — a documented endpoint that 404s",
        ))

    # Do the docs agree with EACH OTHER about each named constant? Six of ten
    # findings in the 2026-08-25 scout pass were docs contradicting other docs
    # (and twice, themselves) about ENRICHMENT_THRESHOLD and friends.
    for const, where, claimed, against in constant_disagreements():
        doc, _, line_no = where.rpartition(":")
        drift.append((doc, line_no, f"disagree:{const}", claimed, against))

    # Do the docs agree with EACH OTHER about the suite baseline? Every other
    # guard compares a doc to the code; none can see six docs saying 3,297 while
    # two say ~1,409, or README.md contradicting itself on one page.
    baselines = collected_baseline_claims()
    distinct = {n for _, _, n in baselines}
    if len(distinct) > 1:
        agreed = max(distinct)  # the freshest measurement wins the comparison
        for rel, line_no, claimed in baselines:
            if claimed != agreed:
                drift.append((
                    rel, line_no, "suite-baseline", f"{claimed:,} collected",
                    f"{agreed:,} collected — docs must agree with each other",
                ))

    # The landing page is a claim to USERS. It drifted this way once already —
    # it advertised 47 sources against a registry of 41 for a week — and since
    # slice 5 (#483) there is no registry left to advertise a count OF, so any
    # claim at all is now the drift.
    for line_no, claimed in landing_page_source_claims():
        drift.append((
            "frontend (user-facing copy)", str(line_no), "landing-source-count",
            f"{claimed} sources",
            "0 — Job360 no longer sources, ranks or recommends jobs (VISION rule 4)",
        ))

    # Skills are executed, not read. A dead path there sends an agent's whole
    # output into a directory that does not exist.
    for skill, line_no, claimed in skill_dead_paths():
        drift.append((
            skill, line_no, "skill-dead-path", claimed,
            "path does not exist — skills are instructions agents ACT on",
        ))

    # The authoritative folder must be watched in full, not file by file.
    for rel in pillars_fully_watched():
        drift.append((
            rel, "-", "pillar-unwatched", "not in LIVING_DOCS",
            "docs/product/pillars/ is the AUTHORITATIVE reference — every file in it must be checked",
        ))

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
    print(f"Code facts: migration head = **{mig_head:04d}**\n")

    # The haystack. Never a failure -- a trend line. Findings-per-night can
    # fall because the scout got lazy; this cannot.
    surf_docs, surf_lines = living_surface()
    if surf_docs:
        print(f"LIVING surface: **{surf_docs}** docs, **{surf_lines:,}** lines of "
              f"prose that must be true. Deleting a line retires the claim; "
              f"rewording it renews it.\n")

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
