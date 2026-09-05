"""Write the countable facts INTO the docs from code. Generation, not detection.

The doc-truth loop spent seventeen nightly runs detecting drift, and grew 26
guards and ~2,260 lines of checker to do it. Every one of those guards watches
a fact that is DERIVABLE: the registry size, the rate-limit count, the
migration head, how many entries LOCATIONS holds. A guard makes drift VISIBLE.
A generator makes it IMPOSSIBLE -- and costs nothing to maintain afterwards,
because there is no second copy to keep in step.

That is the difference between a smoke alarm and not keeping petrol in the
hallway. This file is the second thing.

HOW IT WORKS
    A doc marks a region:

        <!-- generated: code-facts -->
        ...anything here is overwritten...
        <!-- /generated -->

    `python scripts/gen_doc_blocks.py --write` fills every marked region from
    the code. `--check` (what CI runs) regenerates in memory and fails if the
    file on disk differs, so a stale block cannot survive a push.

WHY --check RATHER THAN JUST TRUSTING --write
    Nobody remembers to run a generator. The check is the enforcement; the
    writer is the convenience. This mirrors gen-api-types.sh, which the gate
    already runs the same way.

ADDING A FACT
    Add a producer to BLOCKS. Prefer deleting the hand-written sentence that
    stated the same fact -- a generated block beside a prose restatement is two
    copies again, which is the problem this file exists to end.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _checker():
    """The extractors already live in doc_sync_check.py — reuse, do not re-derive.

    Two extractors for one fact is exactly the duplication being removed. If
    they ever disagreed, the guard and the generated text would fight forever.
    """
    spec = importlib.util.spec_from_file_location(
        "doc_sync_check", ROOT / "scripts" / "doc_sync_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def code_facts() -> str:
    """Countable facts about what remains after slice 5 (#483).

    Every fact this block used to carry about the sourcing era — the job
    registry, its unique classes, the rate-limit table, the location keyword
    list, the scorer version, the source-class count, the ATS slug catalog,
    the enrichment schema — was retired 2026-09-05 with the code that made it
    true. What is left is generic repo shape, none of it sourcing-specific.
    """
    c = _checker()
    rows = [
        "| Fact | Value | Where the code says it |",
        "| --- | --- | --- |",
        f"| Migration head | **{c.migration_head():04d}** | `backend/migrations/` |",
        f"| Migration files | **{c.migration_file_count()}** | `backend/migrations/*.up.sql` |",
        f"| `test_*.py` files | **{c.test_file_count()}** | `backend/tests/` |",
        f"| GitHub Actions workflows | **{c.workflow_count()}** | `.github/workflows/` |",
        f"| Hard rules | **{c.hard_rule_count()}** | `.claude/skills/hard-rules/SKILL.md` |",
    ]
    return "\n".join(rows)


def api_routes() -> str:
    """Every route FastAPI actually declares, grouped by router file.

    The most expensive doc lie in this repo has twice been a route. A wrong
    number is a bad fact; a wrong endpoint reads like a CONTRACT, and whoever
    trusts it gets a 404. `POST /api/pipeline/applications` was documented in
    three places and never existed. `.claude/skills/health/SKILL.md` told an
    agent to call `GET /api/me` nightly — it would 404 and report a healthy
    system as broken.

    A path is assembled in THREE places, and reading fewer than three is how
    the guard for this was wrong on its first attempt: APIRouter(prefix=...),
    the decorator, and include_router(prefix="/api"). All three here.
    """
    routes_dir = ROOT / "backend/src/api/routes"
    if not routes_dir.exists():
        return "_(no routes directory)_"

    decl = re.compile(r"@router\.(get|post|put|patch|delete)\(\s*[\"']([^\"']*)")
    prefix_re = re.compile(r"APIRouter\(\s*prefix=[\"']([^\"']+)")

    # The mount prefix is whatever main.py passes to include_router — "/api"
    # for almost every router, but the OAuth discovery documents
    # (`well_known`) are mounted at the site ROOT, because a client resolves
    # `/.well-known/...` against the bare origin. Read it, don't assume it.
    mount_re = re.compile(
        r"include_router\(\s*(\w+)\.router\s*(?:,\s*prefix=[\"']([^\"']*)[\"'])?"
    )
    main_body = (ROOT / "backend/src/api/main.py").read_text(encoding="utf-8", errors="replace")
    mounts = {m.group(1): (m.group(2) or "") for m in mount_re.finditer(main_body)}

    lines = ["| Method | Path | Router |", "| --- | --- | --- |"]
    total = 0
    for path in sorted(routes_dir.glob("*.py")):
        body = path.read_text(encoding="utf-8", errors="replace")
        pm = prefix_re.search(body)
        prefix = pm.group(1).rstrip("/") if pm else ""
        mount = mounts.get(path.stem, "/api")
        found = []
        for m in decl.finditer(body):
            full = f"{mount}{prefix}{m.group(2)}".rstrip("/") or mount or "/"
            found.append((m.group(1).upper(), full))
        for method, full in sorted(found, key=lambda r: (r[1], r[0])):
            lines.append(f"| `{method}` | `{full}` | `{path.name}` |")
            total += 1
    lines.append("")
    lines.append(f"**{total} routes.** Generated from the routers; a path is "
                 f"assembled from `APIRouter(prefix=…)` + the decorator + the "
                 f"`include_router(prefix=…)` in `main.py` (`/api` for all but "
                 f"the root-mounted `/.well-known/*` discovery documents).")
    return "\n".join(lines)


# doc -> {block name: producer}
BLOCKS: dict[str, dict[str, callable]] = {
    "ARCHITECTURE.md": {"code-facts": code_facts, "api-routes": api_routes},
}

OPEN = "<!-- generated: {name} -->"
CLOSE = "<!-- /generated -->"


def _render(text: str, name: str, body: str) -> tuple[str, bool]:
    open_tag = OPEN.format(name=name)
    pat = re.compile(
        re.escape(open_tag) + r".*?" + re.escape(CLOSE), re.S)
    if not pat.search(text):
        return text, False
    new = f"{open_tag}\n<!-- Generated by scripts/gen_doc_blocks.py — do not edit by hand. -->\n\n{body}\n{CLOSE}"
    return pat.sub(lambda _: new, text, count=1), True


def main() -> int:
    write = "--write" in sys.argv
    stale: list[str] = []
    missing: list[str] = []

    for rel, blocks in BLOCKS.items():
        path = ROOT / rel
        if not path.exists():
            missing.append(f"{rel}: file not found")
            continue
        original = path.read_text(encoding="utf-8", errors="replace")
        text = original
        for name, produce in blocks.items():
            text, found = _render(text, name, produce())
            if not found:
                # Loud, not silent: a generator whose marker vanished is a
                # block that has quietly gone back to being hand-written.
                missing.append(f"{rel}: no '{name}' block — marker deleted?")
        if text != original:
            if write:
                path.write_text(text, encoding="utf-8")
                print(f"wrote {rel}")
            else:
                stale.append(rel)

    if missing:
        print("MISSING MARKERS")
        for m in missing:
            print(f"  {m}")
    if stale:
        print("STALE GENERATED BLOCKS — run: python scripts/gen_doc_blocks.py --write")
        for s in stale:
            print(f"  {s}")
    if missing or stale:
        return 1
    print("generated doc blocks are current" if not write else "done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
