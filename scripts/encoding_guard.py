#!/usr/bin/env python3
"""Text decoded with the machine's locale, instead of a codec we chose.

WHAT THIS CAUGHT, AND WHY IT IS NOT A STYLE RULE
------------------------------------------------
`subprocess.run(..., text=True)` decodes with the LOCALE codec. On the owner's Windows
machine that is cp1252, so an em-dash in a PR title raised UnicodeDecodeError inside the
reader thread. scripts/merge_cage.py -- the file that decides what reaches real users --
CRASHED on 3 of 6 pull requests rather than refusing them.

That distinction is the whole point. Refusing is a defined, safe state. Crashing is
undefined: the caller sees a non-zero exit and cannot tell "this change is unsafe" from
"my decoder died on a dash". A guard that crashes has not failed safe, it has stopped
answering.

The same door exists on `open()` and `Path.read_text()` with no `encoding=`.

WHY A GUARD AND NOT JUST TEN FIXES
-----------------------------------
Ten instances were live across seven files when this was written. Fixing ten and moving
on is what this repo already did with log injection -- SEVEN instances of one bug class
reached production because nothing stopped the second one. A bug class with many copies
and no guard is a bug class that will have more copies.

CI runs on Linux, where the locale is usually UTF-8, so these mostly do NOT fail there.
That is exactly what makes them dangerous: the failure appears only on the owner's
machine, and only for some inputs, which reads as flakiness rather than a bug.
"""

from __future__ import annotations

import argparse
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCAN = ["scripts", "backend/scripts"]

# Baselined by FILE and COUNT, never by line number.
#
# A line-number baseline rots the moment anyone adds an import -- every entry
# shifts, the guard reports 37 "new" findings, and the owner turns it off. What
# cannot drift is "this file has at most N such calls", so that is what is
# recorded. A file not listed here may have ZERO: the debt can shrink, and no new
# file may join it.
BASELINE_PATH = Path(__file__).resolve().parent / "encoding_baseline.txt"


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.is_file():
        return {}
    out: dict[str, int] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        path, _, count = line.rpartition(" ")
        out[path.strip()] = int(count)
    return out


def _has_kw(call: ast.Call, name: str) -> bool:
    return any(k.arg == name for k in call.keywords)


def _kw_is_true(call: ast.Call, name: str) -> bool:
    for k in call.keywords:
        if k.arg == name and isinstance(k.value, ast.Constant) and k.value.value is True:
            return True
    return False


def _call_name(call: ast.Call) -> str:
    """Dotted name of the thing being called, e.g. `subprocess.run` or `open`."""
    node: ast.expr = call.func
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Every call in this file that decodes bytes without saying how."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as exc:
        return [(exc.lineno or 0, f"could not parse: {exc.msg}")]

    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)

        # subprocess.run/check_output/Popen with text=True (or universal_newlines)
        # and no explicit codec.
        if name.split(".")[-1] in {"run", "check_output", "Popen", "check_call"}:
            decodes = _kw_is_true(node, "text") or _kw_is_true(node, "universal_newlines")
            if decodes and not _has_kw(node, "encoding"):
                out.append((node.lineno,
                            f"`{name}(text=True)` decodes with the machine's locale. On "
                            f"Windows that is cp1252 and any non-ASCII byte raises inside "
                            f"the reader thread -- the caller sees a crash, not a verdict. "
                            f"Add encoding=\"utf-8\", errors=\"replace\"."))

        # open(...) / Path.read_text() with no encoding, in read-text mode.
        tail = name.rsplit(".", 1)[-1]
        if name == "open" or tail in {"read_text", "write_text"}:
            if not _has_kw(node, "encoding"):
                # `open(p, "rb")` is bytes and therefore fine.
                binary = any(
                    isinstance(a, ast.Constant) and isinstance(a.value, str) and "b" in a.value
                    for a in node.args[1:2]
                )
                if not binary:
                    out.append((node.lineno,
                                f"`{name}(...)` with no encoding= reads with the machine's "
                                f"locale, so the same file parses differently on two "
                                f"machines. Add encoding=\"utf-8\"."))
    return out


def run(root: Path, extra_skip: set[str] | None = None) -> list[str]:
    findings: list[str] = []
    skip = extra_skip or set()
    for d in SCAN:
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            rel = f.relative_to(root).as_posix()
            if rel in skip:
                continue
            for line, msg in scan_file(f):
                findings.append(f"{rel}:{line}  {msg}")
    return findings


