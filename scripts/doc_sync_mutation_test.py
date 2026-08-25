"""Prove every doc_sync_check.py guard can actually go RED.

A green drift report means one of two very different things: "the docs are
correct", or "my regex matched nothing and I am watching an empty room". They
are indistinguishable from the outside, and the second is how a stale number
survives for months behind a passing check -- `SCORER_VERSION` sat at 4 in the
docs while the code said 7, with a green tripwire the whole time.

So each guard is broken ON PURPOSE, one at a time, and must report drift.
Restore is byte-level (read_bytes/write_bytes): a text round-trip rewrites line
endings on Windows and would leave the tree dirty after a "successful" run.

Usage (from repo root):
    python scripts/doc_sync_mutation_test.py

Exit 0 = every guard proven able to fail. Exit 1 = at least one guard is blind.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ["JOB360_ROOT"]) if os.environ.get("JOB360_ROOT") else Path(__file__).resolve().parents[1]

# The drift report and these findings quote doc text full of arrows and
# em-dashes. A Windows console defaults to cp1252 and this script died mid-report
# on a single "→" -- a guard that crashes has not failed safe, it has stopped
# answering, which is the very thing this file exists to prevent.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# (doc, regex with ONE capture group, replacement that lies, fact name in the report)
CASES: list[tuple[str, str, str, str]] = [
    ("CLAUDE.md", r"SOURCE_REGISTRY`? has (\d+) entries", "SOURCE_REGISTRY` has 999 entries", "registry"),
    ("CLAUDE.md", r"(\d+) unique source classes", "999 unique source classes", "unique-classes"),
    ("CLAUDE.md", r"SCORER_VERSION`?\s*=\s*\*{0,2}(\d+)", "SCORER_VERSION` = **999", "scorer-version"),
    ("CLAUDE.md", r"(\d+) workflows in", "999 workflows in", "workflows"),
    ("ARCHITECTURE.md", r"across (\d+) `?test_\*\.py`? files", "across 999 test_*.py files", "test-files"),
    ("frontend/CLAUDE.md", r"Next\.js (\d+\.\d+\.\d+)", "Next.js 1.2.3", "nextjs-version"),
    ("frontend/CLAUDE.md", r"React (\d+\.\d+\.\d+)", "React 4.5.6", "react-version"),
    # Second promotion batch, 2026-08-24.
    ("docs/product/pillars/03-job-providers.md",
     r"checking all (\d+) subclasses", "checking all 999 subclasses", "subclasses"),
    ("docs/product/pillars/glossary.md",
     r"`RATE_LIMITS` dict in `settings\.py` \((\d+) entries",
     "`RATE_LIMITS` dict in `settings.py` (999 entries", "rate-limits"),
    # Third batch, 2026-08-24 — the glossary's remaining countable facts.
    ("docs/product/pillars/glossary.md",
     r"\((\d+) slugs across \d+ platforms\)", "(999 slugs across 11 platforms)", "ats-slugs"),
    ("docs/product/pillars/glossary.md",
     r"\(\d+ slugs across (\d+) platforms\)", "(302 slugs across 99 platforms)", "ats-platforms"),
    ("docs/product/pillars/glossary.md",
     r"every source must produce: (\d+) fields", "every source must produce: 999 fields", "job-fields"),
    ("docs/product/pillars/glossary.md",
     r"shape with (\d+) strict-typed fields", "shape with 999 strict-typed fields",
     "enrichment-fields"),
    ("docs/product/pillars/glossary.md",
     r"strict-typed fields, (\d+) enums", "strict-typed fields, 99 enums", "enrichment-enums"),
    # The only CODE file guarded here. It shipped "47 Job Sources" to every
    # visitor of job360.uk for a week after the roster dropped to 41.
    # The only CODE files guarded here. The copy shipped "47 Job Sources" to
    # every visitor of job360.uk for a week after the roster dropped to 41 --
    # on the landing page, in the site metadata Google and social cards read,
    # and in the footer on every page.
    #
    # The constant is drilled SEPARATELY and deliberately. The first version of
    # this guard filtered lines with `"ource" not in line`, which skipped
    # `SOURCE_COUNT = 41` because that spelling is uppercase: setting it to 99
    # left the report green. Drilling only the page copy would never have found
    # that. One capital letter, one blind guard.
    ("frontend/src/lib/catalog.ts",
     r"SOURCE_COUNT = (\d+)", "SOURCE_COUNT = 999", "landing-source-count"),
    # Eighth batch, 2026-08-25. The disagreement guard: does one doc contradict
    # ANOTHER doc about a named constant? Six of ten findings in the cycle that
    # prompted it were exactly this, twice within a single file.
    #
    # The mutation flips one of the two agreeing ENRICHMENT_MIN_SCORE claims,
    # which is the only way to make a DISAGREEMENT guard red: with every copy
    # equal there is nothing to disagree about.
    # Anchored on UK_LOCATIONS, which README.md and ARCHITECTURE.md both state
    # as 25. Flipping ONE of an agreeing pair is the only mutation that can
    # make a DISAGREEMENT guard red: with every copy equal there is nothing to
    # disagree about.
    #
    # The first draft mutated a line reading "the old ENRICHMENT_THRESHOLD=60
    # gate never fired" -- which this guard deliberately SKIPS, because a doc
    # explaining a retired value has to name it. The drill reported the guard
    # blind, correctly: I had pointed it at the one line the guard is designed
    # not to read.
    ("ARCHITECTURE.md",
     r"`LOCATIONS` \((\d+)\) and", "`LOCATIONS` (777) and", "disagree:LOCATIONS"),
    # Fourth batch, 2026-08-24, from the nightly routine.
    #
    # suite-baseline is the odd one out and the point of it: every other guard
    # asks "does this doc match the code?". This one asks "do two docs disagree
    # with EACH OTHER?" -- the question none of the others can ask, and the one
    # that would have caught README.md contradicting itself on a single page
    # (3,297 collected at :124, ~1,409 at :402) while every check stayed green.
    # Breaking ONE doc's number is therefore a real mutation: it creates the
    # disagreement.
    # Points at CONTRIBUTING.md, not ARCHITECTURE.md, and the move is the
    # lesson. #393 deleted the collected count from every doc that stated it as
    # fact -- correctly: a number no guard can check against the code rots
    # silently, so the fix is NO number, not a better one. That left this drill
    # mutating a claim that no longer existed, and the drill SAID SO rather
    # than passing quietly.
    #
    # CONTRIBUTING.md keeps it twice on purpose, as the merge-gate ratchet
    # floor -- a policy threshold, not a claim about current state. Two sites
    # is exactly what this guard needs: it fires on DISAGREEMENT, so mutating
    # one of the pair is the only mutation that can make it red.
    ("CONTRIBUTING.md",
     r"([\d,]{3,}) collected", "9,999 collected", "suite-baseline"),
    ("backend/CLAUDE.md",
     r"(SQLite|Postgres) via psycopg3", "SQLite table via psycopg3", "stale-phrase"),
    # Seventh batch, 2026-08-24. Skills are INSTRUCTIONS AGENTS EXECUTE, so a
    # dead path there is worse than a wrong sentence: the scout skill told an
    # agent to append its findings to `D:\dev\job360\docs\maintenance\MISSIONS.md`,
    # a directory gone since the maintenance docs moved under docs/harness/.
    # Every scout pass following that rule wrote nowhere. Four skills carried
    # the same stale root, and scout/SKILL.md contradicted itself: line 9 had
    # the correct path in prose, line 23 -- the operative rule -- had the dead one.
    (".claude/skills/scout/SKILL.md",
     r"docs\\(harness)\\maintenance\\MISSIONS\.md",
     # Replacement goes through re.subn, so each literal backslash must be
     # doubled here or `\n` / `\m` are read as escapes.
     "docs\\\\nowhere\\\\maintenance\\\\MISSIONS.md", "skill-dead-path"),
    # Sixth batch, 2026-08-24. The nightly routine found runbook.md still
    # telling operators to run `sqlite3 data/jobs.db` against a Postgres
    # database -- five dead COMMANDS, not stale prose. The four SQLite entries
    # in FORBIDDEN_PHRASES all pin sentences; none matched a shell command.
    ("docs/product/pillars/runbook.md",
     # Anchored on `psql ` rather than the longer `railway run -s Postgres psql`
     # form this drill originally targeted. #396 rewrote the runbook to use bare
     # SQL blocks with a connect line, so the longer string vanished and the
     # drill reported "guard watches nothing" -- correctly. Second time a drill
     # has caught its own target being removed by someone else's fix (the first
     # was suite-baseline after #393). That is the drill working, not failing.
     r"(psql) postgresql://", "sqlite3 data/jobs.db postgresql://",
     "stale-phrase"),
    # Fifth batch, 2026-08-24. Two "N-thing schema" style guards promoted by the
    # nightly routine after ARCHITECTURE.md carried "25-migration forward-compat
    # schema" (real 31) and README.md's source-tree said `ats/ (12)` (real 10).
    # The migration-head guard could not see either — it watches "0000 → NNNN"
    # phrasing only — and no per-subfolder count was ever guarded.
    ("ARCHITECTURE.md",
     r"(\d+)-migration forward-compat schema",
     "999-migration forward-compat schema", "migrations-schema"),
    ("ARCHITECTURE.md",
     r"\bats/\s*\((\d+)\)", "ats/ (999)", "subfolder-ats"),
]


def structural_drills() -> list[str]:
    """Drills for failure paths a TEXT mutation cannot express.

    The CASES above all work by planting a lie in a file and re-running the
    checker. Two failure modes cannot be reached that way, and CodeRabbit
    caught both on the PR that added the migration/subfolder guards:

    1. Deleting a source subfolder used to delete its own guard. The checker
       discovered folders by iterating the directory, so removing ``ats/``
       removed ``subfolder-ats`` too and left a stale ``ats/ (10)`` green
       forever. There is no text to mutate here -- the bug is an ABSENT check.
    2. The migration guard counted every ``*.up.sql`` while documenting the
       ``NNNN_`` shape, so a gapped or malformed sequence (delete 0020, add
       ``notes.up.sql``) kept both count and head unchanged and stayed green.

    Both are checked against a THROWAWAY root, never the real tree: renaming a
    live source package to prove a point is how a drill becomes an outage.
    """
    import tempfile  # noqa: PLC0415 — only this drill needs it

    sys.path.insert(0, str(ROOT / "scripts"))
    import doc_sync_check as dsc  # noqa: PLC0415

    failures: list[str] = []
    real_root = dsc.ROOT

    with tempfile.TemporaryDirectory() as tmp:
        fake = Path(tmp)
        try:
            dsc.ROOT = fake

            # 1. Every expected subfolder must still yield a guard (count 0)
            #    when backend/src/sources/ is not there at all.
            counts = dsc.source_subfolder_counts()
            for name in dsc.EXPECTED_SOURCE_SUBFOLDERS:
                if name not in counts:
                    failures.append(
                        f"subfolder-{name}: guard VANISHES when the folder is missing "
                        "— a deleted folder would silently retire its own check"
                    )
                elif counts[name] != 0:
                    failures.append(
                        f"subfolder-{name}: missing folder scored {counts[name]}, expected 0"
                    )

            # 2. A gapped migration sequence must RAISE, not count quietly.
            migs = fake / "backend" / "migrations"
            migs.mkdir(parents=True)
            for n in (0, 1, 3):  # deliberate hole at 0002
                (migs / f"{n:04d}_drill.up.sql").write_text("-- drill\n", encoding="utf-8")
            try:
                got = dsc.migration_file_count()
                failures.append(
                    f"migrations-schema: a sequence with a hole at 0002 returned {got} "
                    "instead of raising — a schema that cannot rebuild from 0000 stayed green"
                )
            except RuntimeError:
                pass

            # 3. A file the runner cannot order must RAISE too.
            (migs / f"{2:04d}_drill.up.sql").write_text("-- drill\n", encoding="utf-8")
            (migs / "notes.up.sql").write_text("-- not a migration\n", encoding="utf-8")
            try:
                got = dsc.migration_file_count()
                failures.append(
                    f"migrations-schema: a malformed 'notes.up.sql' counted as a migration "
                    f"(returned {got}) instead of raising"
                )
            except RuntimeError:
                pass

            # 4. TWO files claiming the same prefix must RAISE. CodeRabbit, on
            #    PR #394: cases 2 and 3 leave the duplicate-prefix branch
            #    unexercised, so deleting it would not fail this drill. A
            #    contiguous 0000..0002 run with 0002 claimed twice is the
            #    smallest fixture that isolates it from the gap and malformed
            #    checks above.
            (migs / "notes.up.sql").unlink()
            (migs / "0002_duplicate.up.sql").write_text("-- drill\n", encoding="utf-8")
            try:
                got = dsc.migration_file_count()
                failures.append(
                    f"migrations-schema: two files claiming prefix 0002 returned {got} "
                    "instead of raising — duplicate-prefix detection is dead code"
                )
            except RuntimeError:
                pass
        finally:
            dsc.ROOT = real_root

    # 5. The GENERATED guard, not just the count behind it. CodeRabbit, on
    #    PR #394: drill 1 proves source_subfolder_counts() keeps a zero entry,
    #    but a regression that filtered zero-count folders out of
    #    build_checks() would still pass it -- the count survives while the
    #    check it feeds disappears. Force ats to 0 and require the emitted
    #    check list to still carry `subfolder-ats`. Runs against the REAL root
    #    (build_checks parses main.py, settings.py, companies.py...), with only
    #    the counts function swapped.
    real_counts = dsc.source_subfolder_counts
    try:
        dsc.source_subfolder_counts = lambda: {**real_counts(), "ats": 0}  # noqa: E731
        emitted = {name for name, _, _ in dsc.build_checks()[0]}
        if "subfolder-ats" not in emitted:
            failures.append(
                "subfolder-ats: build_checks() DROPS the guard when the folder counts 0 "
                "— the count survives but the check it feeds does not"
            )
    finally:
        dsc.source_subfolder_counts = real_counts

    # 3. A NEW pillar doc must be caught the day it appears.
    #
    # This one cannot use a throwaway root: pillars_fully_watched() compares the
    # real folder against the real LIVING_DOCS, and the bug it guards is a file
    # existing that nobody listed. So a real file is created and removed under
    # try/finally -- a drill that leaves litter in the authoritative folder
    # would be worse than the bug.
    probe = ROOT / "docs/product/pillars/_drill_probe.md"
    try:
        probe.write_text("# drill probe\n", encoding="utf-8")
        missing = dsc.pillars_fully_watched()
        if not any(p.endswith("_drill_probe.md") for p in missing):
            failures.append(
                "pillar-unwatched: a new file in docs/product/pillars/ was NOT reported "
                "— the folder-coverage guard is blind"
            )
    finally:
        probe.unlink(missing_ok=True)

    if probe.exists():  # belt and braces: never leave litter behind
        failures.append(f"pillar-unwatched: drill failed to clean up {probe}")

    if not failures:
        print("PASS  structural      missing subfolder (count + emitted guard), "
              "gapped/malformed/duplicate migrations, unwatched pillar doc")
    return failures


def unwatched_claims() -> list[str]:
    """Find docs that state a guarded fact but are NOT in LIVING_DOCS.

    The other half of this drill, and the one the registry specifically asks
    for: "a doc it is not watching". The checker has shipped blind exactly this
    way before -- frontend/CLAUDE.md carried a stale "The 28 hard rules" while
    being absent from LIVING_DOCS, so the one file with the wrong number was
    the one file the guard could not see.

    Planting a lie only proves the docs on the LIST are scanned. It says
    nothing about a doc that should be on the list and isn't. This does.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import doc_sync_check as dsc  # noqa: PLC0415

    watched = {w.replace("\\", "/") for w in dsc.LIVING_DOCS}
    patterns = [p for _, _, p in dsc.build_checks()[0]]

    # graphify-out/ holds dated, machine-generated graph snapshots. They are
    # frozen records of what the repo looked like on a day, so a "stale" number
    # in one is correct, not drift. Archives are excluded for the same reason.
    # graphify-out/ holds dated machine-generated graph snapshots; docs/harness/
    # fable/ and reviews/ hold dated audit and review records. All three are
    # FROZEN accounts of what was true on a day. A "stale" number in a dated
    # record is correct — rewriting it would falsify the record. Only LIVING
    # docs owe agreement with today's code.
    skip_dirs = (
        "node_modules", ".git", "_archive", "archive",
        "graphify-out", "fable", "reviews",
    )

    findings: list[str] = []
    for path in ROOT.rglob("*.md"):
        parts = path.parts
        if any(skip in parts for skip in skip_dirs):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if rel in watched:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pat in patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                findings.append(f"{rel} claims /{pat}/ -> {m.group(0)!r} but is not in LIVING_DOCS")
                break
    return findings


