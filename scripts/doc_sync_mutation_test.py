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
#
# Every case that used to plant a lie in a job-registry / rate-limit table /
# ATS-slug / dataclass-shape claim was retired 2026-09-05 (slice 5, #483)
# along with the guard it drilled: those facts no longer exist in the code,
# so a text mutation would have nothing true to compare against. The three
# still here (subfolder-ats, disagree:LOCATIONS in the code-facts sense) went
# with them; `landing-source-count` is now a structural, not textual, drill
# — see `landing_source_count_drill()` below — because there is no live
# registry value left to plant a wrong NUMBER against.
CASES: list[tuple[str, str, str, str]] = [
    ("CLAUDE.md", r"(\d+) workflows in", "999 workflows in", "workflows"),
    ("ARCHITECTURE.md", r"across (\d+) `?test_\*\.py`? files", "across 999 test_*.py files", "test-files"),
    ("frontend/CLAUDE.md", r"Next\.js (\d+\.\d+\.\d+)", "Next.js 1.2.3", "nextjs-version"),
    ("frontend/CLAUDE.md", r"React (\d+\.\d+\.\d+)", "React 4.5.6", "react-version"),
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
    # The `disagree:LOCATIONS` case that used to live here mutated
    # ARCHITECTURE.md's "Only `LOCATIONS` (26) and `VISA_KEYWORDS`" line, part
    # of the sourcing-era keyword-defaults paragraph deleted 2026-09-05 (slice
    # 5, #483) along with `core/keywords.py`'s search-config role. There is no
    # replacement: the disagreement guard drills whatever CONSTANT claim two
    # LIVING docs currently share, and none of the surviving docs states one.
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
    # Retargeted 2026-09-05: runbook.md was archived with the sourcing era
    # (slice 5, #483), so the drill moved to root CLAUDE.md's prod-database
    # row -- on LIVING_DOCS (the phrase sweep reads only that list) and still
    # telling an operator which DB command to run. Third time a drill has
    # caught its own target being removed by someone else's fix. That is the
    # drill working, not failing.
    ("CLAUDE.md",
     r"`(railway run -s Postgres python) <script>`", "`sqlite3 data/jobs.db <script>`",
     "stale-phrase"),
    # Fifth batch, 2026-08-24. Two "N-thing schema" style guards promoted by the
    # nightly routine after ARCHITECTURE.md carried "25-migration forward-compat
    # schema" (real 31) and README.md's source-tree said `ats/ (12)` (real 10).
    # The migration-head guard could not see either — it watches "0000 → NNNN"
    # phrasing only — and no per-subfolder count was ever guarded.
    # RETIRED 2026-08-25 with its guard. The migration count is now produced by
    # gen_doc_blocks.py into ARCHITECTURE.md's code-facts block and the
    # hand-written copies are deleted, so there is no claim left to mutate.
    # The drill said so rather than passing quietly -- the fourth time this
    # design has caught its own target disappearing, and the first time the
    # target disappeared ON PURPOSE.
    #
    # The `subfolder-ats` case that used to live here mutated ARCHITECTURE.md's
    # `ats/ (10)` source-tree line. `backend/src/sources/` and its per-category
    # subfolder counts were deleted 2026-09-05 (slice 5, #483) along with
    # `source_subfolder_counts()` — see structural_drills() below, whose
    # matching cases went with it.
    #
    # Sixth batch, 2026-08-25. The route guard: a documented endpoint that no
    # router declares. Cycle 13 found `POST /api/pipeline/applications` in
    # THREE places (real route: `POST /api/pipeline/{job_id}`, id in the path,
    # no body) -- a doc lie that reads like a contract and 404s whoever trusts
    # it. The mutation renames a REAL route to one that has never existed.
    # Retargeted 2026-09-05 from docs/product/pillars/glossary.md (archived
    # with the sourcing era, slice 5, #483) to a route that survives the
    # slice: OAuth is part of the kept product path.
    ("ARCHITECTURE.md",
     r"`GET (/api/oauth/authorize)`", "`GET /api/oauth/definitely-not-a-route`",
     "route-not-found"),
    # The stamp guard. A KIND outside the five is as unreadable as no stamp at
    # all -- both leave the routine unable to tell a dated record from a live
    # claim, which is why thirteen cycles could never reach zero.
    ("STATUS.md",
     r"<!-- doc: (LIVING)", "<!-- doc: BOGUS", "unstamped-doc"),
    # The ninth-batch `locations` drill (2026-08-25) counted `LOCATIONS` in
    # `core/keywords.py` against the docs' claim. The list, its reader and the
    # guard all went with the sourcing era (slice 5, #483); its lesson stays:
    # a doc-vs-doc check can only find disagreement, never a shared falsehood.
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

            # The subfolder-missing drill that used to open this block
            # (source_subfolder_counts() / EXPECTED_SOURCE_SUBFOLDERS) was
            # retired 2026-09-05 with `backend/src/sources/` itself (slice 5,
            # #483) — there is no folder left to delete out from under it.

            # 1b. A C0 control byte planted in a guard file must be REPORTED.
            #     Text mutation cannot express this: the byte is invisible to
            #     every text instrument. Writing the route guard I produced a
            #     0x08 BACKSPACE where `\b` was intended, which killed the
            #     negation branch outright -- and sed, grep and
            #     inspect.getsource all rendered it as `\b`, agreeing with the
            #     mistake. Only the bytes disagreed, so the drill checks bytes.
            gdir = fake / "scripts"
            gdir.mkdir(parents=True, exist_ok=True)
            (gdir / "doc_sync_check.py").write_bytes(
                b"# planted " + bytes([8]) + b" backspace\n")
            hits = dsc.control_chars_in_guards()
            if not any(h[2] == "0x08" for h in hits):
                failures.append(
                    "control-char: a planted 0x08 went UNREPORTED — a guard whose "
                    "regex can never match is indistinguishable from one that passes"
                )
            (gdir / "doc_sync_check.py").write_bytes(b"# clean\n")
            if dsc.control_chars_in_guards():
                failures.append(
                    "control-char: fired on a clean file — a guard that cries wolf "
                    "gets ignored, which kills the loop"
                )

            # 1c. The line-citation RATCHET must refuse a NEW raw line number.
            #     Not a text mutation: the guard compares against a baseline
            #     file, so the drill has to add a citation to the real tree and
            #     put it back. ~317 exist today, so this guard can never report
            #     them all without becoming noise nobody reads -- it only ever
            #     refuses an INCREASE, and that is the branch worth proving.
            real_status = real_root / "STATUS.md"
            if real_status.exists():
                before = real_status.read_bytes()
                try:
                    dsc.ROOT = real_root
                    real_status.write_bytes(
                        before + b"\n\nSee `backend/src/api/main.py:999` for details.\n")
                    if not dsc.line_citation_regressions():
                        failures.append(
                            "line-citation-ratchet: a NEW raw line number went "
                            "unreported — new prose can keep adding the "
                            "fastest-rotting reference form there is"
                        )
                finally:
                    real_status.write_bytes(before)
                    dsc.ROOT = fake

            # 1d. The SURFACE ratchet must refuse prose GROWTH. This is the one
            #     guard that enforces the deletion contract itself: without it,
            #     "delete, do not reword" is a request in a prompt, and the next
            #     writer grows the haystack back without anyone noticing.
            real_status = real_root / "STATUS.md"
            if real_status.exists() and (real_root / "scripts" / "living_surface_ceiling.txt").exists():
                before = real_status.read_bytes()
                try:
                    dsc.ROOT = real_root
                    dsc._LIVING_STAMPED_CACHE = None
                    pad = "\n" + "\n".join(f"padding {i}" for i in range(40)) + "\n"
                    real_status.write_bytes(before + pad.encode("utf-8"))
                    if not dsc.surface_regression():
                        failures.append(
                            "surface-ratchet: 40 added lines of LIVING prose went "
                            "unreported — the haystack can grow back and the "
                            "deletion contract is unenforced"
                        )
                finally:
                    real_status.write_bytes(before)
                    dsc._LIVING_STAMPED_CACHE = None
                    dsc.ROOT = fake

            # 1e. A directory-tree entry naming a file that is GONE must be
            #     reported. Not a text mutation on a fake root: the guard
            #     resolves against the real repo, so the drill renames one real
            #     entry (backend/src/cli.py) and restores it.
            real_arch = real_root / "ARCHITECTURE.md"
            if real_arch.exists():
                before = real_arch.read_bytes()
                try:
                    dsc.ROOT = real_root
                    dsc._LIVING_STAMPED_CACHE = None
                    real_arch.write_bytes(before.replace(b"cli.py", b"cli_GONE.py", 1))
                    if not any("cli_GONE" in row[2] for row in dsc.doc_tree_dead_paths()):
                        failures.append(
                            "tree-dead-path: a tree entry naming a missing file went "
                            "unreported — a tree can keep sending readers to paths "
                            "that are not there"
                        )
                finally:
                    real_arch.write_bytes(before)
                    dsc._LIVING_STAMPED_CACHE = None
                    dsc.ROOT = fake

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

    # The GENERATED-guard drill that used to run here (force `ats` to 0,
    # require build_checks() to still emit `subfolder-ats`) was retired
    # 2026-09-05 with `source_subfolder_counts()` itself (slice 5, #483).

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
              "gapped/malformed/duplicate migrations, unwatched pillar doc, "
              "control byte in a guard, line-citation ratchet, surface ratchet, dead tree path")
    return failures


