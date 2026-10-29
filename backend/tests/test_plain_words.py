"""Owner decision 2026-10-29: the user reads plain words, never the developers' notes.

``plain_words.dev_talk`` refuses a PR / issue number at write time; ``internal_code`` refuses a closed
internal code as a whole word. Both are word scans (no regex over caller text), so a real note with a
job's own "Req #12345" stays legal.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.services.applications import plain_words


@pytest.mark.parametrize("text", [
    "fixed in #1180",
    "after #1169 went live",
    "tests #821",
    "see PR 1180",
    "Done (PR #1180).",
    "merged via #77, then deployed",
    "This was a pull request about the form",
    "see PR#12",
    "Fixed in (#1180)",
])
def test_dev_talk_is_refused(text):
    refusal = plain_words.dev_talk(text)
    assert refusal is not None and "developer notes" in refusal, text


@pytest.mark.parametrize("text", [
    "Req #12345",
    "Job ID #4521",
    "the posting went live Monday",
    "ask 3 friends",
    "The company deployed a new careers site last week",
    "Salary band #3 applies",
    "",
    None,
])
def test_real_notes_stay_legal(text):
    assert plain_words.dev_talk(text) is None


@pytest.mark.parametrize("text", [
    "Blocked by cv_not_seen on this job",
    "ran check_submit and it said stop",
    "see artifact 12 for the CV",
    "event #44 shows it",
    "Call ask_user next.",
])
def test_internal_codes_are_refused(text):
    refusal = plain_words.internal_code(text)
    assert refusal is not None and "internal code" in refusal, text


@pytest.mark.parametrize("text", [
    "Your assistant got stuck: a CAPTCHA on jobs.example.",
    "The event starts at 6pm",
    "I attached 2 artifacts",
    "Is the job blocked or paused?",
    "stats and browser are plain words",
])
def test_plain_words_pass_internal_code(text):
    assert plain_words.internal_code(text) is None


def test_refusal_checks_every_text_and_skips_none():
    assert plain_words.refusal(None, "", "fine") is None
    assert plain_words.refusal("fine", "fixed in #9") is not None
    assert plain_words.refusal("use check_submit") is not None


def test_the_mcp_tool_names_are_the_servers_underscore_tools():
    tree = ast.parse((Path(__file__).parent.parent / "src" / "api" / "mcp_server.py").read_text(encoding="utf-8"))
    names = {
        f.name for f in ast.walk(tree) if isinstance(f, (ast.AsyncFunctionDef, ast.FunctionDef))
        and any("mcp.tool" in ast.unparse(d) for d in f.decorator_list) and "_" in f.name
    }
    assert names and names == set(plain_words.MCP_TOOL_NAMES)
