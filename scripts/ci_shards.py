#!/usr/bin/env python3
"""OFFLINE-SUITE SHARDS — split the backend tests across parallel CI jobs, and
prove afterwards that every test ran in exactly one of them.

ci-offline.yml runs the backend suite as N matrix jobs (`offline-suite (shard
k/N)`), then one job named exactly `offline-suite` (the required check) joins
them. A split that drops a file is a test that silently stops running, so the
split is checked twice: here, before any test runs, and in the joining job,
across the shards' own manifests and their merged junit report.

  split        run in each shard (from backend/): reads `pytest --collect-only -q`
               output, takes every test file pytest would consider (`tests/**/
               test_*.py`, `*_test.py`, plus whatever was collected), assigns
               files to shards by collected-test count (largest first, to the
               lightest shard, ties by name: deterministic), proves the whole
               partition is disjoint and complete, writes this shard's file list
               and a JSON manifest. A file that collects nothing (a module-level
               skip) is still assigned, so its skip still reaches the report.
  verify       run in the joining job: every manifest saw the same collection
               and the same file universe; the shards' file lists are disjoint,
               none is empty, and their union is the universe; every collected
               test id is in the merged junit report.
  merge-junit  joins the shards' junit files into one <testsuites> report, for
               the skip ratchet (scripts/test_integrity.py --skips) and the
               lifecycle verdict.

Exit 0 = ok · 1 = a check failed (each finding printed as ::error).
Usage:
  python scripts/ci_shards.py split --collected F --shards N --shard K --files-out F --manifest-out F
  python scripts/ci_shards.py verify --junit merged.xml MANIFEST...
  python scripts/ci_shards.py merge-junit OUT IN...
  python scripts/ci_shards.py --drill [--break-checker]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET  # noqa: S405 - pytest's own junit reports from this CI run
from collections import Counter
from pathlib import Path
from typing import Any

_PY = re.compile(r"\.py$")


def parse_collection(text: str) -> list[str]:
    """Test node ids from `pytest --collect-only -q` output, in order, once each.
    Only the leading id block counts: a later `warnings summary` section names
    node ids again, with text after them."""
    ids: list[str] = []
    for ln in text.splitlines():
        s = ln.strip()
        if "::" in s and s.split("::", 1)[0].endswith(".py"):
            ids.append(s)
        elif ids and (not s or s.startswith("=")):
            break
    return list(dict.fromkeys(ids))


def universe_of(tests_dir: Path, base: Path, node_ids: list[str]) -> list[str]:
    """Every test file pytest would consider: its default `python_files` globs
    under the tests dir, plus every file that actually collected something."""
    found = {p.relative_to(base).as_posix() for pat in ("test_*.py", "*_test.py")
             for p in tests_dir.rglob(pat)}
    return sorted(found | {n.split("::", 1)[0] for n in node_ids})


def partition(universe: list[str], node_ids: list[str], shards: int) -> list[list[str]]:
    """Greedy largest-first by collected-test count; a file with none weighs 1."""
    weight = Counter(n.split("::", 1)[0] for n in node_ids)
    loads = [0] * shards
    out: list[list[str]] = [[] for _ in range(shards)]
    for f in sorted(universe, key=lambda f: (-max(weight[f], 1), f)):
        k = min(range(shards), key=lambda i: (loads[i], i))
        out[k].append(f)
        loads[k] += max(weight[f], 1)
    return [sorted(s) for s in out]


def partition_problems(universe: list[str], parts: list[list[str]], blind: bool = False) -> list[str]:
    """Disjoint, none empty, union == universe. Empty list = sound."""
    if blind:  # --break-checker: a guard that never sees a bad split
        return []
    probs: list[str] = []
    seen: Counter[str] = Counter(f for p in parts for f in p)
    dup = sorted(f for f, c in seen.items() if c > 1)
    if dup:
        probs.append(f"{len(dup)} test file(s) in more than one shard: {', '.join(dup[:10])}")
    missing = sorted(set(universe) - set(seen))
    if missing:
        probs.append(f"{len(missing)} test file(s) in no shard (would never run): {', '.join(missing[:10])}")
    extra = sorted(set(seen) - set(universe))
    if extra:
        probs.append(f"{len(extra)} shard file(s) not in the collection: {', '.join(extra[:10])}")
    for i, p in enumerate(parts, 1):
        if not p:
            # An EMPTY file list makes `pytest` run with no paths = the whole suite.
            probs.append(f"shard {i} is empty")
    return probs


def junit_ids(xml_text: str) -> set[str]:
    """(classname, name) of every testcase, as pytest's junitxml writes them."""
    root = ET.fromstring(xml_text)  # noqa: S314 - see import
    return {f"{c.get('classname', '')}::{c.get('name', '')}" for c in root.iter("testcase")}