def landing_source_count_drill() -> list[str]:
    """Prove `landing-source-count` still fires — as a PRESENCE guard now.

    Before slice 5 (#483) this was an ordinary CASE: mutate a `SOURCE_COUNT`
    digit in `frontend/src/lib/catalog.ts` (deleted with the sourcing era) and
    require the number to disagree with the registry. There is no registry
    left to disagree with, so `landing_page_source_claims()` in
    doc_sync_check.py changed shape: it now fires on ANY "N source(s)" claim
    at all, which a text-substitution CASE cannot express (there is nothing
    truthful already in the file to substitute over — the frontend's own
    regression test, `landing-sources-count.test.tsx`, keeps that copy at
    zero mentions on purpose). So this drill INSERTS a claim instead.
    """
    target = ROOT / "frontend" / "src" / "app" / "page.tsx"
    if not target.exists():
        return ["landing-source-count: frontend/src/app/page.tsx does not exist — cannot drill"]

    before = target.read_bytes()
    try:
        # A CODE line, not a `//` comment: the guard skips comment lines on
        # purpose (prose explaining the old 47-vs-41 bug may say 47), so a
        # planted comment proves nothing. The first draft planted exactly that
        # and reported the guard blind when it was the drill that was.
        target.write_bytes(before + b'\nconst drillCopy = "47 job sources";\n')
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "doc_sync_check.py")],
            capture_output=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
        )
        if "landing-source-count" in (proc.stdout or ""):
            print("PASS  landing-source-count went RED when the landing page claimed a source count")
            return []
        return ["landing-source-count: stayed GREEN on a page claiming '47 job sources' — the guard is blind"]
    finally:
        target.write_bytes(before)


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

    # landing-source-count is now a PRESENCE guard (see docstring) — an
    # insertion drill, not a CASE substitution.
    failures.extend(landing_source_count_drill())

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
