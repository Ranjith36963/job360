"""Plain words for the user (owner decision 2026-10-29): what Job360 shows the user never carries the
developers' own notes.

Two narrow write-time checks on text a CALLER wrote (an assistant over MCP or the web):

* ``dev_talk`` - a PR or issue number ("fixed in #1180", "see PR 1180", "pull request"). A number that
  belongs to the job ("Req #12345", "Job ID #4521") stays legal: only a number right after a word that
  introduces a code change counts.
* ``internal_code`` - a word that is exactly one of Job360's own closed codes (a check_submit reason,
  an MCP tool name) or "artifact 7" / "event 12".

Both work on the words of ``text.split()``, each trimmed of punctuation: no regex runs over caller text
(CodeQL py/redos), and the module imports nothing heavy. Callers turn a hit into a 422 whose text says
what to change. Nothing here rewrites stored text (rule M3).
"""
from __future__ import annotations

from typing import Optional

# Words that introduce a reference to a code change when "#<digits>" follows them.
_REF_LEADS = frozenset({
    "pr", "prs", "pull", "in", "after", "by", "see", "fixed", "since", "merged", "via", "tests", "test",
})
_PR_WORDS = frozenset({"pr", "prs"})
_TRIM = ".,;:!?()[]{}<>\"'`*_-"  # trimmed from both ends of a word; '#' stays (it marks a reference)

DEV_TALK_REFUSAL = (
    "This reads like developer notes (a PR or issue number, or a deploy note). "
    "Write what happened in the user's words."
)

# Closed codes with an underscore, so a plain word the user may write ("blocked", "stats") never matches.
# The MCP tool names are checked against ``mcp_server`` by ``tests/test_plain_words.py``.
MCP_TOOL_NAMES = frozenset({
    "get_profile", "bring_job", "get_job", "get_tailored_documents", "record_application", "list_receipts",
    "get_receipt", "get_application", "list_applications", "mark_inbox_checked", "whats_due", "mark_done",
    "own_browser_queue", "match_email", "save_artifact", "save_fit", "record_event", "record_events",
    "whats_new", "export_history", "add_contact", "update_contact", "list_people", "update_job",
    "update_profile", "check_submit", "check_outreach", "check_auto_email", "report_missing_tool",
    "report_capabilities", "get_application_kit", "check_form", "get_proof_upload_link", "get_recipe",
    "ask_user", "answer_ask",
})
_NUMBERED = frozenset({"artifact", "artifacts", "event", "events"})


def _words(text: str) -> list[str]:
    return [w.strip(_TRIM) for w in text.split()]


def _is_number(word: str) -> bool:
    return bool(word) and word.isascii() and word.isdigit()


def _ref_number(word: str) -> bool:
    """``#1180`` (a hash and digits)."""
    return word.startswith("#") and _is_number(word[1:])


def dev_talk(text: Optional[str]) -> Optional[str]:
    """The refusal text when ``text`` cites a PR or issue number, else None."""
    words = _words(text or "")
    lowered = [w.lower() for w in words]
    for i, word in enumerate(lowered):
        before = lowered[i - 1] if i else ""
        after = lowered[i + 1] if i + 1 < len(lowered) else ""
        if _ref_number(word) and before in _REF_LEADS:
            return DEV_TALK_REFUSAL
        if word in _PR_WORDS and (_is_number(after) or _ref_number(after)):
            return DEV_TALK_REFUSAL
        if word.startswith("pr#") and _is_number(word[3:]):
            return DEV_TALK_REFUSAL
        if word == "pull" and after == "request":
            return DEV_TALK_REFUSAL
    return None


def internal_code(text: Optional[str]) -> Optional[str]:
    """The refusal text when ``text`` holds one of Job360's own closed codes as a whole word, else None."""
    from src.services.profile.assistant_settings import REASONS  # noqa: PLC0415 - keep this module light

    codes = {code for code in REASONS if "_" in code} | MCP_TOOL_NAMES
    words = [w.lower() for w in _words(text or "")]
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        if word in codes or (word in _NUMBERED and (_is_number(nxt) or _ref_number(nxt))):
            shown = f"{word} {nxt}" if word in _NUMBERED else word
            return f'This mentions an internal code ("{shown}"). Say it in plain words for the user.'
    return None


def refusal(*texts: Optional[str]) -> Optional[str]:
    """The first refusal for any of ``texts`` (``dev_talk`` first, then ``internal_code``), else None."""
    for text in texts:
        if text and (found := dev_talk(text) or internal_code(text)):
            return found
    return None
