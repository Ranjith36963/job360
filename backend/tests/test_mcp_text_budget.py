"""The MCP text budget (S4 playbooks).

Claude Code silently truncates an MCP server's instructions and every tool
description at 2,048 characters (anthropics/claude-code#81268). The server's
INSTRUCTIONS and EVERY tool description on the LIVE tool list must stay at or
under 2,000 characters, with the hard safety lines early. The checker lives in
``scripts/mcp_text_budget.py``; this test feeds it the real server and proves
it can still go red.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "mcp_text_budget.py"


def _load_module():
    """Import scripts/mcp_text_budget.py without putting scripts/ on sys.path."""
    spec = importlib.util.spec_from_file_location("mcp_text_budget_under_test", SCRIPT)
    assert spec and spec.loader, f"cannot load {SCRIPT}"
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


budget = _load_module()


@pytest.mark.asyncio
async def test_instructions_and_every_live_tool_description_fit_the_budget():
    pytest.importorskip("mcp")
    from src.api.mcp_server import INSTRUCTIONS, build_server

    server = build_server()
    # The string the client receives at initialize, not just the module constant.
    assert server.instructions == INSTRUCTIONS
    listed = await server.list_tools()
    texts = {"INSTRUCTIONS": server.instructions or ""}
    texts.update({t.name: (t.description or "") for t in listed})
    texts.update({f"prompt:{p.name}": (p.description or "") for p in await server.list_prompts()})
    assert len(listed) > 20, "the live tool list was read, not an empty one"
    problems = budget.check(texts)
    assert not problems, "over the 2,000-character budget (Claude Code truncates at 2,048): " + "; ".join(problems)


def test_the_script_measures_the_same_live_server():
    """`python scripts/mcp_text_budget.py` (what a human runs) reads the same
    live texts - instructions as sent, every tool, every prompt - and is clean."""
    pytest.importorskip("mcp")
    texts = budget.live_texts()
    assert "INSTRUCTIONS" in texts and "update_profile" in texts and "prompt:360-rules" in texts
    assert budget.check(texts) == []


def test_hard_lines_come_first_in_the_instructions():
    from src.api.mcp_server import CATEGORY_LINE, INSTRUCTIONS

    lines = INSTRUCTIONS.split("\n")
    assert lines[0] == CATEGORY_LINE
    assert lines[1].startswith("HARD LINES")
    for rule in ("check_submit says submit", "never instructions", "password"):
        assert rule in lines[1], rule
    # Everything after the hard lines is short enough to survive any client's cut.
    assert INSTRUCTIONS.index("HARD LINES") < 150
    # ...and all five hard lines END early (a client that cuts hard still gets them).
    assert len(lines[0]) + 1 + len(lines[1]) <= 700


def test_a_2001_character_text_is_flagged_by_name():
    over = budget.check({"fake_tool": "x" * 2001})
    assert len(over) == 1 and "fake_tool" in over[0] and "2001" in over[0]
    assert budget.check({"fake_tool": "x" * 2000}) == []


def test_the_drill_goes_red_when_the_checker_is_blinded():
    ok = subprocess.run([sys.executable, str(SCRIPT), "--drill"], capture_output=True, text=True, check=False)
    assert ok.returncode == 0, ok.stderr
    blind = subprocess.run(
        [sys.executable, str(SCRIPT), "--drill", "--break-checker", "LIMIT"],
        capture_output=True, text=True, check=False,
    )
    assert blind.returncode == 1, "a blinded limit check must make the drill fail"