def junit_id_of(node_id: str) -> str:
    """A node id as pytest's junitxml names it (`mangle_test_address`)."""
    path, bracket, params = node_id.partition("[")
    names = path.split("::")
    names[0] = _PY.sub("", names[0].replace("/", "."))
    names[-1] += bracket + params
    return f"{'.'.join(names[:-1])}::{names[-1]}"


def verify(manifests: list[dict[str, Any]], junit_text: str | None, blind: bool = False) -> list[str]:
    if blind:
        return []
    if not manifests:
        return ["no shard manifests were found"]
    probs: list[str] = []
    shards = {m["shards"] for m in manifests}
    if len(shards) != 1:
        probs.append(f"manifests disagree on the shard count: {sorted(shards)}")
    n = manifests[0]["shards"]
    got = sorted(m["shard"] for m in manifests)
    if got != list(range(1, n + 1)):
        probs.append(f"expected manifests for shards 1..{n}, got {got}")
    if any(m["collected"] != manifests[0]["collected"] for m in manifests):
        probs.append("the shards collected different tests from the same tree")
    if any(m["universe"] != manifests[0]["universe"] for m in manifests):
        probs.append("the shards saw different test-file universes")
    probs += partition_problems(manifests[0]["universe"], [m["files"] for m in manifests])
    if junit_text is None:
        probs.append("no merged junit report")
    else:
        reported = junit_ids(junit_text)
        lost = [n for n in manifests[0]["collected"] if junit_id_of(n) not in reported]
        if lost:
            probs.append(f"{len(lost)} collected test(s) missing from the merged report: {', '.join(lost[:10])}")
    return probs


def merge_junit(inputs: list[str]) -> str:
    root = ET.Element("testsuites")
    for text in inputs:
        r = ET.fromstring(text)  # noqa: S314 - see import
        suites = [r] if r.tag == "testsuite" else list(r.iter("testsuite"))
        root.extend(suites)
    return ET.tostring(root, encoding="unicode")


def _cmd_split(a: argparse.Namespace) -> int:
    nodes = parse_collection(Path(a.collected).read_text(encoding="utf-8"))
    if not nodes:
        print("::error title=shards::the collection is empty; refusing to split nothing")
        return 1
    tests_dir = Path(a.tests_dir)
    uni = universe_of(tests_dir, tests_dir.parent, nodes)
    parts = partition(uni, nodes, a.shards)
    probs = partition_problems(uni, parts)
    for p in probs:
        print(f"::error title=shards::{p}")
    if probs:
        return 1
    mine = parts[a.shard - 1]
    Path(a.files_out).write_text("".join(f"{f}\n" for f in mine), encoding="utf-8")
    Path(a.manifest_out).write_text(json.dumps({
        "shard": a.shard, "shards": a.shards, "files": mine,
        "universe": uni, "collected": nodes}), encoding="utf-8")
    weight = Counter(n.split("::", 1)[0] for n in nodes)
    for i, part in enumerate(parts, 1):
        print(f"shard {i}/{a.shards}: {len(part)} files, {sum(weight[f] for f in part)} tests"
              + ("  <- this job" if i == a.shard else ""))
    print(f"partition sound: {len(uni)} files, {len(nodes)} tests, every file in exactly one shard")
    return 0


def _cmd_verify(a: argparse.Namespace) -> int:
    manifests = [json.loads(Path(p).read_text(encoding="utf-8")) for p in a.manifests]
    junit = Path(a.junit).read_text(encoding="utf-8") if Path(a.junit).is_file() else None
    probs = verify(manifests, junit)
    for p in probs:
        print(f"::error title=shards::{p}")
    if probs:
        return 1
    m = manifests[0]
    print(f"shards sound: {len(manifests)} shards, {len(m['universe'])} files, "
          f"{len(m['collected'])} collected tests, every one in the merged report")
    return 0


