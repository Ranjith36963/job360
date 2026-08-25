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
    # Added 2026-08-24 (third batch) after the nightly routine found
    # runbook.md still telling operators to run `sqlite3 data/jobs.db` against a
    # database that has been Postgres since 2026-07-02 -- five dead commands in
    # the one file whose whole job is "I see a problem, what do I type".
    #
    # It survived every guard because it was one of TWO files in this folder
    # with no doc-type header and no place on this list. Adding files here one
    # at a time is what let that happen: four of six were watched, and the
    # drift landed in one of the other two. pillars_fully_watched() below now
    # asserts the whole folder is covered, so the list cannot fall behind the
    # directory again.
    "docs/product/pillars/README.md",
    "docs/product/pillars/runbook.md",
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


def built_source_classes() -> set[str]:
    """Class names `_build_sources()` actually INSTANTIATES.

    CodeRabbit, third round on PR #394, correcting a false claim this file made
    in its previous revision. `_build_sources()` does NOT iterate
    SOURCE_REGISTRY -- it hand-writes `all_sources = [ReedSource(...), ...]`
    (`backend/src/main.py:251-303`). The registry is a SEPARATE surface, used by
    the CLI `--source` choices and `GET /api/sources`.

    That gap is the whole point of CLAUDE.md rules #8/#13: registry and
    `_build_sources()` are two of the five surfaces that must move together
    precisely BECAUSE nothing makes them move together automatically. A source
    can sit in the registry, have a module on disk, and still never be polled.

    So "is this platform polled?" has exactly one honest answer: is its class in
    this list. Reading the registry instead was a guard pointed at the wrong
    authority -- the same mistake, one layer up, as reading the directory.
    """
    tree = ast.parse((ROOT / "backend/src/main.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_build_sources"):
            continue
        for st in ast.walk(node):
            if not isinstance(st, ast.Assign):
                continue
            if not any(getattr(x, "id", None) == "all_sources" for x in st.targets):
                continue
            if not isinstance(st.value, ast.List):
                raise RuntimeError("_build_sources: all_sources is not a list literal")
            names = {
                e.func.id for e in st.value.elts
                if isinstance(e, ast.Call) and isinstance(e.func, ast.Name)
            }
            if not names:
                raise RuntimeError("_build_sources: all_sources instantiates nothing")
            return names
    raise RuntimeError("all_sources list not found in _build_sources() in backend/src/main.py")


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


def locations_count() -> int:
    """Entries in LOCATIONS (backend/src/core/keywords.py). AST-strict.

    Added 2026-08-25, and the reason matters more than the number. This repo
    already had a `disagree:LOCATIONS` guard, and it was GREEN: it asks "do two
    docs agree?", and five LIVING docs agreed perfectly -- on 25, when the list
    holds 26. Cycle 14 caught it by counting the source.

    Consensus is not verification. A doc-vs-doc check can only ever find
    disagreement, never a shared falsehood, and shared falsehoods are exactly
    what documentation drifts toward, because docs get copied from each other.
    So this one counts the CODE, and the disagreement guard stays as the
    cheaper net for facts no extractor owns.

    The 26 includes "Remote" and "Hybrid", which `_location_score` skips when
    matching (skill_matcher.py:277-278) -- they are workplace modes living in a
    place list. Counted anyway: this guard reports what the list HOLDS, and a
    doc wanting to say "24 places" should say so in those words.
    """
    tree = ast.parse((ROOT / "backend/src/core/keywords.py").read_text(encoding="utf-8"))
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            if getattr(t, "id", None) == "LOCATIONS":
                if not isinstance(node.value, ast.List):
                    raise RuntimeError("LOCATIONS is not a plain list literal")
                return len(node.value.elts)
    raise RuntimeError("LOCATIONS list literal not found in backend/src/core/keywords.py")


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


def ats_slug_count() -> tuple[int, int]:
    """(total ATS company slugs, number of *_COMPANIES lists).

    Third promotion batch, 2026-08-24. The glossary said "~256 slugs across 11
    platforms" against a real 302. A tilde is not a measurement -- it is a
    number nobody intends to keep true.
    """
    tree = ast.parse((ROOT / "backend/src/core/companies.py").read_text(encoding="utf-8"))
    total = lists = 0
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            name = getattr(t, "id", None)
            if name and name.endswith("_COMPANIES") and isinstance(node.value, (ast.List, ast.Tuple)):
                total += len(node.value.elts)
                lists += 1
    if not lists:
        raise RuntimeError("no *_COMPANIES list literals found in backend/src/core/companies.py")
    return total, lists


def ats_platform_slugs() -> dict[str, int]:
    """{'GREENHOUSE': 82, 'LEVER': 35, ...} — slugs per *_COMPANIES list.

    ats_slug_count() returns only the TOTAL, which is why README's per-platform
    breakdown table rotted invisibly: the total guard was satisfied by the
    glossary's one-line claim while the table beneath it disagreed on six of
    eleven rows (Greenhouse 80/82, Workable 25/21, Pinpoint 15/39,
    Recruitee 20/31, Personio 18/26, total ~264/302).

    Promoted 2026-08-24 after CodeRabbit flagged the stale total. The rows are
    worse than the total: someone reading "Pinpoint 15" plans against a quarter
    of the real board list.
    """
    tree = ast.parse((ROOT / "backend/src/core/companies.py").read_text(encoding="utf-8"))
    out: dict[str, int] = {}
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
        for t in targets:
            name = getattr(t, "id", None)
            if name and name.endswith("_COMPANIES") and isinstance(node.value, (ast.List, ast.Tuple)):
                out[name[: -len("_COMPANIES")]] = len(node.value.elts)
    if not out:
        raise RuntimeError("no *_COMPANIES list literals found in backend/src/core/companies.py")
    return out


def active_ats_inventory() -> tuple[int, int]:
    """(ATS source classes, slugs those classes actually poll).

    CONFIGURED is not ACTIVE, and the gap is load-bearing: companies.py holds
    302 slugs across 11 platform lists, but `RIPPLING_COMPANIES` has had no
    source class since the 2026-08-10 rotation, so 10 boards poll 297. Docs
    state BOTH numbers, and only the configured one was ever guarded — a
    rotation that retires another platform would leave "297" stale and green.

    Added 2026-08-24 at CodeRabbit's request on PR #394. Derived, not typed: a
    platform counts as active when it is actually POLLED, so retiring one moves
    both numbers automatically.

    "Polled" is read from the classes `_build_sources()` INSTANTIATES, and it
    took two corrections to get there. Draft 1 scanned module filenames in
    `sources/ats/`, so a module left on disk counted forever. Draft 2 switched
    to SOURCE_REGISTRY on the belief that `_build_sources()` iterates it -- it
    does not (CodeRabbit, third round): the registry is a separate surface for
    the CLI and `GET /api/sources`, while `_build_sources()` hand-writes its own
    `all_sources` list. A source can be in the registry, have a module on disk,
    and still never be fetched.

    Both drafts made the same mistake at different depths: guarding a proxy for
    the behaviour instead of the behaviour. `built_source_classes()` is the list
    the pipeline actually constructs, so it is the only one that answers
    "does this platform get polled?".
    """
    per_platform = ats_platform_slugs()
    # GREENHOUSE -> greenhousesource; a platform is active when some class the
    # pipeline builds starts with its name. RIPPLING_COMPANIES has no
    # RipplingSource, which is exactly why 302 configured and 297 polled differ.
    built = {c.lower().replace("_", "") for c in built_source_classes()}
    active = {
        k: v for k, v in per_platform.items()
        if any(c.startswith(k.lower().replace("_", "")) for c in built)
    }
    return len(active), sum(active.values())


def dataclass_field_count(rel: str, cls: str) -> int:
    """Annotated fields on a dataclass. Used for Job and JobEnrichment.

    The glossary described `Job` as "~27 fields" (31) and `JobEnrichment` as
    "18 fields, 8 enums" (16, 7). These are the shapes every source and the
    whole scorer are written against, so a wrong count teaches the wrong model.
    """
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return len([x for x in node.body if isinstance(x, ast.AnnAssign)])
    raise RuntimeError(f"class {cls} not found in {rel}")


def enrichment_enum_count() -> int:
    """Enum classes in the JobEnrichment schema module."""
    tree = ast.parse(
        (ROOT / "backend/src/services/job_enrichment_schema.py").read_text(encoding="utf-8")
    )
    return len([
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
        and any(getattr(b, "id", "") == "str" or getattr(b, "attr", "") == "Enum" for b in n.bases)
    ])


def landing_page_source_claims() -> list[tuple[int, int]]:
    """(line, claimed count) for every source-count claim on the landing page.

    Added 2026-08-24. This is the only guard here that watches CODE rather than
    a doc, and it exists because the doc was RIGHT and the code was LYING:
    01-user-pillar.md faithfully quoted the landing page as "47 sources", and
    the page really did say 47 -- to every visitor of job360.uk -- while the
    registry held 41. Six sources were pruned on 2026-08-17 and the marketing
    copy never moved.

    Doc-sync found it by looking at the seam between doc and code. The nightly
    routine never could: it may only edit *.md.
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
        "frontend/src/lib/catalog.ts",
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


# The source subfolders that MUST exist. Named explicitly rather than
# discovered, so a deleted or renamed folder is drift instead of a guard that
# quietly stops existing -- see source_subfolder_counts().
EXPECTED_SOURCE_SUBFOLDERS = (
    "apis_keyed", "apis_free", "ats", "feeds", "other", "scrapers",
)


def source_subfolder_counts() -> dict[str, int]:
    """{'apis_keyed': N, 'apis_free': N, 'ats': N, 'feeds': N, 'other': N, 'scrapers': N}

    File count per source subfolder, excluding ``__init__.py`` and ``base.py``.
    Docs everywhere use a tree diagram of ``apis_keyed/ (8)  apis_free/ (9) ...``
    — the numbers rot every rotation, and until today no guard watched them.

    Promoted 2026-08-24 by the nightly routine. README.md said ``ats/ (12)``
    against 10, ``feeds/ (8)`` against 4, ``scrapers/ (7)`` against 5.

    CodeRabbit, on the PR that added this: the first draft DISCOVERED the
    folders by iterating the directory, so deleting ``ats/`` deleted the
    ``subfolder-ats`` guard along with it and left ``ats/ (10)`` green forever
    — a check that cannot be made to go red on demand. The expected set is now
    named as a constant and a missing folder yields a count of 0, which the
    docs' non-zero claim then contradicts loudly. A NEW folder is still
    discovered (and, having no doc claim, trips the "claim not found in any
    doc" alarm), so the set can grow without editing this file.
    """
    out: dict[str, int] = {}
    base = ROOT / "backend/src/sources"
    # A missing folder scores 0 rather than vanishing: the guard must survive
    # the deletion of the thing it guards.
    for name in EXPECTED_SOURCE_SUBFOLDERS:
        out[name] = 0
    if not base.is_dir():
        return out
    for d in base.iterdir():
        if d.is_dir() and d.name != "__pycache__":
            out[d.name] = len([
                p for p in d.glob("*.py") if p.name not in {"__init__.py", "base.py"}
            ])
    return out


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
    ats_slugs, ats_lists = ats_slug_count()
    job_fields = dataclass_field_count("backend/src/models.py", "Job")
    enr_fields = dataclass_field_count(
        "backend/src/services/job_enrichment_schema.py", "JobEnrichment"
    )
    enr_enums = enrichment_enum_count()
    active_boards, active_slugs = active_ats_inventory()

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
        # Counted from the CODE, deliberately alongside the doc-vs-doc
        # `disagree:LOCATIONS` net. That net was green while five LIVING docs
        # agreed on 25 and the list held 26 -- agreement is not truth.
        # UPPERCASE only, and that is the whole rule. A looser second pattern
        # allowing the lowercase word fired on "top 8 titles x top 2 locations"
        # (ARCHITECTURE.md:474, 01-user-pillar.md:286) -- a search-query fan-out
        # that has nothing to do with the constant. Same lesson as the SOURCE
        # guard, opposite direction: there, case-insensitivity hid a real lie;
        # here, it invented two.
        ("locations", locations_count(), r"`?LOCATIONS`?\s*\((\d+)\)"),
        ("locations", locations_count(), r"(\d+)\s+entries in `?LOCATIONS`?\b"),
        ("rate-limits", rate_limit_count(), r"`?RATE_LIMITS`?[^.\n]{0,40}?\((\d+) entries"),
        ("rate-limits", rate_limit_count(), r"`?RATE_LIMITS`? dict in `?settings\.py`? \((\d+) entries"),
        ("subclasses", source_subclass_count(), r"checking all (\d+) subclasses"),
        ("registry", registry, r"from a (\d+)-key `?SOURCE_REGISTRY"),
        ("registry", registry, r"The (\d+)-key dict in `?main\.py`?"),
        ("unique-classes", unique_classes, r"[Bb]uilds (\d+) instances"),
        # Third batch, 2026-08-24: the remaining countable facts in the glossary,
        # the densest such file in the repo. Each was wrong until today.
        ("ats-slugs", ats_slugs, r"\((\d+) slugs across \d+ platforms\)"),
        ("ats-platforms", ats_lists, r"\(\d+ slugs across (\d+) platforms\)"),
        ("job-fields", job_fields, r"every source must produce: (\d+) fields"),
        ("enrichment-fields", enr_fields, r"shape with (\d+) strict-typed fields"),
        ("enrichment-enums", enr_enums, r"strict-typed fields, (\d+) enums"),
        # Deliberately NOT matching `SOURCE_INSTANCE_COUNT = N`. That is a
        # verbatim code quote, and docs legitimately cite it -- including dated
        # decision notes that are correct at the time of writing. Guarding it
        # fires on every honest citation, and a permanent false alarm is how a
        # loop dies. The prose form above covers the claim that matters.
        #
        # Fourth batch, 2026-08-24 -- promoted from the nightly routine after
        # ARCHITECTURE.md read "25-migration forward-compat schema" and the
        # search-and-match-engine pillar read "14-migration" against 31 actual
        # forward migrations. The existing `migration-head` guard watches only
        # "0000 → NNNN" phrasing, so both stale numbers slid past for weeks.
        ("migrations-schema", migration_file_count(),
         r"(\d+)-migration forward-compat schema"),
        # The same COUNT, stated two other ways in the directory trees. Found by
        # CodeRabbit on PR #394: both lines end "(0000 → 0030)", so
        # `migration-head` matched them and they LOOKED guarded -- but that guard
        # reads the HEAD, not the count beside it. Adding migration 0031 would
        # correctly force the head to 0031 while "31 forward/reverse SQL
        # migrations" quietly stayed 31. A guard on the same line is not a guard
        # on the same fact.
        ("migrations-schema", migration_file_count(),
         r"(\d+) forward/reverse SQL migrations"),
        ("migrations-schema", migration_file_count(),
         r"(\d+) forward\+reverse SQL migration pairs"),
        # Sixth batch, 2026-08-24, at CodeRabbit's request on PR #394.
        # CONFIGURED (302 slugs / 11 lists) was guarded; ACTIVE (10 boards
        # polling 297) was not, though the docs state both. A rotation that
        # retires another platform would leave "297" stale and green.
        ("ats-boards-active", active_boards, r"\*\*(\d+) ATS boards\*\* polling"),
        ("ats-boards-active", active_boards, r"ATS Boards \((\d+), \d+ slugs polled\)"),
        ("ats-boards-active", active_boards, r"so (\d+) ATS boards poll"),
        ("ats-slugs-active", active_slugs, r"polling (\d+) company slugs"),
        ("ats-slugs-active", active_slugs, r"ATS Boards \(\d+, (\d+) slugs polled\)"),
        ("ats-slugs-active", active_slugs, r"ATS boards poll (\d+) company slugs"),
        # A THIRD wording of the same two numbers, added to the companies.py
        # tree comment in ARCHITECTURE.md:54 / README.md:379 while this PR was
        # open: "297 polled across 10 ATS sources". Found by CodeRabbit.
        #
        # This is the failure mode the "claim not found in any doc" alarm cannot
        # catch, and the reason it cannot is worth stating: that alarm is keyed
        # on the FACT NAME, not the site. Both facts already had other matching
        # claims elsewhere, so matches_per_fact stayed non-zero and the checker
        # reported a clean run while two fresh, unwatched copies of the same
        # numbers sat in the two most-read files in the repo. Every new phrasing
        # of a guarded fact needs its own pattern.
        ("ats-boards-active", active_boards, r"\d+ polled across (\d+) ATS sources"),
        ("ats-slugs-active", active_slugs, r"(\d+) polled across \d+ ATS sources"),
    ]

    # Per-platform ATS slug counts. The TOTAL was guarded; the breakdown table
    # under it was not, and six of its eleven rows were stale (Greenhouse
    # 80/82, Workable 25/21, Pinpoint 15/39, Recruitee 20/31, Personio 18/26).
    # A reader planning against "Pinpoint 15" is off by a factor of two and a
    # half. Matches the README table row `| Pinpoint | 39 | ... |`.
    #
    # The negative lookahead is load-bearing, not tidiness. 03-job-providers.md
    # has a RATE-LIMIT table whose rows are `| personio | 1 | 3.0 | XML feed |`
    # — same shape, different meaning — and the matcher below runs IGNORECASE,
    # so the first draft read that "1" as a slug count and reported personio as
    # 1-vs-26 drift on a doc that was perfectly correct. Requiring the third
    # column NOT to open with a decimal separates the two tables: slug rows
    # carry prose notes or nothing, rate rows carry a delay like `3.0`.
    # A permanent false alarm is how a loop dies (see the module docstring).
    for _plat, _n in ats_platform_slugs().items():
        _label = re.escape(_plat.title().replace("_", ""))
        checks.append((
            f"ats-slugs-{_plat.lower()}",
            _n,
            rf"^\|\s*{_label}\s*\|\s*(\d+)\s*\|(?!\s*\d+\.\d)",
        ))

    # Per-subfolder source counts. Docs everywhere use a tree diagram
    # `apis_keyed/ (8)  ats/ (10) ...` and the numbers rot every rotation;
    # until today no guard watched them and README.md carried three drifted
    # values (ats/12, feeds/8, scrapers/7) against real 10/4/5.
    for _name, _count in source_subfolder_counts().items():
        checks.append((
            f"subfolder-{_name}",
            _count,
            rf"\b{re.escape(_name)}/\s*\((\d+)\)",
        ))

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

    # The landing page is a claim to USERS, and it drifted the same way a doc
    # does — it advertised 47 sources against a registry of 41 for a week.
    for line_no, claimed in landing_page_source_claims():
        if claimed != registry:
            drift.append((
                "frontend (user-facing copy)", str(line_no), "landing-source-count",
                str(claimed), str(registry),
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
    print(f"Code facts: SOURCE_REGISTRY entries = **{registry}**, "
          f"unique source classes = **{unique_classes}**, "
          f"migration head = **{mig_head:04d}**\n")

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
