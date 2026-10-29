"""MCP text in plain words (owner decision 2026-10-29).

Every description a user's assistant reads (INSTRUCTIONS, every tool, every prompt) talks
about "the user", never about the dev process: no "owner", no VISION rule numbers, no PR or
slice numbers, no dates. The closed reason values that still contain "owner" (owner_clicked,
needs_owner_click, the score_source value "owner") stay until their own slice renames them.
"""
from __future__ import annotations

import re

import pytest

# Closed values that keep the word until the rename slice; removed from the text before the scan.
_CLOSED_VALUES = ("owner_clicked", "needs_owner_click", "full_ad|listing|owner", '"owner" (the')
_DEV_WORDS = ("owner", "vision")
# Fixed-shape patterns, no nesting or backtracking: "#123", "2026-10-29", "Slice 5".
_NUMBERED = (re.compile(r"#[0-9]"), re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}"), re.compile(r"slice [0-9]"))


def dev_process_hits(text: str) -> list[str]:
    cleaned = text
    for value in _CLOSED_VALUES:
        cleaned = cleaned.replace(value, " ")
    lowered = cleaned.lower()
    hits = [w for w in _DEV_WORDS if w in lowered]
    hits += [p.pattern for p in _NUMBERED if p.search(lowered)]
    return hits


def test_the_scan_can_go_red():
    assert dev_process_hits("tell the owner") == ["owner"]
    assert dev_process_hits("never computed by Job360 (VISION rule 4)") == ["vision"]
    assert dev_process_hits("see PR #483, 2026-10-29, Slice 5")
    assert dev_process_hits("score_source full_ad|listing|owner; owner_clicked; tell the user") == []


@pytest.mark.asyncio
async def test_no_dev_process_text_in_any_tool_description_or_instructions():
    from src.api.mcp_server import build_server

    server = build_server()
    texts = {"INSTRUCTIONS": server.instructions or ""}
    texts.update({t.name: (t.description or "") for t in await server.list_tools()})
    texts.update({f"prompt:{p.name}": (p.description or "") for p in await server.list_prompts()})
    assert len(texts) > 20, "the live server was read, not an empty one"
    bad = {name: hits for name, text in texts.items() if (hits := dev_process_hits(text))}
    assert not bad, f"dev-process words in text a user's assistant reads: {bad}"