def _cmd_merge(a: argparse.Namespace) -> int:
    texts = [Path(p).read_text(encoding="utf-8") for p in a.inputs]
    Path(a.out).write_text(merge_junit(texts), encoding="utf-8")
    print(f"merged {len(texts)} junit report(s) into {a.out}")
    return 0


def drill(blind: bool) -> int:
    """Plant every bad split the guard exists for and demand each is named."""
    nodes = ["tests/test_a.py::t1", "tests/test_a.py::t2", "tests/test_a.py::t3",
             "tests/test_b.py::C::t[x-1]", "tests/test_b.py::t2", "tests/test_c.py::t1",
             "tests/sub/test_d.py::t1"]
    uni = sorted({n.split("::", 1)[0] for n in nodes} | {"tests/test_skipped_module.py"})
    parts = partition(uni, nodes, 2)

    def man(k: int, files: list[str], coll: list[str] = nodes) -> dict[str, Any]:
        return {"shard": k, "shards": 2, "files": files, "universe": uni, "collected": coll}

    def suite(ids: list[str]) -> str:
        cases = "".join(f'<testcase classname="{junit_id_of(n).split("::")[0]}" '
                        f'name="{junit_id_of(n).split("::", 1)[1]}"/>' for n in ids)
        return f"<testsuites><testsuite>{cases}</testsuite></testsuites>"

    full = merge_junit([suite(nodes[:3]), suite(nodes[3:])])
    good = [man(1, parts[0]), man(2, parts[1])]
    checks = [
        ("a sound split passes", partition_problems(uni, parts, blind) == [] and verify(good, full, blind) == []),
        ("the split is deterministic", partition(list(reversed(uni)), nodes, 2) == parts),
        ("a zero-test file (module skip) still lands in a shard",
         any("tests/test_skipped_module.py" in p for p in parts)),
        ("merge-junit keeps every testcase", len(junit_ids(full)) == len(nodes)),
        ("collection parsing takes the id block only, not the warnings summary",
         parse_collection("tests/test_a.py::t1\ntests/test_a.py::t1\ntests/test_b.py::t[a b]\n\n"
                          "=== warnings summary ===\ntests/test_c.py::t9\n  x.py:1: Warning\n\n"
                          "3 tests collected in 0.1s\n")
         == ["tests/test_a.py::t1", "tests/test_b.py::t[a b]"]),
        ("a file in BOTH shards is named",
         bool(verify([man(1, parts[0] + [parts[1][0]]), man(2, parts[1])], full, blind))),
        ("a file in NO shard is named", bool(verify([man(1, parts[0]), man(2, parts[1][1:])], full, blind))),
        ("an empty shard is named", bool(partition_problems(uni, [uni, []], blind))),
        ("a missing shard manifest is named", bool(verify([good[0]], full, blind))),
        ("shards that collected differently are named",
         bool(verify([good[0], man(2, parts[1], nodes[:-1])], full, blind))),
        ("a collected test absent from the report is named",
         bool(verify(good, suite(nodes[1:]), blind))),
        ("no report at all is named", bool(verify(good, None, blind))),
    ]
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    return 0 if all(ok for _, ok in checks) else 1


def main(argv: list[str]) -> int:
    if "--drill" in argv:
        return drill(blind="--break-checker" in argv)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("--collected", required=True)
    s.add_argument("--tests-dir", default="tests")
    s.add_argument("--shards", type=int, required=True)
    s.add_argument("--shard", type=int, required=True)
    s.add_argument("--files-out", required=True)
    s.add_argument("--manifest-out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--junit", required=True)
    v.add_argument("manifests", nargs="*")
    m = sub.add_parser("merge-junit")
    m.add_argument("out")
    m.add_argument("inputs", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "split":
        if not 1 <= a.shard <= a.shards:
            ap.error("--shard must be in 1..--shards")
        return _cmd_split(a)
    if a.cmd == "verify":
        return _cmd_verify(a)
    return _cmd_merge(a)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
