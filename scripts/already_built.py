#!/usr/bin/env python3
"""Look before you write: does this already exist in the codebase?

HOLE #4. An LLM cannot answer "is this already built?" — not because it is
badly prompted, but because of physics: the codebase does not fit in its
context window. Measured (dupehound, 2026): a deterministic scanner found
36/39 planted duplicate functions in 3.3M lines; Claude Opus asked the same
question at 1M lines found 0/39.

So dumb code answers it and hands the answer to the agent as a file. Same
philosophy as the independent test re-run: the workflow does what the model
structurally cannot, and the model gets the result as evidence.

This is deliberately a SEARCH, not a judge. It prints candidates and says
"these already exist, check before writing a new one" — deciding whether a
candidate is really the same thing is exactly the judgement the agent is for.

Usage:
    python scripts/already_built.py "add retry to the reed source" [--root .]
    python scripts/already_built.py --from-file agent-issue.md > already-built.md

Exit code is always 0: an empty result is a finding ("nothing similar exists"),
not an error, and this must never fail a repair run.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

# Words that carry no signal about WHAT is being built. Kept deliberately short:
# an over-eager stoplist silently drops the one word that would have matched.
_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "to", "of", "in", "on", "for",
    "with", "is", "are", "was", "were", "be", "been", "it", "this", "that",
    "add", "fix", "make", "need", "should", "must", "when", "then", "so",
    "we", "i", "you", "our", "my", "not", "no", "yes", "can", "will", "does",
    "issue", "bug", "error", "problem", "test", "tests", "code", "line",
    "file", "files", "run", "runs", "why", "how", "what", "please", "also",
}

_SEARCH_DIRS = ("backend/src", "frontend/src")


def keywords(text: str, limit: int = 12) -> list[str]:
    """Pull the content words out of a request, longest first.

    Longest-first matters: 'notification' is a far better probe than 'send',
    and we only run a bounded number of scans.
    """
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text.lower())
    seen: dict[str, int] = {}
    for w in words:
        if w in _STOP or len(w) < 4:
            continue
        seen[w] = seen.get(w, 0) + 1
    # score = frequency, tie-broken by length (longer = more specific)
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], -len(kv[0])))
    return [w for w, _ in ranked[:limit]]


def python_defs(root: Path) -> list[tuple[str, str, int]]:
    """Every function/class def under backend/src as (name, path, lineno).

    Uses the AST, not a regex: a regex over `def ` also matches strings,
    comments and docstrings, and would report duplicates that do not exist.
    """
    out: list[tuple[str, str, int]] = []
    base = root / "backend" / "src"
    if not base.is_dir():
        return out
    for py in base.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, ValueError):
            continue  # a file we cannot parse is not a reason to fail the run
        rel = py.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                out.append((node.name, rel, node.lineno))
    return out


def ts_exports(root: Path) -> list[tuple[str, str, int]]:
    """Exported functions/consts/components under frontend/src."""
    out: list[tuple[str, str, int]] = []
    base = root / "frontend" / "src"
    if not base.is_dir():
        return out
    pat = re.compile(
        r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
        r"(?:function|const|class|interface|type)\s+([A-Za-z_][A-Za-z0-9_]*)"
    )
    for f in list(base.rglob("*.ts")) + list(base.rglob("*.tsx")):
        if "node_modules" in f.parts:
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = f.relative_to(root).as_posix()
        for i, line in enumerate(lines, 1):
            m = pat.match(line)
            if m:
                out.append((m.group(1), rel, i))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("request", nargs="?", default="")
    ap.add_argument("--from-file", help="read the request text from a file")
    ap.add_argument("--root", default=".")
    ap.add_argument("--max-hits", type=int, default=8, help="per keyword")
    args = ap.parse_args()

    text = args.request
    if args.from_file:
        try:
            text = Path(args.from_file).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"(could not read {args.from_file}: {exc})")
            return 0

    kws = keywords(text)
    if not kws:
        print("# Already built?\n\n(no searchable keywords in the request)")
        return 0

    root = Path(args.root).resolve()
    symbols = python_defs(root) + ts_exports(root)

    print("# Already built? — deterministic scan before you write anything\n")
    print(
        "Dumb code searched the codebase for things matching this request. "
        "An LLM cannot do this reliably (the repo does not fit in a context "
        "window), so treat the list below as evidence, not opinion.\n"
    )
    print(f"Symbols scanned: **{len(symbols)}** across `{'`, `'.join(_SEARCH_DIRS)}`")
    print(f"Keywords probed: `{'`, `'.join(kws)}`\n")

    any_hit = False
    for kw in kws:
        hits = [(n, p, ln) for (n, p, ln) in symbols if kw in n.lower()]
        if not hits:
            continue
        any_hit = True
        shown = sorted(hits, key=lambda h: len(h[0]))[: args.max_hits]
        print(f"\n## `{kw}` — {len(hits)} existing symbol(s)\n")
        for name, path, lineno in shown:
            print(f"- `{name}` — {path}:{lineno}")
        if len(hits) > len(shown):
            print(f"- …and {len(hits) - len(shown)} more")

    if not any_hit:
        print("\n**No existing symbol matched any keyword.** Likely genuinely new — "
              "but this scan matches NAMES only, so a differently-named "
              "implementation would not appear. Still grep before writing.")
    else:
        print(
            "\n---\n**Before writing new code:** check whether one of the above "
            "already does this job. Extending an existing function is almost "
            "always better than adding a second one that nearly does the same "
            "thing — duplicate logic is how two code paths silently drift apart."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
