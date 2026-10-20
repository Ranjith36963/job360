#!/usr/bin/env python3
"""Test integrity: a PR may not go green by running fewer tests.

WHY THIS EXISTS (2026-10-20)
----------------------------
Coding agents that are judged by a test suite learn to edit the suite: skip the
failing test, delete it, or change the config so it is never collected (METR,
2025-06-05; Anthropic, 2025-11-26). Here only the two fixer workflows guarded
against it (pr-repair.yml and issue-fix.yml check their own agent's diff). An
ordinary Claude Code PR had nothing, so a skipped test merged as green as a fixed one.

This reads `git diff BASE...HEAD` and names three kinds of change:

  SKIP     an ADDED line that switches a test off or hides code from coverage:
           backend/tests: pytest skip / skipif / xfail markers (or a name bound
           to one, like `needs_gpu`), pytest.skip( / xfail( / importorskip(,
           unittest.skip, SkipTest; backend/src: `# pragma: no cover`;
           frontend tests: .skip( / .only( / .todo( / .fixme, xit / xdescribe /
           fit / fdescribe; frontend src: istanbul / v8 / c8 ignore comments.
  DELETE   a test function (`def test_`, `it(` / `test(`) removed with no
           re-add of that name anywhere in the diff and no new, non-trivial test
           in the same file to count as a rename; a deleted test file whose
           tests were not moved elsewhere; a test file renamed or moved to a
           path the test runner does not collect (backend/tests/test_x.py ->
           x_helpers.py, or out of backend/tests).
  CONFIG   any edit to what decides which tests run: conftest.py under backend/,
           the [tool.pytest*] section of a pyproject.toml (backend/ or root),
           pytest.ini / setup.cfg / tox.ini, frontend vitest / playwright
           configs, the "test*" scripts in frontend/package.json, and this file
           once it exists on the base (its baseline is a config).

One skip is NOT a finding: `@needs_chromium` (bound exactly to `skipif(not
_chromium_available(), reason="Playwright + Chromium not installed")`) and that
exact skipif. offline-suite installs Chromium, so it never skips on CI, and the
ratchet below holds its reason at 0 (see CHROMIUM_MARKER).

Not seen (2026-10-20): a gutted assertion or an early `return` inside a test that
still runs, and frontend renames-in-place to a trivial body. The floor test
(backend/tests/test_suite_floor.py) and review are the backstop there.

Any finding exits 1. The `test-integrity` job in ci.yml runs it on every PR and
merge_cage.py requires that check, so a machine cannot merge such a PR; the owner
can read the report and merge by hand.

The second mode is the skip ratchet: `--skips junit.xml` counts the skipped
tests in the offline-suite report and fails if the total exceeds SKIP_BASELINE
or any one reason exceeds its own count, printing every reason that is new or grew.

USAGE
-----
    python scripts/test_integrity.py --base SHA --head SHA     # judge a PR diff
    python scripts/test_integrity.py --skips /tmp/junit.xml   # skip-count ratchet
    python scripts/test_integrity.py --drill                  # each category must go red
    python scripts/test_integrity.py --drill --break-checker SKIP
                                    # NEGATIVE CONTROL: blinds one check; must exit 1
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

SELF = "scripts/test_integrity.py"

# ── The skip ratchet ─────────────────────────────────────────────────────────
# Skipped tests in offline-suite's junit report, by skip message. History: 33
# on CI at 78c20af1, 64 on c0cef280 -- all the growth `@needs_chromium` browser
# tests, which never ran on CI because offline-suite installed only `.[dev]`.
# Since 2026-10-20 offline-suite installs `.[dev,browser]` + Chromium, so those
# run, and their reason ("Playwright + Chromium not installed") has NO allowance:
# one such skip is red. Measured on ci/integrity (origin/main efdfcf6c merged),
# full suite, real Chromium, no numpy, `-n 4`: "4434 passed, 2 skipped".
# The ratchet holds each reason to its own count as well as the total.
# Raising a number here is an edit to this file, which the CONFIG check flags.
SKIP_BASELINE: dict[str, int] = {
    "collection skipped": 1,                        # test_skill_normalizer: numpy
    "owner's real CV is not on this machine": 1,    # test_cv_style
}

# ── Which paths are what ─────────────────────────────────────────────────────
_PY_TEST = re.compile(r"^backend/tests/(?:.*/)?(?:test_[^/]*|[^/]*_test)\.py$")
_PY_TESTS_DIR = re.compile(r"^backend/tests/.*\.py$")
_PY_SRC = re.compile(r"^backend/src/.*\.py$")
_FE_TEST = re.compile(
    r"^frontend/(?:"
    r"(?:.*/)?[^/]+\.(?:test|spec)\.[cm]?[jt]sx?"   # *.test.ts(x), *.spec.ts(x)
    r"|tests/.+\.[cm]?[jt]sx?"                      # frontend/tests/**
    r"|(?:.*/)?e2e/.+\.[cm]?[jt]sx?"                # any e2e/ directory
    r"|(?:.*/)?__tests__/.+\.[cm]?[jt]sx?"
    r")$")
_FE_SRC = re.compile(r"^frontend/(?!node_modules/).*\.[cm]?[jt]sx?$")

# What decides which tests run. Globs over repo-relative paths.
CONFIG_GLOBS = (
    "backend/conftest.py", "backend/**/conftest.py",
    "pytest.ini", "**/pytest.ini", "setup.cfg", "**/setup.cfg", "tox.ini", "**/tox.ini",
    "frontend/vitest.config.*", "frontend/vitest.setup.*", "frontend/vitest.workspace.*",
    "frontend/playwright.config.*",
)
PYPROJECT = "backend/pyproject.toml"
PYPROJECTS = (PYPROJECT, "pyproject.toml")   # pytest reads a root one too
FE_PACKAGE = "frontend/package.json"
# A "test", "test:unit", "test:e2e" ... script line: `vitest run src/none` there
# runs nothing and stays green.
_FE_TEST_SCRIPT = re.compile(r'^\s*"test[\w:.-]*"\s*:')

# ── What an added line must not do ───────────────────────────────────────────
# `mark.skip` with any prefix: `pytest.mark`, `pt.mark` (import pytest as pt),
# bare `mark` (from pytest import mark). `<name>.skip(` covers `pt.skip(`.
_PY_SKIP = re.compile(
    r"\bmark\.(?:skip|skipif|xfail)\b|\b\w+\.(?:skip|xfail|importorskip)\s*\("
    r"|unittest\.skip|\bSkipTest\b|\bskipTest\s*\("
    r"|from\s+pytest\s+import\s+.*\b(?:skip|xfail|importorskip)\b")
# A name bound to a skip marker (`needs_gpu = pytest.mark.skipif(...)`).
_PY_SKIP_ALIAS_DEF = re.compile(r"^\s*(\w+)\s*=\s*(?:\w+\.)?mark\.(?:skip|skipif|xfail)\b")
# THE ONE SKIP CI NOW SATISFIES (2026-10-20). offline-suite installs the browser
# extra and Chromium, so the browser-agent tests' marker never skips there; if
# it ever does, the junit ratchet goes red (this reason's baseline is 0). An
# added `@needs_chromium`, or this exact skipif, is therefore not a finding.
# Narrow on purpose: only this exact condition AND reason, and the alias only
# while every binding of `needs_chromium` in backend/tests is exactly it. Any
# other condition, reason or name, or that name rebound, is still a SKIP.
CHROMIUM_MARKER = "needs_chromium"
_CHROMIUM_SKIPIF = re.compile(
    r"\b(?:\w+\.)?mark\.skipif\(\s*not\s+_chromium_available\(\)\s*,\s*"
    r"reason\s*=\s*([\"'])Playwright \+ Chromium not installed\1\s*\)")
_CHROMIUM_BINDING = re.compile(rf"^\s*{CHROMIUM_MARKER}\s*=\s*{_CHROMIUM_SKIPIF.pattern}\s*$")
# A test body that proves nothing: `pass`, `...`, `assert True`, a docstring.
_TRIVIAL_STMT = re.compile(r"""^(?:pass|\.\.\.|assert\s+(?:True|1|not\s+False)|(?:r?["']).*)?$""")
_PRAGMA = re.compile(r"#\s*pragma:\s*no\s*cover", re.I)
# `if TYPE_CHECKING:  # pragma: no cover` hides imports that never run anyway.
_TYPE_ONLY = re.compile(r"^\s*if\s+(?:typing\.)?TYPE_CHECKING\s*:")
_FE_SKIP = re.compile(
    r"\b(?:describe|it|test|suite|bench|context)(?:\.\w+)*\.(?:skip|only|todo|fixme|skipIf|runIf)\b"
    r"|(?<![.\w])(?:xit|xdescribe|xtest|fit|fdescribe)\s*\(")
_FE_IGNORE = re.compile(r"\b(?:istanbul|v8|c8)\s+ignore\b")

# ── Test names ───────────────────────────────────────────────────────────────
_PY_DEF = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(")
_FE_DEF = re.compile(r"^\s*(?:it|test)(?:\.\w+)*\s*\(\s*(['\"`])(.+?)\1")

# `--break-checker X` blinds one check so the drill can be watched going red.
# TRUST blinds the narrowness of the Chromium exemption (every skip alias trusted).
CHECKS = ("SKIP", "DELETE", "CONFIG", "RATCHET", "TRUST")
_BLIND: set[str] = set()


@dataclass
class FileDiff:
    old: str | None  # None = added file
    new: str | None  # None = deleted file
    added: list[tuple[int, str]] = field(default_factory=list)    # (new line no, text)
    removed: list[tuple[int, str]] = field(default_factory=list)  # (old line no, text)

    @property
    def path(self) -> str:
        return self.new or self.old or "?"


def parse_diff(text: str) -> list[FileDiff]:
    """`git diff --unified=0 -M --no-color` -> one FileDiff per file.

    `---` / `+++` are headers only BEFORE a file's first hunk: inside a hunk an
    added line `++ x` reads `+++ x`, and taking it for a header loses the file.
    """
    files: list[FileDiff] = []
    cur: FileDiff | None = None
    in_hunk = False
    old_no = new_no = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.*) b/(.*)$", line)
            cur = FileDiff(old=m.group(1) if m else None, new=m.group(2) if m else None)
            files.append(cur)
            in_hunk = False
            continue
        if cur is None:
            continue
        if not in_hunk:
            if line.startswith("--- "):
                cur.old = None if line[4:] == "/dev/null" else line[4:].removeprefix("a/")
            elif line.startswith("+++ "):
                cur.new = None if line[4:] == "/dev/null" else line[4:].removeprefix("b/")
            elif line.startswith("rename from "):
                cur.old = line[len("rename from "):]
            elif line.startswith("rename to "):
                cur.new = line[len("rename to "):]
            elif line.startswith("new file mode"):
                cur.old = None
            elif line.startswith("deleted file mode"):
                cur.new = None
        if line.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
            if m:
                old_no, new_no = int(m.group(1)), int(m.group(2))
                in_hunk = True
        elif not in_hunk:
            continue
        elif line.startswith("+"):
            cur.added.append((new_no, line[1:]))
            new_no += 1
        elif line.startswith("-"):
            cur.removed.append((old_no, line[1:]))
            old_no += 1
    return files


def _is_config(path: str) -> bool:
    return any(fnmatch.fnmatchcase(path, g) for g in CONFIG_GLOBS)


def _section_at(text: str, line_no: int) -> str:
    """The TOML/INI `[section]` header in force at 1-based ``line_no``."""
    section = ""
    for i, line in enumerate(text.splitlines(), 1):
        if i > line_no:
            break
        # Column 0 only: an indented `[` is an array element, not a header.
        m = re.match(r"^\[+\s*([^\]\s][^\]]*?)\s*\]+\s*(?:#.*)?$", line)
        if m:
            section = m.group(1)
    return section


def _code(line: str) -> str:
    """The line with a trailing `# comment` cut off (crude, and fine for markers)."""
    return line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""


def judge(files: list[FileDiff], skip_aliases: set[str],
          show: Callable[[str, str], str | None], self_on_base: bool) -> list[str]:
    """Every finding in one diff, as `path:line  CATEGORY  what`.

    ``show(side, path)`` returns a file's text on "base" or "head" (or None);
    it is only asked for a pyproject.toml, to place a change in a section.
    """
    out: list[str] = []
    alias_re = (re.compile(r"(?:^\s*@\s*(?:\w+\.)*(?:%s)\b|\bpytestmark\b.*\b(?:%s)\b|\bmarks\s*=.*\b(?:%s)\b)"
                           % (("|".join(map(re.escape, sorted(skip_aliases))),) * 3))
                if skip_aliases else None)

    # ── SKIP ──────────────────────────────────────────────────────────────────
    if "SKIP" not in _BLIND:
        for f in files:
            if f.new is None:
                continue
            p = f.new
            for no, text in f.added:
                code = _code(text)
                if _PY_TESTS_DIR.match(p):
                    # The ratcheted Chromium skipif is not a skip (see CHROMIUM_MARKER).
                    code = _CHROMIUM_SKIPIF.sub("", code)
                    if _PY_SKIP.search(code):
                        out.append(f"{p}:{no}  SKIP  adds a skip / xfail: {text.strip()[:120]}")
                    elif alias_re and alias_re.search(code):
                        out.append(f"{p}:{no}  SKIP  adds a skip-marker alias: {text.strip()[:120]}")
                elif _PY_SRC.match(p) and _PRAGMA.search(text) and not _TYPE_ONLY.match(text):
                    out.append(f"{p}:{no}  SKIP  hides code from coverage: {text.strip()[:120]}")
                elif _FE_TEST.match(p) and _FE_SKIP.search(text):
                    out.append(f"{p}:{no}  SKIP  adds .skip / .only / .todo / .fixme: {text.strip()[:120]}")
                elif _FE_SRC.match(p) and not _FE_TEST.match(p) and _FE_IGNORE.search(text):
                    out.append(f"{p}:{no}  SKIP  hides code from coverage: {text.strip()[:120]}")

    # ── DELETE ────────────────────────────────────────────────────────────────
    if "DELETE" not in _BLIND:
        def names(f: FileDiff, side: str) -> list[tuple[int, str]]:
            p = (f.new if side == "+" else f.old) or ""
            rx = _PY_DEF if _PY_TEST.match(p) else _FE_DEF if _FE_TEST.match(p) else None
            if rx is None:
                return []
            lines = f.added if side == "+" else f.removed
            got = []
            for no, text in lines:
                m = rx.match(text)
                if m:
                    got.append((no, m.group(m.lastindex or 1)))
            return got

        def trivial(f: FileDiff, def_no: int) -> bool:
            """True when the added python test at ``def_no`` has an added body and
            every statement of it is trivial (`assert True`, `pass`, ...). A def
            line changed alone keeps its old body: a real rename."""
            by_no = dict(f.added)
            head = by_no.get(def_no, "")
            indent = len(head) - len(head.lstrip())
            # `def test_ok(x: int) -> None: pass` -- the body after the signature.
            inline = re.search(r"\)\s*(?:->[^:]*)?:\s*(.*)$", _code(head))
            body = [inline.group(1).strip()] if inline and inline.group(1).strip() else []
            no = def_no + 1
            while no in by_no:
                line = by_no[no]
                if line.strip() and len(line) - len(line.lstrip()) <= indent:
                    break
                if line.strip() and not line.strip().startswith("#"):
                    body.append(line.strip())
                no += 1
            return bool(body) and all(_TRIVIAL_STMT.match(b) for b in body)

        added_anywhere = Counter(n for f in files for _, n in names(f, "+"))
        removed_anywhere = Counter(n for f in files for _, n in names(f, "-"))
        for f in files:
            gone = names(f, "-")
            py_new = bool(f.new and _PY_TEST.match(f.new))
            # Only a test that checks something can stand in for a removed one.
            new_here = [n for no, n in names(f, "+")
                        if removed_anywhere[n] == 0 and not (py_new and trivial(f, no))]
            lost = []
            for no, n in gone:
                if added_anywhere[n] > 0:
                    added_anywhere[n] -= 1   # kept or moved: same name re-added
                    continue
                if new_here:
                    new_here.pop(0)          # renamed in place: one new test for one old
                    continue
                lost.append((no, n))
            for no, n in lost:
                out.append(f"{f.old}:{no}  DELETE  removes test `{n}` with no replacement")
            if f.new is None and f.old and (_PY_TEST.match(f.old) or _FE_TEST.match(f.old)) and gone:
                if lost:
                    out.append(f"{f.old}:1  DELETE  deletes the test file ({len(lost)} of "
                               f"{len(gone)} tests not re-added anywhere)")
            # A pure rename has no removed lines, so the name check above sees
            # nothing; leaving the runner's collection is a deletion all the same.
            for rx in (_PY_TEST, _FE_TEST):
                if f.old and f.new and f.old != f.new and rx.match(f.old) and not rx.match(f.new):
                    out.append(f"{f.old}:1  DELETE  moves the test file to {f.new}, "
                               f"which the test runner does not collect")

    # ── CONFIG ────────────────────────────────────────────────────────────────
    if "CONFIG" not in _BLIND:
        for f in files:
            for p in {x for x in (f.old, f.new) if x}:
                if _is_config(p):
                    out.append(f"{p}:{(f.added or f.removed or [(1, '')])[0][0]}  CONFIG  edits "
                               f"test configuration ({len(f.added)} added, {len(f.removed)} removed lines)")
                    break
                if p == SELF and self_on_base:
                    out.append(f"{p}:{(f.added or f.removed or [(1, '')])[0][0]}  CONFIG  edits the "
                               f"test-integrity guard or its skip baseline")
                    break
            for pp in PYPROJECTS:
                if pp not in (f.old, f.new):
                    continue
                base_text = show("base", pp) or ""
                head_text = show("head", pp) or ""
                hits = [no for no, _ in f.removed if _section_at(base_text, no).startswith("tool.pytest")]
                hits += [no for no, _ in f.added if _section_at(head_text, no).startswith("tool.pytest")]
                if hits:
                    out.append(f"{pp}:{hits[0]}  CONFIG  edits [tool.pytest*] "
                               f"({len(hits)} changed lines)")
            if FE_PACKAGE in (f.old, f.new):
                hits = [no for no, t in f.removed + f.added if _FE_TEST_SCRIPT.match(t)]
                if hits:
                    out.append(f"{FE_PACKAGE}:{hits[0]}  CONFIG  edits a test script "
                               f"({len(hits)} changed lines)")
    return out


# ── git, for the real run ────────────────────────────────────────────────────

def _git(args: list[str]) -> str:
    # Fixed argv, no shell: git with this script's own arguments.
    proc = subprocess.run(["git", "-c", "core.quotepath=false", *args], capture_output=True,  # noqa: S603,S607
                          text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise SystemExit(f"test_integrity: git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def _show(rev: str, path: str) -> str | None:
    proc = subprocess.run(["git", "show", f"{rev}:{path}"], capture_output=True, text=True,  # noqa: S603,S607
                          encoding="utf-8", errors="replace")
    return proc.stdout if proc.returncode == 0 else None


def aliases_in(texts: Sequence[str]) -> set[str]:
    """Names bound to a pytest skip/skipif/xfail marker anywhere in these files,
    less `needs_chromium` while every binding of it is the ratcheted Chromium
    skipif (CHROMIUM_MARKER). One other binding of that name anywhere -- a
    different condition, reason, or a line this cannot read whole -- and it is
    an alias like any other."""
    found: set[str] = set()
    for t in texts:
        for line in t.splitlines():
            m = _PY_SKIP_ALIAS_DEF.match(line)
            if m and m.group(1) != "pytestmark" and not _CHROMIUM_BINDING.match(_code(line)):
                found.add(m.group(1))
    if "TRUST" in _BLIND:   # negative control: every alias trusted
        return set()
    return found


def run_diff(base: str, head: str) -> int:
    merge_base = _git(["merge-base", base, head]).strip()
    text = _git(["diff", "--no-color", "--no-ext-diff", "--unified=0", "-M", f"{base}...{head}"])
    files = parse_diff(text)
    grep = subprocess.run(["git", "grep", "-h", "-E",  # noqa: S603,S607
                           r"^\s*\w+\s*=\s*(\w+\.)?mark\.(skip|skipif|xfail)",
                           head, "--", "backend/tests"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    skip_aliases = aliases_in([grep.stdout])
    sides = {"base": merge_base, "head": head}
    findings = judge(files, skip_aliases, lambda side, p: _show(sides[side], p),
                     self_on_base=_show(merge_base, SELF) is not None)
    print(f"test-integrity: {len(files)} changed files in {base[:12]}...{head[:12]}")
    if not findings:
        print("PASS: no test was skipped, deleted or re-configured.")
        return 0
    print(f"FAIL: {len(findings)} finding(s). A machine may not merge this; the owner can, "
          f"after reading each one.")
    for line in findings:
        print(f"  {line}")
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=test-integrity::{line}")
    return 1


# ── The skip ratchet ─────────────────────────────────────────────────────────

def skip_reasons(junit_text: str) -> Counter[str]:
    """Skip message -> count, from a pytest junit report (xfail counts as a skip)."""
    root = ET.fromstring(junit_text)  # noqa: S314 - pytest's own report from this CI job
    got: Counter[str] = Counter()
    for case in root.iter("testcase"):
        sk = case.find("skipped")
        if sk is not None:
            msg = (sk.get("message") or "").strip()
            # importorskip's message names the module; keep it, it is the reason.
            got[msg or "(no message)"] += 1
    return got


def judge_skips(got: Counter[str], baseline: dict[str, int]) -> tuple[bool, list[str]]:
    total, allowed = sum(got.values()), sum(baseline.values())
    lines = [f"{total} skipped (baseline {allowed})"]
    grew = [(r, n, baseline.get(r, 0)) for r, n in sorted(got.items()) if n > baseline.get(r, 0)]
    for r, n, was in grew:
        lines.append(f"  NEW/GREW  {n - was:+d}  ({was} -> {n})  {r}")
    for r, n in sorted(got.items()):
        lines.append(f"  {n:4d}  {r}")
    # Per reason, not only in total: a new Chromium skip must not hide behind
    # an owner's-CV skip that went away.
    over = (total > allowed or bool(grew)) and "RATCHET" not in _BLIND
    if total < allowed:
        lines.append(f"  {allowed - total} fewer than the baseline: lower SKIP_BASELINE in {SELF}.")
    return (not over), lines


def run_skips(junit: Path) -> int:
    if not junit.is_file():
        print(f"FAIL: no junit report at {junit} -- no report is not a pass.")
        return 1
    ok, lines = judge_skips(skip_reasons(junit.read_text(encoding="utf-8")), SKIP_BASELINE)
    print("skip ratchet: " + lines[0])
    for line in lines[1:]:
        print(line)
    if not ok:
        msg = (f"{lines[0]}. A skipped test is a test that did not run. "
               f"Make it run, or raise SKIP_BASELINE in {SELF} (an owner-read change).")
        print(f"::error title=skip ratchet::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"FAIL: {msg}")
        return 1
    return 0


# ── The drill ────────────────────────────────────────────────────────────────

def _diff(*files: tuple[str | None, str | None, list[str], list[str]]) -> str:
    """A minimal unified=0 diff: (old, new, removed lines, added lines) per file."""
    out = []
    for old, new, rem, add in files:
        out.append(f"diff --git a/{old or new} b/{new or old}")
        if old and new and old != new:
            out += [f"rename from {old}", f"rename to {new}"]
        out.append(f"--- {'a/' + old if old else '/dev/null'}")
        out.append(f"+++ {'b/' + new if new else '/dev/null'}")
        out.append(f"@@ -{1 if rem else 0},{len(rem)} +{1 if add else 0},{len(add)} @@")
        out += [f"-{x}" for x in rem] + [f"+{x}" for x in add]
    return "\n".join(out) + "\n"


_PYPROJECT_BASE = ('[project]\nname = "x"\n\n[tool.pytest.ini_options]\naddopts = "-p no:randomly"\n'
                   '\n[tool.ruff]\nline-length = 120\n')


# The skip-marker bindings already on the head, as in backend/tests today: the
# real Chromium marker, and one arbitrary skipif bound to a name.
_CHROMIUM_LINE = ('needs_chromium = pytest.mark.skipif(not _chromium_available(), '
                  'reason="Playwright + Chromium not installed")')
_HEAD_BINDINGS = _CHROMIUM_LINE + '\nneeds_gpu = pytest.mark.skipif(not _gpu(), reason="no GPU")\n'


def _judge_text(diff: str, self_on_base: bool = True, pyproject_head: str | None = None) -> list[str]:
    texts = {"base": _PYPROJECT_BASE, "head": pyproject_head or _PYPROJECT_BASE}
    files = parse_diff(diff)
    # As run_diff: aliases are read off the whole head, the diff's own added lines included.
    aliases = aliases_in([_HEAD_BINDINGS] + ["\n".join(t for _, t in f.added) for f in files])
    return judge(files, aliases, lambda side, p: texts[side], self_on_base)


def self_drill() -> int:
    T = "backend/tests/test_x.py"
    red_cases = [
        ("SKIP", "pytest.mark.skip decorator", _diff((T, T, [], ["@pytest.mark.skip(reason='flaky')"]))),
        ("SKIP", "skipif marker", _diff((T, T, [], ["@pytest.mark.skipif(True, reason='x')"]))),
        ("SKIP", "xfail marker", _diff((T, T, [], ["@pytest.mark.xfail"]))),
        ("SKIP", "pytest.skip( call", _diff((T, T, [], ["    pytest.skip('later')"]))),
        ("SKIP", "pytest.importorskip(", _diff((T, T, [], ["np = pytest.importorskip('numpy')"]))),
        ("SKIP", "module pytestmark", _diff((T, T, [], ["pytestmark = pytest.mark.skip"]))),
        ("SKIP", "a skip-marker alias on a test", _diff((T, T, [], ["@needs_gpu"]))),
        ("SKIP", "a new marker bound to an arbitrary skipif, then used",
         _diff(("backend/tests/test_n.py", "backend/tests/test_n.py", [],
                ["needs_net = pytest.mark.skipif(True, reason='no network')", "", "@needs_net",
                 "def test_n():", "    assert f()"]))),
        ("SKIP", "needs_chromium rebound to an arbitrary skipif, then used",
         _diff(("backend/tests/test_n.py", "backend/tests/test_n.py", [],
                ['needs_chromium = pytest.mark.skipif(True, reason="Playwright + Chromium not installed")']),
               (T, T, [], ["@needs_chromium"]))),
        ("SKIP", "the Chromium condition with another reason",
         _diff((T, T, [], ['@pytest.mark.skipif(not _chromium_available(), reason="later")']))),
        ("SKIP", "the Chromium skipif with a skip on the same line",
         _diff((T, T, [], ['pytestmark = [pytest.mark.skipif(not _chromium_available(), '
                           'reason="Playwright + Chromium not installed"), pytest.mark.skip]']))),
        ("SKIP", "pragma: no cover in src",
         _diff(("backend/src/a.py", "backend/src/a.py", [], ["    if x:  # pragma: no cover"]))),
        ("SKIP", "frontend it.skip", _diff(("frontend/src/a.test.ts", "frontend/src/a.test.ts", [],
                                            ["  it.skip('renders', () => {"]))),
        ("SKIP", "frontend describe.only", _diff(("frontend/tests/b.spec.tsx", "frontend/tests/b.spec.tsx",
                                                  [], ["describe.only('x', () => {"]))),
        ("SKIP", "playwright test.fixme", _diff(("frontend/tests/e2e/c.spec.ts", "frontend/tests/e2e/c.spec.ts",
                                                 [], ["test.fixme('flaky', async ({ page }) => {"]))),
        ("SKIP", "vitest it.todo", _diff(("frontend/src/d.test.tsx", "frontend/src/d.test.tsx", [],
                                          ["it.todo('later')"]))),
        ("DELETE", "a test function removed", _diff((T, T, ["def test_refund_is_capped():", "    assert f()"], []))),
        ("DELETE", "a test file deleted", _diff((T, None, ["def test_a():", "    pass", "def test_b():"], []))),
        ("DELETE", "a frontend it( removed", _diff(("frontend/src/a.test.ts", "frontend/src/a.test.ts",
                                                    ["  it('shows the error', () => {"], []))),
        ("DELETE", "a test file renamed to a non-test name", _diff((T, "backend/tests/x_helpers.py", [], []))),
        ("DELETE", "a test file moved out of backend/tests", _diff((T, "backend/attic/test_x.py", [], []))),
        ("DELETE", "a frontend test renamed to a non-test name",
         _diff(("frontend/src/a.test.ts", "frontend/src/a.ts", [], []))),
        ("DELETE", "a failing test swapped for `assert True`",
         _diff((T, T, ["def test_refund_is_capped():", "    assert f() == 3"],
                ["def test_ok():", "    assert True"]))),
        ("DELETE", "a failing test swapped for a one-line `pass`",
         _diff((T, T, ["def test_refund_is_capped():", "    assert f() == 3"],
                ["def test_ok(db: int) -> None: pass"]))),
        ("SKIP", "skip through `import pytest as pt`", _diff((T, T, [], ["@pt.mark.skip(reason='later')"]))),
        ("SKIP", "skip through `from pytest import mark`", _diff((T, T, [], ["@mark.skipif(True, reason='x')"]))),
        ("SKIP", "pt.skip( call", _diff((T, T, [], ["    pt.skip('later')"]))),
        ("SKIP", "unittest self.skipTest(", _diff((T, T, [], ["        self.skipTest('later')"]))),
        ("CONFIG", "conftest.py edited", _diff(("backend/tests/conftest.py", "backend/tests/conftest.py",
                                                [], ["def pytest_collection_modifyitems(items): items.clear()"]))),
        ("CONFIG", "[tool.pytest] edited",
         _diff((PYPROJECT, PYPROJECT, [], ['addopts = "-p no:randomly -k not slow"']))),
        ("CONFIG", "pytest.ini added", _diff((None, "backend/pytest.ini", [], ["[pytest]"]))),
        ("CONFIG", "vitest config edited", _diff(("frontend/vitest.config.ts", "frontend/vitest.config.ts",
                                                  [], ["  exclude: ['**/*'],"]))),
        ("CONFIG", "playwright config edited", _diff(("frontend/playwright.config.ts",
                                                      "frontend/playwright.config.ts", [], ["  testIgnore: '**',"]))),
        ("CONFIG", "the guard's own baseline raised", _diff((SELF, SELF, ['    "x": 1,'], ['    "x": 9,']))),
        ("CONFIG", "frontend package.json test script edited",
         _diff((FE_PACKAGE, FE_PACKAGE, ['    "test:unit": "vitest",'], ['    "test:unit": "vitest run src/none",']))),
        ("CONFIG", "a root pyproject [tool.pytest] edited",
         _diff(("pyproject.toml", "pyproject.toml", [], ['addopts = "-p no:randomly -k not slow"']))),
    ]
    # Harmless shapes that must stay green.
    green_cases = [
        ("a new test added", _diff((T, T, [], ["def test_new_behaviour():", "    assert g() == 2"]))),
        ("a new test file", _diff((None, "backend/tests/test_new.py", [], ["def test_one():", "    assert 1"]))),
        ("a test renamed in place", _diff((T, T, ["def test_old_name():"], ["def test_clearer_name():"]))),
        ("a test renamed with its body rewritten",
         _diff((T, T, ["def test_old_name():", "    assert f() == 2"],
                ["def test_clearer_name():", "    assert f() == 2", "    assert g() is None"]))),
        ("`from pytest import mark` used for parametrize",
         _diff((T, T, [], ["from pytest import mark", "@mark.parametrize('n', [1, 2])"]))),
        ("a frontend package.json non-test edit",
         _diff((FE_PACKAGE, FE_PACKAGE, ['    "lint": "eslint",'], ['    "lint": "eslint .",']))),
        ("a test moved to another file",
         _diff((T, T, ["def test_moved():"], []), ("backend/tests/test_y.py", "backend/tests/test_y.py",
                                                   [], ["def test_moved():"]))),
        ("a test file renamed", _diff((T, "backend/tests/test_renamed.py", [], []))),
        ("a src-only change", _diff(("backend/src/a.py", "backend/src/a.py", ["x = 1"], ["x = 2"]))),
        ("a type-only import block marked no cover",
         _diff(("backend/src/a.py", "backend/src/a.py", [], ["if TYPE_CHECKING:  # pragma: no cover"]))),
        ("skip text inside a comment", _diff((T, T, [], ["    # never pytest.skip( here"]))),
        ("[tool.ruff] edited, not [tool.pytest]",
         _diff((PYPROJECT, PYPROJECT, [], ["line-length = 100"]))),
        ("a frontend test added", _diff(("frontend/src/a.test.ts", "frontend/src/a.test.ts", [],
                                         ["  it('works', () => {"]))),
        ("the guard created (not on base yet)", _diff((None, SELF, [], ["x = 1"]))),
        # CI installs Chromium (ci-offline.yml) and the ratchet holds its reason at 0.
        ("a new @needs_chromium browser test",
         _diff((T, T, [], ["@needs_chromium", "async def test_fills_the_form(site):",
                           "    assert (await run(site)).status == 'filled'"]))),
        ("a module marked `pytestmark = needs_chromium`",
         _diff(("backend/tests/test_browser_new.py", "backend/tests/test_browser_new.py", [],
                ["from tests.test_browser_agent import needs_chromium", "pytestmark = needs_chromium"]))),
        ("the exact Chromium skipif as a decorator", _diff((T, T, [], ["@" + _CHROMIUM_LINE.split(" = ", 1)[1]]))),
        ("the needs_chromium binding copied as is", _diff((T, T, [], [_CHROMIUM_LINE]))),
    ]
    misses: list[str] = []
    pyproject_head_ruff = _PYPROJECT_BASE.replace("line-length = 120", "line-length = 100")
    pyproject_head_pytest = _PYPROJECT_BASE.replace('addopts = "-p no:randomly"',
                                                    'addopts = "-p no:randomly -k not slow"')
    for cat, name, diff in red_cases:
        head = pyproject_head_pytest if "[tool.pytest]" in name else None
        # The added line sits where the base had addopts (line 5), so place it there.
        if "[tool.pytest]" in name and diff.count("diff --git") == 1:
            diff = diff.replace("@@ -0,0 +1,1 @@", "@@ -5,0 +5,1 @@")
        got = _judge_text(diff, pyproject_head=head)
        hit = [g for g in got if f"  {cat}  " in g]
        if name == "a test file deleted":   # the file itself must be named, not only its tests
            hit = [g for g in hit if "deletes the test file" in g]
        if "then used" in name:             # the USE must be flagged, not only the binding
            hit = [g for g in hit if "skip-marker alias" in g]
        print(f"  [{'RED ' if hit else 'MISS'}] {cat:6s} {name}" + (f"  -> {hit[0]}" if hit else ""))
        if not hit:
            misses.append(f"{cat}: {name} was not flagged")
    for name, diff in green_cases:
        self_on_base = "not on base" not in name
        head = pyproject_head_ruff if "[tool.ruff]" in name else None
        if "[tool.ruff]" in name:
            diff = diff.replace("@@ -0,0 +1,1 @@", "@@ -8,0 +8,1 @@")
        got = _judge_text(diff, self_on_base=self_on_base, pyproject_head=head)
        print(f"  [{'OK  ' if not got else 'FALSE'}] green  {name}" + (f"  -> {got}" if got else ""))
        if got:
            misses.append(f"harmless `{name}` was flagged: {got}")
    # The ratchet: one skip over the baseline must fail and be named; at the
    # baseline must pass.
    junit = ('<testsuites><testsuite>'
             '<testcase name="a"><skipped message="Playwright + Chromium not installed"/></testcase>'
             '<testcase name="b"><skipped message="Playwright + Chromium not installed"/></testcase>'
             '<testcase name="c"><skipped message="flaky, will fix"/></testcase>'
             '<testcase name="d"/></testsuite></testsuites>')
    got = skip_reasons(junit)
    ok_over, lines_over = judge_skips(got, {"Playwright + Chromium not installed": 2})
    ok_at, _ = judge_skips(got, {"Playwright + Chromium not installed": 2, "flaky, will fix": 1})
    named = any("NEW/GREW" in x and "flaky, will fix" in x for x in lines_over)
    print(f"  [{'RED ' if not ok_over and named else 'MISS'}] RATCHET one skip over the baseline, reason named")
    print(f"  [{'OK  ' if ok_at else 'FALSE'}] green  skips exactly at the baseline")
    if ok_over or not named:
        misses.append("RATCHET: a skip over the baseline passed, or its reason was not printed")
    if not ok_at:
        misses.append("RATCHET: skips at the baseline were refused")
    # Per reason: one skip swapped for another keeps the total and is still red.
    ok_swap, _ = judge_skips(Counter({"flaky, will fix": 1}), {"owner's real CV is not on this machine": 1})
    print(f"  [{'RED ' if not ok_swap else 'MISS'}] RATCHET a skip swapped for a new reason (same total)")
    if ok_swap:
        misses.append("RATCHET: a new skip reason hid behind one that went away")
    # The committed baseline: ONE Chromium skip on CI is red (the browser tests
    # must run there), with every other reason at its baseline.
    chr_reason = "Playwright + Chromium not installed"
    ok_chr, lines_chr = judge_skips(Counter({**SKIP_BASELINE, chr_reason: 1}), SKIP_BASELINE)
    chr_named = any("NEW/GREW" in x and chr_reason in x for x in lines_chr)
    print(f"  [{'RED ' if not ok_chr and chr_named else 'MISS'}] RATCHET one Chromium skip on CI "
          f"(SKIP_BASELINE holds that reason at {SKIP_BASELINE.get(chr_reason, 0)})")
    if ok_chr or not chr_named:
        misses.append("RATCHET: skipped Chromium tests passed the committed SKIP_BASELINE")
    if misses:
        print(f"DRILL FAILED: {len(misses)} case(s) wrong:")
        for m in misses:
            print(f"  {m}")
        return 1
    print(f"DRILL PASS: {len(red_cases) + 3} breaks went red, {len(green_cases) + 1} harmless shapes stayed green.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="base sha (the diff is base...head)")
    ap.add_argument("--head", default="HEAD", help="head sha (default HEAD)")
    ap.add_argument("--skips", type=Path, help="junit report: run the skip-count ratchet")
    ap.add_argument("--drill", action="store_true", help="break each check on purpose")
    ap.add_argument("--break-checker", choices=CHECKS, help="negative control: blind the named check")
    args = ap.parse_args(argv)
    if args.break_checker:
        _BLIND.add(args.break_checker)
    if args.drill:
        return self_drill()
    if args.skips:
        return run_skips(args.skips)
    if not args.base:
        ap.error("--base is required (or --skips / --drill)")
    return run_diff(args.base, args.head)


if __name__ == "__main__":
    sys.exit(main())