def self_drill() -> int:
    """Break it on purpose. A guard nobody has watched fail cannot be trusted."""
    import tempfile

    print("DRILL - planting locale-decoded reads. The guard must name each one.")
    print("=" * 72)
    cases = [
        ("subprocess text=True with no codec",
         "import subprocess\nsubprocess.run(['ls'], capture_output=True, text=True)\n",
         "locale"),
        ("universal_newlines is the same bug, older spelling",
         "import subprocess\nsubprocess.run(['ls'], universal_newlines=True)\n",
         "locale"),
        ("open() with no encoding",
         "open('x.txt').read()\n",
         "no encoding"),
        ("read_text() with no encoding",
         "from pathlib import Path\nPath('x').read_text()\n",
         "no encoding"),
    ]
    negatives = [
        ("NEGATIVE: text=True WITH a codec",
         "import subprocess\nsubprocess.run(['ls'], text=True, encoding='utf-8')\n"),
        ("NEGATIVE: binary mode needs no codec",
         "open('x.bin', 'rb').read()\n"),
        ("NEGATIVE: read_text WITH a codec",
         "from pathlib import Path\nPath('x').read_text(encoding='utf-8')\n"),
    ]

    results: list[tuple[str, bool, str]] = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "scripts").mkdir()
        for name, src, needle in cases:
            p = tmp / "scripts" / "case.py"
            p.write_text(src, encoding="utf-8")
            hits = run(tmp)
            hit = next((h for h in hits if needle in h), "")
            results.append((name, bool(hit), hit))
        for name, src in negatives:
            p = tmp / "scripts" / "case.py"
            p.write_text(src, encoding="utf-8")
            hits = run(tmp)
            results.append((name, not hits, "" if not hits else f"false alarm: {hits[0][:110]}"))

    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if ok and detail:
            print(f"         CAUGHT -> {detail[:130]}")
        elif not ok and detail:
            print(f"         {detail[:160]}")

    n = sum(1 for _, ok, _ in results if ok)
    print()
    print("=" * 72)
    print(f"DRILL RESULT: {n}/{len(results)} passed")
    if n != len(results):
        print("The guard did not catch something it claims to catch.")
        return 1
    print("Every planted bug was named; every correct form was left alone.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--drill", action="store_true")
    ap.add_argument("--write-baseline", action="store_true",
                    help="record today's debt; never run this to silence a new finding")
    args = ap.parse_args(argv)
    if args.drill:
        return self_drill()

    findings = run(ROOT)
    by_file: dict[str, list[str]] = {}
    for f in findings:
        by_file.setdefault(f.split(":", 1)[0], []).append(f)

    if args.write_baseline:
        lines = ["# Files that decode with the machine's locale, and how many calls each.",
                 "# The count may only go DOWN. A file not listed may have zero.",
                 "# Regenerate deliberately, never to silence a new finding."]
        lines += [f"{k} {len(v)}" for k, v in sorted(by_file.items())]
        BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"baseline written: {len(by_file)} file(s), {len(findings)} call(s)")
        return 0

    base = load_baseline()
    regressions: list[str] = []
    for path, hits in sorted(by_file.items()):
        allowed = base.get(path)
        if allowed is None:
            detail = "\n      ".join(h.split("  ", 1)[1][:110] for h in hits[:2])
            regressions.append(
                f"NEW FILE {path} has {len(hits)} locale-decoded call(s):\n      {detail}")
        elif len(hits) > allowed:
            regressions.append(f"{path} went {allowed} -> {len(hits)}; the count may only fall")

    improved = [f"{p}: {c} -> {len(by_file.get(p, []))}"
                for p, c in sorted(base.items()) if len(by_file.get(p, [])) < c]

    print(f"encoding_guard: {len(findings)} locale-decoded call(s) across "
          f"{len(by_file)} file(s); baseline allows {sum(base.values())}")
    for i in improved:
        print(f"  [BETTER] {i}")

    if regressions:
        print()
        print("REGRESSIONS")
        print("-" * 72)
        for r in regressions:
            print(f"  {r}")
        print()
        print("A guard that crashes has not failed safe -- it has stopped answering.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
