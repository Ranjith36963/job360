#!/usr/bin/env python3
"""The MCP text budget — no server text the assistant reads may pass 2,000 characters.

WHY THIS EXISTS
---------------
Claude Code silently truncates an MCP server's instructions and every tool
description at 2,048 characters (anthropics/claude-code#81268). It says nothing:
the tail of the text is simply gone. Job360 shipped with INSTRUCTIONS at 10,099
characters and three tool descriptions over 3,000, so the safety lines that sat
late in the text never reached the assistant. The hard lines now come first and
the long rules live in `get_recipe("rules")`. This guard keeps it that way: every
text must stay at or under 2,000 characters (48 characters of margin).

USAGE
-----
    python scripts/mcp_text_budget.py                       # measure the LIVE server, print every length
    python scripts/mcp_text_budget.py --drill               # break it on purpose (stdlib only; CI runs this)
    python scripts/mcp_text_budget.py --drill --break-checker LIMIT
                                                            # NEGATIVE CONTROL: blinds the limit; must exit 1

The checker is `check(texts, limit)`; `backend/tests/test_mcp_text_budget.py`
feeds it the live tool list, and the drill proves it can still go red.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 2000  # Claude Code truncates at 2,048; stay under with a margin.

# `--break-checker LIMIT` sets this: the limit check then never fires, exactly
# the "checker gone blind" failure the drill exists to catch.
_BLIND_LIMIT = False


def check(texts: Mapping[str, str], limit: int = LIMIT) -> list[str]:
    """One message per text longer than ``limit``, naming it and its length.
    An empty mapping is clean (nothing to measure is not a violation)."""
    if _BLIND_LIMIT:
        return []
    return [
        f"{name} is {len(text)} characters, over the {limit}-character limit"
        for name, text in texts.items()
        if len(text) > limit
    ]


def live_texts() -> dict[str, str]:
    """The instructions the built server sends at initialize, plus every tool
    and prompt description on its live lists (exactly the strings a client gets)."""
    sys.path.insert(0, str(ROOT / "backend"))
    from src.api.mcp_server import build_server  # noqa: PLC0415 — heavy, only for a live run

    server = build_server()

    async def _listed() -> dict[str, str]:
        tools = {t.name: (t.description or "") for t in await server.list_tools()}
        prompts = {f"prompt:{p.name}": (p.description or "") for p in await server.list_prompts()}
        return {**tools, **prompts}

    out = {"INSTRUCTIONS": server.instructions or ""}
    out.update(asyncio.run(_listed()))
    return out


def self_drill() -> int:
    """Break the budget on purpose. A guard is trusted once it has been watched
    go red: 2,001 characters must be named, 2,000 must pass, nothing must pass."""
    misses: list[str] = []
    over = check({"too_long": "x" * (LIMIT + 1)})
    if not over or "too_long" not in over[0] or str(LIMIT + 1) not in over[0]:
        misses.append(f"a {LIMIT + 1}-character text was not flagged by name and length: {over!r}")
    if check({"exact": "x" * LIMIT}):
        misses.append(f"a {LIMIT}-character text was flagged, but {LIMIT} is allowed")
    if check({}):
        misses.append("an empty mapping was flagged")
    mixed = check({"ok": "x", "bad": "y" * (LIMIT + 50)})
    if len(mixed) != 1 or "bad" not in mixed[0]:
        misses.append(f"one bad text among good ones was not isolated: {mixed!r}")
    for miss in misses:
        print(f"DRILL MISS: {miss}", file=sys.stderr)
    if misses:
        return 1
    print(f"drill ok: {LIMIT + 1} flagged by name, {LIMIT} clean, empty clean")
    return 0


def report(texts: Mapping[str, str], limit: int = LIMIT) -> int:
    """Print every length (longest first) and exit 1 if any is over the limit."""
    for name, text in sorted(texts.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(text):>6}  {name}")
    problems = check(texts, limit)
    for line in problems:
        print(f"OVER BUDGET: {line}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    global _BLIND_LIMIT
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--drill", action="store_true", help="break it on purpose")
    ap.add_argument("--break-checker", choices=["LIMIT"], help="negative control: blind the named check")
    args = ap.parse_args(argv)
    if args.break_checker == "LIMIT":
        _BLIND_LIMIT = True
    if args.drill:
        return self_drill()
    return report(live_texts())


if __name__ == "__main__":
    sys.exit(main())
