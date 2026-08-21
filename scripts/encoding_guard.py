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

WHY A GUARD AND NOT JUST FORTY-TWO FIXES
----------------------------------------
The baseline shipped beside this file (scripts/encoding_baseline.txt) lists 18 files and
36 calls, and this PR fixes 6 more in backend/scripts/mypy_ratchet.py,
scripts/checker_scorecard.py, scripts/doc_clutter_check.py, scripts/watchdog_check.py and
scripts/chain_check.py. So the real number is about 42, not the ten this paragraph used
to claim -- a figure that contradicted the baseline committed alongside it. Corrected
because the argument gets STRONGER with the true number: fixing forty-two by hand and
moving on is what this repo already did with log injection, where SEVEN instances of one
bug class reached production because nothing stopped the second one. A bug class with
many copies and no guard is a bug class that will have more copies. (CodeRabbit, PR #336.)

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


def _has_real_encoding(node: "ast.Call") -> bool:
    """Is there an `encoding=` that actually names a codec?

    `_has_kw` answered "is the keyword present", which is a different question:
    `encoding=None` is present AND is the locale default, so the very bug this
    guard hunts could be written out in full and pass. Anything that is not a
    literal None counts -- a variable or a call cannot be judged statically, and
    guessing in the strict direction would fire on correct code.
    """
    for k in node.keywords:
        if k.arg == "encoding":
            return not (isinstance(k.value, ast.Constant) and k.value.value is None)
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
            # `encoding=None` IS the locale default -- it is the exact bug this
            # guard exists for, written explicitly. Accepting any `encoding=`
            # keyword let it through silently. (CodeRabbit, PR #336.)
            if decodes and not _has_real_encoding(node):
                out.append((node.lineno,
                            f"`{name}(text=True)` decodes with the machine's locale. On "
                            f"Windows that is cp1252 and any non-ASCII byte raises inside "
                            f"the reader thread -- the caller sees a crash, not a verdict. "
                            f"Add encoding=\"utf-8\", errors=\"replace\"."))

        # open(...) / Path.read_text() with no encoding, in read-text mode.
        tail = name.rsplit(".", 1)[-1]
        if name == "open" or tail in {"read_text", "write_text"}:
            if not _has_real_encoding(node):
                # `open(p, "rb")` is bytes and therefore fine -- and so is
                # `open(p, mode="rb")`. Only positional args were inspected, so
                # the keyword spelling read as TEXT mode, produced a finding, and
                # the baseline turned that into a NEW FILE regression on correct
                # code. A detector that goes red on correct code gets switched
                # off, which costs more than the bug it was chasing.
                # (CodeRabbit, PR #336.)
                mode_kw = next((k.value for k in node.keywords if k.arg == "mode"), None)
                binary = any(
                    isinstance(a, ast.Constant) and isinstance(a.value, str) and "b" in a.value
                    for a in node.args[1:2]
                ) or (isinstance(mode_kw, ast.Constant)
                      and isinstance(mode_kw.value, str) and "b" in mode_kw.value)
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


def compare_to_baseline(by_file: dict[str, list[str]],
                        base: dict[str, int]) -> list[str]:
    """The half of this guard that can actually FAIL CI. Pure, so it is drillable.

    It used to live inline in main(). The drill therefore only ever called run()
    and inspected findings, while the two branches that return 1 -- NEW FILE and
    the count regression -- were never exercised. If load_baseline() mis-parsed a
    line, or these keys stopped matching baseline keys (by_file splits on the
    first ":", the baseline stores bare paths), the guard would print
    "0 regressions" and CI would stay green for the wrong reason.

    Extracted so the drill tests THIS rather than a re-implementation of it: a
    self-test that recomputes the comparison it is checking proves only that it
    agrees with itself. (CodeRabbit, PR #336.)
    """
    regressions: list[str] = []
    for path, hits in sorted(by_file.items()):
        allowed = base.get(path)
        if allowed is None:
            detail = "\n      ".join(h.split("  ", 1)[1][:110] for h in hits[:2])
            regressions.append(
                f"NEW FILE {path} has {len(hits)} locale-decoded call(s):\n      {detail}")
        elif len(hits) > allowed:
            regressions.append(f"{path} went {allowed} -> {len(hits)}; the count may only fall")
    return regressions


def self_drill() -> int:
    """Break it on purpose. A guard nobody has watched fail cannot be trusted."""
    import tempfile

    print("DRILL - planting locale-decoded reads. The guard must name each one.")
    print("=" * 72)
    cases = [
        # THE TWO GAPS CodeRabbit FOUND ON PR #336, one in each direction.
        ("encoding=None is the locale default written out in full",
         "import subprocess\nsubprocess.run(['ls'], text=True, encoding=None)\n",
         "locale"),
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
        # A DETECTOR THAT FIRES ON CORRECT CODE GETS SWITCHED OFF, so the
        # absence of a finding is drilled as hard as its presence. Only
        # POSITIONAL args were inspected, so the keyword spelling read as text
        # mode and the baseline turned it into a NEW FILE regression on code
        # that was already right. (CodeRabbit, PR #336.)
        ("NEGATIVE: keyword mode='rb' is binary, not a finding",
         "data = open('x', mode='rb').read()\n"),
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

    # THE BRANCH THAT ACTUALLY FAILS CI, DRILLED AT LAST. Everything above
    # calls run() and reads findings -- but run() never returns an exit code.
    # What turns this guard red is compare_to_baseline(), and until now nothing
    # exercised it. A mis-parsed baseline line, or by_file keys drifting away
    # from baseline keys (by_file splits on the first ":", the baseline stores
    # bare paths), would have printed "0 regressions" and gone green for the
    # wrong reason. Both failing branches are now driven directly, against a
    # baseline built in memory so no file on disk is touched.
    # (CodeRabbit, PR #336.)
    _base = {"scripts/known.py": 2}
    _hit = "scripts/known.py:10  subprocess.run(x, text=True)"

    results.append((
        "a baselined file that GAINS a call is a regression",
        bool(compare_to_baseline({"scripts/known.py": [_hit, _hit, _hit]}, _base)),
        "2 -> 3 was accepted; the ratchet does not ratchet"))

    results.append((
        "an UNLISTED file with a call is a NEW FILE regression",
        any("NEW FILE" in r for r in
            compare_to_baseline({"scripts/ghost.py": [_hit]}, _base)),
        "an unbaselined file slipped through as if it were allowed"))

    results.append((
        "a baselined file at or BELOW its count is not a regression",
        not compare_to_baseline({"scripts/known.py": [_hit, _hit]}, _base),
        "a file that held its count was reported as a regression -- and a guard "
        "that goes red on correct code gets switched off"))

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
    regressions = compare_to_baseline(by_file, base)

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
