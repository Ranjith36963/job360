"""SKILL LAW hook — the right skill at the right time, enforced (.claude/hooks/skill_law.py).

WHY THIS TEST EXISTS
--------------------
Measured 2026-10-02: 243 skills installed, 11% of sessions used any skill, 22/22 bug
sessions used no debugging skill. The hook routes every prompt to skills and DENIES
writes when a law is owed. A guard that cannot fire is decoration, so every law below
is driven end-to-end through the real hook process (stdin JSON in, stdout JSON out —
which also pins the Windows cp1252 crash the drill caught) and each deny has a
NEGATIVE control proving the same input is allowed once the evidence exists.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / ".claude" / "hooks" / "skill_law.py"
REGISTRY = REPO / ".claude" / "skill-law.json"
ZONE = "D:/dev/job360/.claude/worktrees/w/backend/migrations/0099_x.up.sql"
PLAIN = "D:/dev/job360/README.md"


@pytest.fixture()
def run(tmp_path: Path):
    """Run one hook event as Claude Code would: JSON on stdin, decision JSON on stdout."""
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS", "SKILL_LAW")}
    env["SKILL_LAW_STATE_DIR"] = str(tmp_path)
    env.pop("PYTHONIOENCODING", None)                 # keep the platform default encoding

    def _run(event: str, payload: dict, session: str = "s1") -> dict:
        payload = {"session_id": session, **payload}
        out = subprocess.run([sys.executable, str(HOOK), event], input=json.dumps(payload),
                             capture_output=True, text=True, env=env, timeout=30, check=True)
        assert "Traceback" not in out.stderr, out.stderr
        return json.loads(out.stdout) if out.stdout.strip() else {}

    return _run


def decision(resp: dict) -> str:
    """'deny' or 'allow' from a PreToolUse response."""
    return (resp.get("hookSpecificOutput") or {}).get("permissionDecision") or "allow"


def context(resp: dict) -> str:
    """Injected additionalContext ('' when none)."""
    return (resp.get("hookSpecificOutput") or {}).get("additionalContext") or ""


# ── the registry itself ─────────────────────────────────────────────────────
def test_registry_is_valid() -> None:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    ids = [i["id"] for i in reg["intents"]]
    assert len(ids) == len(set(ids)), "duplicate intent ids"
    for it in reg["intents"]:
        re.compile(it["pattern"])
        assert it["law"] in ("gate", "advise") and it["skills"] and it["why"]
    for law in reg["paths"]:
        assert law["law"] in ("gate", "advise") and law["globs"] and law["skills_all"]


def test_drill_is_green() -> None:
    out = subprocess.run([sys.executable, str(HOOK), "--drill"], capture_output=True, text=True,
                         timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert out.returncode == 0 and "drill: GREEN" in out.stdout, out.stdout + out.stderr


# ── L1 route ────────────────────────────────────────────────────────────────
def test_bug_prompt_with_evidence_is_enforced(run) -> None:
    ctx = context(run("prompt", {"prompt": "fix this bug: KeyError in test_receipts::test_x"}))
    assert "BUG" in ctx and "systematic-debugging" in ctx and "ENFORCED" in ctx


def test_bug_word_alone_only_advises(run) -> None:
    """Voice prompts are noisy: one keyword must never arm a deny (two signals needed)."""
    ctx = context(run("prompt", {"prompt": "there is an error in the landing copy"}))
    assert "BUG" in ctx and "ENFORCED" not in ctx
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}})) == "allow"


def test_unrelated_prompt_injects_nothing(run) -> None:
    assert run("prompt", {"prompt": "thanks, looks fine"}) == {}


def test_skip_word_waives(run) -> None:
    assert run("prompt", {"prompt": "skip-law fix this bug: KeyError at spine.py:42"}) == {}
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}})) == "allow"


def test_session_cheat_sheet_lists_every_intent(run) -> None:
    ctx = context(run("session", {"source": "startup"}))
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for it in reg["intents"]:
        assert it["label"] in ctx


# ── L2 gates: bug intent ────────────────────────────────────────────────────
def test_bug_gate_denies_until_debug_skill(run) -> None:
    run("prompt", {"prompt": "it crashes: Traceback ... ValueError"})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}})) == "deny"
    run("post", {"tool_name": "Skill", "tool_input": {"skill": "superpowers:systematic-debugging"}})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}})) == "allow"


def test_bug_gate_satisfied_by_reproduction(run) -> None:
    run("prompt", {"prompt": "broken: HTTP 500 on /jobs/bring"})
    assert decision(run("pre", {"tool_name": "Write", "tool_input": {"file_path": PLAIN}})) == "deny"
    run("post", {"tool_name": "Bash", "tool_input": {"command": "python -m pytest -k bring -q"}})
    assert decision(run("pre", {"tool_name": "Write", "tool_input": {"file_path": PLAIN}})) == "allow"


def test_bug_gate_resets_on_next_prompt(run) -> None:
    run("prompt", {"prompt": "crash: Traceback ValueError"})
    run("prompt", {"prompt": "never mind, rename the button"})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}})) == "allow"


def test_logged_model_waiver(run) -> None:
    run("prompt", {"prompt": "the error text in the footer: ValueError shown to users"})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}})) == "deny"
    run("pre", {"tool_name": "Bash", "tool_input": {"command": 'python .claude/hooks/skill_law.py waive "copy change, not a defect"'}})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}})) == "allow"


def test_short_waiver_reason_is_refused(run) -> None:
    run("prompt", {"prompt": "crash: Traceback ValueError"})
    run("pre", {"tool_name": "Bash", "tool_input": {"command": 'python .claude/hooks/skill_law.py waive "no"'}})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}})) == "deny"


def test_intent_gate_never_blocks_workers(run) -> None:
    """Workers never saw the prompt — a bug gate must not kill a parallel fan-out."""
    run("prompt", {"prompt": "crash: Traceback ValueError"})
    resp = run("pre", {"tool_name": "Edit", "tool_input": {"file_path": PLAIN}, "agent_id": "w1"})
    assert decision(resp) == "allow"


# ── L2 gates: hard-rules zone ───────────────────────────────────────────────
def test_zone_edit_denied_until_hard_rules(run) -> None:
    run("prompt", {"prompt": "add a column"})
    resp = run("pre", {"tool_name": "Edit", "tool_input": {"file_path": ZONE}})
    assert decision(resp) == "deny" and "hard-rules" in resp["hookSpecificOutput"]["permissionDecisionReason"]
    run("post", {"tool_name": "Skill", "tool_input": {"skill": "hard-rules"}})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": ZONE}})) == "allow"


def test_zone_skill_under_worktree_prefix_counts(run) -> None:
    run("post", {"tool_name": "Skill", "tool_input": {"skill": ".claude/worktrees/x:hard-rules"}})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": ZONE}})) == "allow"


def test_worker_must_load_hard_rules_itself(run) -> None:
    run("post", {"tool_name": "Skill", "tool_input": {"skill": "hard-rules"}})          # parent loads
    w = {"tool_name": "Edit", "tool_input": {"file_path": ZONE}, "agent_id": "w1"}
    assert decision(run("pre", w)) == "deny"
    run("post", {"tool_name": "Skill", "tool_input": {"skill": "hard-rules"}, "agent_id": "w1"})
    assert decision(run("pre", w)) == "allow"


@pytest.mark.parametrize("cmd", [
    "sed -i 's/a/b/' backend/migrations/0099_x.up.sql",
    "cat > backend/src/services/applications/spine.py <<'EOF'\nx\nEOF",
    "git apply fix.patch backend/src/api/mcp_server.py",
])
def test_bash_write_into_zone_denied(run, cmd: str) -> None:
    assert decision(run("pre", {"tool_name": "Bash", "tool_input": {"command": cmd}})) == "deny"


@pytest.mark.parametrize("cmd", [
    "cat backend/src/services/applications/spine.py | head -40",
    "grep -n UPDATE backend/src/repositories/pg.py 2>/dev/null",
    "sed -i 's/a/b/' frontend/src/app/page.tsx",
])
def test_bash_read_or_outside_zone_allowed(run, cmd: str) -> None:
    """NEGATIVE controls: reading the zone, or writing outside it, is never gated."""
    assert decision(run("pre", {"tool_name": "Bash", "tool_input": {"command": cmd}})) == "allow"


def test_frontend_ui_is_advised_once_never_denied(run) -> None:
    tsx = {"tool_name": "Edit", "tool_input": {"file_path": "D:/dev/job360/frontend/src/app/page.tsx"}}
    first = run("pre", tsx)
    assert decision(first) == "allow" and "design:design-critique" in context(first)
    assert context(run("pre", tsx)) == ""


# ── L3 ledger + L4 measure ──────────────────────────────────────────────────
def test_slash_command_counts_as_skill_use(run) -> None:
    run("prompt", {"prompt": "/hard-rules"})
    assert decision(run("pre", {"tool_name": "Edit", "tool_input": {"file_path": ZONE}})) == "allow"


def test_ledger_never_stores_raw_commands(run, tmp_path: Path) -> None:
    run("post", {"tool_name": "Bash", "tool_input": {"command": "curl -H 'Authorization: Bearer j360_SECRET' x"}})
    assert "j360_SECRET" not in (tmp_path / "s1.jsonl").read_text(encoding="utf-8")


def test_kill_switch_env_disables() -> None:
    env = {**os.environ, "SKILL_LAW": "off"}
    out = subprocess.run([sys.executable, str(HOOK), "pre"], capture_output=True, text=True, env=env, timeout=30,
                         input=json.dumps({"session_id": "k", "tool_name": "Edit", "tool_input": {"file_path": ZONE}}))
    assert out.stdout.strip() == ""


def test_broken_input_fails_open_loudly(tmp_path: Path) -> None:
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS", "SKILL_LAW")}
    env["SKILL_LAW_STATE_DIR"] = str(tmp_path)
    out = subprocess.run([sys.executable, str(HOOK), "pre"], input="{not json", capture_output=True,
                         text=True, env=env, timeout=30)
    assert out.returncode == 0 and "skill-law" in json.loads(out.stdout)["systemMessage"]
    assert (tmp_path / "errors.log").exists()