def main() -> int:
    failures: list[str] = []

    for rel, pattern, replacement, fact in CASES:
        path = ROOT / rel
        if not path.exists():
            failures.append(f"{fact}: {rel} does not exist")
            continue

        original = path.read_bytes()
        text = original.decode("utf-8", errors="replace")
        mutated, n = re.subn(pattern, replacement, text, count=1)

        if n == 0:
            # The doc no longer makes this claim at all. Either the claim was
            # reworded (so the guard is now blind) or the pattern is wrong.
            failures.append(f"{fact}: no claim matching /{pattern}/ in {rel} — guard watches nothing")
            continue

        try:
            path.write_bytes(mutated.encode("utf-8"))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "doc_sync_check.py")],
                capture_output=True,
                # Explicit utf-8, never text=True: that decodes with the
                # machine's locale (cp1252 on Windows), and the drift report
                # is full of em-dashes. Caught by scripts/encoding_guard.py --
                # this file's first draft printed "MUTATION TEST FAILED <?>".
                encoding="utf-8",
                errors="replace",
                cwd=str(ROOT),
            )
            if fact in (proc.stdout or ""):
                print(f"PASS  {fact:16s} went RED when {rel} lied")
            else:
                failures.append(f"{fact}: stayed GREEN on a broken {rel} — the guard is blind")
        finally:
            path.write_bytes(original)

    # Failure paths no text mutation can express: an ABSENT check (deleted
    # source folder) and a structurally broken migration run.
    failures.extend(structural_drills())

    # Second half of the drill: docs that make a guarded claim while sitting
    # outside LIVING_DOCS. Planting a lie proves the LISTED docs are scanned;
    # this proves nothing that matters is missing from the list.
    for finding in unwatched_claims():
        failures.append(f"unwatched-claim: {finding}")

    print()
    if failures:
        print("MUTATION TEST FAILED — these guards cannot fail, so they prove nothing:")
        for f in failures:
            print("  " + f)
        return 1

    print(f"All {len(CASES)} guards proven able to go RED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
