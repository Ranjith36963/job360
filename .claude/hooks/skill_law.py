#!/usr/bin/env python3
"""skill_law.py — the SKILL LAW hook: the right skill at the right time, enforced.

Why: measured 2026-10-02 — 243 skills installed, 11% of sessions used any skill,
22/22 bug sessions used no debugging skill. Nothing routed a prompt to a skill and
CLAUDE.md's "read hard-rules before editing schema/spine/MCP/auth" was unenforced.

One registry decides the law: `.claude/skill-law.json` (no routes are hardcoded here).
Events (wired in .claude/settings.json):

  session   SessionStart  — inject a compact cheat sheet (situation → skill).
  prompt    UserPromptSubmit — match intents; inject a FORCED decision for each.
  pre       PreToolUse (Edit|Write|MultiEdit|NotebookEdit|Bash) — DENY when a law is owed.
  post      PostToolUse (Skill|Bash|Edit|Write|MultiEdit|NotebookEdit) — append to the ledger.

Laws are EVIDENCE, not ritual (Fable review): a BUG gate is satisfied by a debugging
skill OR a reproduction command; the hard-rules zone needs `hard-rules` loaded by the
SAME agent that edits (a parent's load does not put the rules in a worker's context).
Intent gates apply to the main agent only — workers never saw the prompt.

Escape hatches, all logged in the ledger and visible in `report`:
  - owner: put the skip word (default "skip-law") in the prompt;
  - model: `python .claude/hooks/skill_law.py waive "<reason>"` waives the current prompt;
  - kill switch: create `.claude/SKILL-LAW-OFF` (no restart needed).

Fails OPEN on any internal error, but LOUDLY (systemMessage + errors.log).
CLI: `report [days]` prints compliance; `--drill` runs the negative-controlled drill.
Stdlib only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent                       # repo root (…/.claude/hooks/ → …/)
REGISTRY = ROOT / ".claude" / "skill-law.json"
KILL_FILE = ROOT / ".claude" / "SKILL-LAW-OFF"
STATE_DIR = Path(os.environ.get("SKILL_LAW_STATE_DIR") or (Path.home() / ".claude" / "skill-law"))
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
LEDGER_TTL_DAYS = 7

# Evidence that a prompt is REALLY about a defect (second signal for the BUG gate):
# a traceback / exception name, an HTTP status, a test id, a log-ish line, or a source path.
BUG_EVIDENCE = re.compile(
    r"(traceback|\w+(error|exception)\b|\bhttp\s*[45]\d\d\b|\b[45]\d\d\s+(error|internal|not found|bad request)"
    r"|test_\w+|::\w+|\bassert(ion)?\b|\bexit (code )?[1-9]\d*|\.(py|tsx?|jsx?|sql):\d+|sentry|stack ?trace)",
    re.IGNORECASE,
)
# Bash is judged per SEGMENT (split on && || ; | and newlines): one substring search over
# the whole command let `sed -i <zone> && curl x` pass as "a repro" (reviewer-bugs P1 #2, #712).
SEGMENT_SPLIT = re.compile(r"&&|\|\||;|\||\n")
# A segment whose PROGRAM reproduces or observes a defect — debugging evidence.
REPRO_START = re.compile(
    r"^\s*(?:\w+=\S*\s+)*(python3?\s+-m\s+pytest|pytest|npm\s+(?:run\s+)?(?:test|test:unit|test:e2e)"
    r"|npx\s+playwright|curl|railway\s+logs|gh\s+run\s+view)\b",
    re.IGNORECASE,
)
# curl/wget flags that write a file (then a "repro" program is also a writer).
OUTPUT_FLAG = re.compile(r"\s(?:-o|-O|--output(?:-dir)?)(?:\s|=)")
# What a redirect writes to (the only thing a repro segment may write).
REDIRECT_TARGET = re.compile(r"(?:^|[^0-9>&])>{1,2}\s*([^\s;&|]+)")
# A Bash command that names repo SOURCE (where a write is a code change, not a log).
SOURCE_PATH = re.compile(r"(^|[\s'\"=:/\\])(backend|frontend|scripts|\.github|\.claude|docs)[/\\]", re.IGNORECASE)
WAIVE_CMD = re.compile(r"skill_law\.py[\"']?\s+waive\s+(.+)$", re.IGNORECASE | re.DOTALL)
BASH_WRITE = re.compile(
    r"(\bsed\s+-i|(^|[^0-9>&])>{1,2}\s*[\w./\"']|\btee\b|\bgit\s+(apply|checkout|restore)\b|\bpatch\b"
    r"|\bcp\b|\bmv\b|\brm(dir)?\b|\bunlink\b|\btruncate\b|\bln\b|<<\s*['\"]?\w+|\s(?:-o|-O|--output)(?:\s|=))"
)
# The backstop: Bash write commands are an UNBOUNDED set (rm, python -c, truncate, …), so the
# parser above is only the cheap first line. While a law is owed, PreToolUse snapshots the
# FINITE set of guarded files (mtime+size, no git) and PostToolUse compares: any guarded file
# created/changed/deleted is a violation, whatever command did it (reviewer-bugs P1 #3, #712).
SOURCE_ROOTS = ("backend", "frontend", "scripts", ".github", ".claude", "docs")
PRUNE_DIRS = {"node_modules", ".next", "__pycache__", ".git", "dist", "build", ".venv", "venv", "data",
              "worktrees", "test-results", "playwright-report", "coverage", ".pytest_cache",
              ".ruff_cache", ".mypy_cache"}


# ── tiny utils ────────────────────────────────────────────────────────────────
def now() -> float:
    """Wall-clock seconds; one place so tests can reason about ordering."""
    return time.time()


def load_registry() -> dict[str, Any]:
    """Read the law. Missing/invalid registry → law disabled (never brick a session)."""
    with REGISTRY.open(encoding="utf-8") as f:
        return json.load(f)


def glob_to_regex(glob: str) -> re.Pattern[str]:
    """Repo-relative glob → regex that matches at a '/' boundary of any absolute path."""
    out, i = [], 0
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**", i):
            out.append(".*")
            i += 2
            if i < len(glob) and glob[i] == "/":
                i += 1
            continue
        out.append({"*": "[^/]*", "?": "[^/]"}.get(c, re.escape(c)))
        i += 1
    return re.compile(r"(^|/)" + "".join(out) + r"$", re.IGNORECASE)


def norm_path(p: str) -> str:
    """Forward slashes; drop a worktree prefix so laws see repo-relative tails."""
    p = (p or "").replace("\\", "/")
    m = re.search(r"/\.claude/worktrees/[^/]+/", p)
    return p[m.end():] if m else p


def norm_skill(name: str) -> str:
    """'.claude/worktrees/x:hard-rules' → 'hard-rules'; plugin prefixes ('superpowers:') stay."""
    name = (name or "").strip().lstrip("/")
    if ":" in name:
        prefix, rest = name.split(":", 1)
        if "/" in prefix or "." in prefix:
            return rest
    return name


def skill_matches(used: str, wanted: str) -> bool:
    """Exact match, or a bare wanted name matches the same skill under any plugin prefix."""
    used, wanted = norm_skill(used), norm_skill(wanted)
    return used == wanted or (":" not in wanted and used.endswith(":" + wanted))


def agent_of(inp: dict[str, Any]) -> str:
    """'main' for the top-level agent, else the subagent id (key for per-agent laws)."""
    return str(inp.get("agent_id") or "main")


# ── ledger (append-only JSONL per session) ───────────────────────────────────
def ledger_path(session_id: str) -> Path:
    """One file per session; ids are sanitised so they cannot escape STATE_DIR."""
    safe = re.sub(r"[^\w.-]", "_", session_id or "unknown")
    return STATE_DIR / f"{safe}.jsonl"


def append(session_id: str, event: dict[str, Any]) -> None:
    """Append one event line (append is the only write → no read-modify-write races)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    event.setdefault("t", now())
    with ledger_path(session_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def read_ledger(session_id: str) -> list[dict[str, Any]]:
    """All events of a session; unreadable lines are skipped, never fatal."""
    out: list[dict[str, Any]] = []
    try:
        with ledger_path(session_id).open(encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        pass
    return out


def prune_old() -> None:
    """Delete ledgers older than LEDGER_TTL_DAYS (runs at SessionStart)."""
    cutoff = now() - LEDGER_TTL_DAYS * 86400
    stale_snap = now() - 86400            # a snapshot whose PostToolUse never came (call denied)
    for f in [*STATE_DIR.glob("*.jsonl"), *STATE_DIR.glob("snap_*.json")]:
        try:
            if f.stat().st_mtime < (stale_snap if f.name.startswith("snap_") else cutoff):
                f.unlink()
        except OSError:
            continue


# ── law evaluation ───────────────────────────────────────────────────────────
def match_intents(reg: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    """Registry intents whose pattern matches the prompt, in registry (priority) order."""
    hits = []
    for it in reg.get("intents", []):
        try:
            if re.search(it["pattern"], prompt, re.IGNORECASE):
                hits.append(it)
        except re.error:
            continue
    return hits[: int(reg.get("max_intents_per_prompt", 4))]


def last_prompt(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The most recent prompt event (prompts only come from the main agent)."""
    for e in reversed(events):
        if e.get("k") == "prompt":
            return e
    return None


def used_since(events: list[dict[str, Any]], skills: list[str], since: float, agent: str | None) -> bool:
    """Did `agent` (None = anyone) invoke any of `skills` at/after `since`?"""
    for e in events:
        if e.get("k") == "skill" and e.get("t", 0) >= since and (agent is None or e.get("agent") == agent):
            if any(skill_matches(e.get("name", ""), w) for w in skills):
                return True
    return False


def repro_since(events: list[dict[str, Any]], since: float) -> bool:
    """Did the main agent run a reproduction/observation command since `since`?"""
    return any(e.get("k") == "bash" and e.get("repro") and e.get("t", 0) >= since and e.get("agent") == "main"
               for e in events)


def owed_laws(reg: dict[str, Any], events: list[dict[str, Any]], agent: str, paths: list[str]) -> list[str]:
    """Reasons the next write is DENIED (empty list = allowed)."""
    lp = last_prompt(events)
    since = lp.get("t", 0) if lp else 0
    if lp and lp.get("waived"):
        return []
    if any(e.get("k") == "waive" and e.get("t", 0) >= since for e in events):
        return []
    owed: list[str] = []
    # 1. intent gates — main agent only (workers never saw the prompt)
    if agent == "main" and lp:
        for iid in lp.get("gates", []):
            it = next((x for x in reg.get("intents", []) if x["id"] == iid), None)
            if not it:
                continue
            if not (used_since(events, it["skills"], since, "main") or repro_since(events, since)):
                owed.append(
                    f"{it['label']}: {it['why']}. Call the Skill tool with one of: {' | '.join(it['skills'])} "
                    f"— or first reproduce it (pytest / curl / railway logs). Then retry."
                )
    # 2. path gates — per agent (the editor must hold the rules in ITS context)
    for law in reg.get("paths", []):
        if law.get("law") != "gate":
            continue
        rx = [glob_to_regex(g) for g in law.get("globs", [])]
        hit = [p for p in paths if any(r.search(norm_path(p)) for r in rx)]
        if not hit:
            continue
        missing = [s for s in law.get("skills_all", []) if not used_since(events, [s], 0, agent)]
        if missing:
            owed.append(
                f"{law['label']} ({norm_path(hit[0])}): {law['why']}. Call the Skill tool with "
                f"{' + '.join(missing)} first (this agent must load it itself), then retry."
            )
    return owed


def advise_paths(reg: dict[str, Any], events: list[dict[str, Any]], agent: str, paths: list[str]) -> list[str]:
    """Non-blocking path nudges, once per session per law per agent."""
    notes = []
    for law in reg.get("paths", []):
        if law.get("law") != "advise":
            continue
        rx = [glob_to_regex(g) for g in law.get("globs", [])]
        if not any(r.search(norm_path(p)) for p in paths for r in rx):
            continue
        if any(e.get("k") == "advised" and e.get("law") == law["id"] and e.get("agent") == agent for e in events):
            continue
        if used_since(events, law.get("skills_all", []), 0, agent):
            continue
        notes.append((law["id"], f"SKILL LAW ({law['label']}): {law['why']}. Consider: {' | '.join(law['skills_all'])}."))
    return notes


def target_paths(tool: str, tin: dict[str, Any], reg: dict[str, Any]) -> list[str]:
    """Files a tool call would write. For Bash: gated paths it names AND writes to."""
    if tool in EDIT_TOOLS:
        p = tin.get("file_path") or tin.get("notebook_path") or ""
        return [p] if p else []
    if tool == "Bash":
        return bash_writes(str(tin.get("command", "")), reg)[0]
    return []


def bash_writes(cmd: str, reg: dict[str, Any]) -> tuple[list[str], bool]:
    """(gated-zone paths written, writes-any-repo-source) judged segment by segment.

    A segment that writes nothing is free. A repro segment (pytest/npm test/curl … as its
    PROGRAM, no -o/--output) may only write through redirects, so only its redirect
    targets are judged — `pytest backend/tests/x.py > out.txt` writes out.txt, not source.
    Every other writing segment is judged on its whole text.
    """
    zone: list[str] = []
    source = False
    for seg in SEGMENT_SPLIT.split(cmd):
        if not BASH_WRITE.search(seg):
            continue
        if REPRO_START.match(seg) and not OUTPUT_FLAG.search(seg):
            text = " ".join(REDIRECT_TARGET.findall(seg))
        else:
            text = seg
        for tok in re.findall(r"[\w./\\-]+", text):
            t = norm_path(tok)
            for law in reg.get("paths", []):
                if law.get("law") == "gate" and any(glob_to_regex(g).search(t) for g in law.get("globs", [])):
                    zone.append(t)
        if SOURCE_PATH.search(text):
            source = True
    return zone, source or bool(zone)


# ── filesystem backstop ─────────────────────────────────────────────────────
def repo_root_of(cwd: str) -> Path | None:
    """The git work tree containing `cwd` (a worktree has a .git FILE, a checkout a dir)."""
    try:
        here = Path(cwd).resolve() if cwd else None
    except OSError:
        return None
    for d in ((here, *here.parents) if here else ()):
        if (d / ".git").exists():
            return d
    return None


def glob_roots(globs: list[str]) -> list[str]:
    """'backend/migrations/**' -> 'backend/migrations'; a plain file stays itself."""
    roots = []
    for g in globs:
        parts = []
        for seg in g.split("/"):
            if any(ch in seg for ch in "*?["):
                break
            parts.append(seg)
        roots.append("/".join(parts) or ".")
    return sorted(set(roots))


def walk_files(root: Path, rels: list[str]) -> dict[str, list[int]]:
    """{repo-relative path: [mtime_ns, size]} for every file under `rels` (pruned dirs skipped)."""
    out: dict[str, list[int]] = {}
    for rel in rels:
        base = root / rel
        if base.is_file():
            st = base.stat()
            out[rel] = [st.st_mtime_ns, st.st_size]
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in PRUNE_DIRS]
            for fn in filenames:
                fp = Path(dirpath) / fn
                try:
                    st = fp.stat()
                except OSError:
                    continue
                out[fp.relative_to(root).as_posix()] = [st.st_mtime_ns, st.st_size]
    return out


def snap_path(sid: str, tool_use_id: str, agent: str) -> Path:
    """Where a Bash call's pre-snapshot waits for its PostToolUse twin."""
    key = re.sub(r"[^\w.-]", "_", f"{sid}_{tool_use_id or agent}")
    return STATE_DIR / f"snap_{key}.json"


def owed_zone_globs(reg: dict[str, Any], events: list[dict[str, Any]], agent: str) -> list[str]:
    """Globs of gated path laws whose skills THIS agent has not loaded yet."""
    globs: list[str] = []
    for law in reg.get("paths", []):
        if law.get("law") == "gate" and any(not used_since(events, [s], 0, agent) for s in law.get("skills_all", [])):
            globs += law.get("globs", [])
    return globs


def take_snapshot(inp: dict[str, Any], reg: dict[str, Any], events: list[dict[str, Any]], agent: str) -> None:
    """Before a Bash call: remember the guarded files whose law is still owed."""
    root = repo_root_of(str(inp.get("cwd") or ""))
    if not root:
        return
    zone = owed_zone_globs(reg, events, agent)
    intent = agent == "main" and bool(owed_laws(reg, events, agent, []))
    if not zone and not intent:
        return
    rels = glob_roots(zone) + (list(SOURCE_ROOTS) if intent else [])
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    snap = {"root": str(root), "rels": rels, "zone": zone, "intent": intent, "files": walk_files(root, rels)}
    snap_path(inp.get("session_id", ""), str(inp.get("tool_use_id", "")), agent).write_text(json.dumps(snap), encoding="utf-8")


def check_snapshot(inp: dict[str, Any], agent: str, was_repro: bool) -> list[str]:
    """After a Bash call: what guarded files did it change while their law was owed?"""
    sp = snap_path(inp.get("session_id", ""), str(inp.get("tool_use_id", "")), agent)
    try:
        snap = json.loads(sp.read_text(encoding="utf-8"))
        sp.unlink()
    except (OSError, json.JSONDecodeError):
        return []
    before = snap["files"]
    after = walk_files(Path(snap["root"]), snap["rels"])
    changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
    if not changed:
        return []
    out = []
    rx = [glob_to_regex(g) for g in snap.get("zone", [])]
    zone_hits = [c for c in changed if any(r.search(c) for r in rx)]
    if zone_hits:
        out.append(f"HARD-RULES ZONE changed by Bash before `hard-rules` was loaded: {', '.join(zone_hits[:5])}")
    src_hits = [c for c in changed if c.split("/", 1)[0] in SOURCE_ROOTS]
    if snap.get("intent") and not was_repro and src_hits:
        out.append(f"BUG law: source changed by Bash before any diagnosis or reproduction: {', '.join(src_hits[:5])}")
    return out


# ── event handlers ───────────────────────────────────────────────────────────
def emit(obj: dict[str, Any]) -> None:
    """Hook output: one JSON object on stdout. ASCII-escaped on purpose: Windows Python's
    stdout is cp1252, and a '→' would raise and silently drop every injection."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=True))


def on_session(_inp: dict[str, Any], reg: dict[str, Any]) -> None:
    """Cheat sheet: restores the 'when to use' the skill-listing budget drops."""
    prune_old()
    lines = ["SKILL LAW (.claude/skill-law.json) — situation → skill. Load the skill with the Skill tool BEFORE acting."]
    for it in reg.get("intents", []):
        tag = " [ENFORCED with evidence]" if it.get("law") == "gate" else ""
        lines.append(f"- {it['label']}: {' | '.join(it['skills'])} — {it['why']}{tag}")
    for law in reg.get("paths", []):
        tag = "ENFORCED: edits denied until loaded by the editing agent" if law.get("law") == "gate" else "advised"
        lines.append(f"- editing {law['label']} → {' + '.join(law['skills_all'])} ({tag})")
    lines.append(f"Waivers are logged: owner types '{reg.get('skip_word', 'skip-law')}'; you may run "
                 "`python .claude/hooks/skill_law.py waive \"<reason>\"` when a match is a false positive.")
    emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "\n".join(lines)}})


def on_prompt(inp: dict[str, Any], reg: dict[str, Any]) -> None:
    """Route: forced decision per matched intent; arm evidence-backed gates."""
    sid = inp.get("session_id", "")
    prompt = str(inp.get("prompt", ""))
    waived = reg.get("skip_word", "skip-law").lower() in prompt.lower()
    hits = match_intents(reg, prompt)
    gates = [it["id"] for it in hits if it.get("law") == "gate" and BUG_EVIDENCE.search(prompt)]
    slash = re.match(r"\s*/([\w:.-]+)", prompt)
    append(sid, {"k": "prompt", "intents": [h["id"] for h in hits], "gates": gates, "waived": waived,
                 "agent": "main", "chars": len(prompt)})
    if slash:  # a typed /skill counts as using it
        append(sid, {"k": "skill", "name": slash.group(1), "agent": "main", "via": "slash"})
    if not hits or waived:
        return
    lines = ["SKILL LAW — before acting, for EACH line either call the Skill tool for it or say in one "
             "short sentence why it does not apply:"]
    for it in hits:
        mark = "  [ENFORCED: edits blocked until a skill below runs or you reproduce the bug]" if it["id"] in gates else ""
        lines.append(f"- {it['label']} → {' | '.join(it['skills'])} ({it['why']}){mark}")
    emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "\n".join(lines)}})


def on_pre(inp: dict[str, Any], reg: dict[str, Any]) -> None:
    """Gate a write: deny with the exact skill to load, or allow (optionally with a nudge)."""
    sid, tool = inp.get("session_id", ""), str(inp.get("tool_name", ""))
    tin = inp.get("tool_input") or {}
    agent = agent_of(inp)
    paths = target_paths(tool, tin, reg)
    if tool == "Bash":
        cmd = str(tin.get("command", ""))
        w = WAIVE_CMD.search(cmd)
        if w:
            reason = w.group(1).strip().strip("\"'")
            if len(reason) >= 8:
                append(sid, {"k": "waive", "reason": reason[:300], "agent": agent})
            return
        # Any Bash write into repo source meets the same laws as Edit/Write (reviewer-bugs
        # P1 on #712: a heredoc into a non-zone file dodged the BUG gate). Judged per
        # segment, so a repro (`pytest … > out.txt`) is never gated — it IS the evidence —
        # while `sed -i <source> && curl x` is (P1 #2). Temp/log writes stay free.
        if not bash_writes(cmd, reg)[1]:
            take_snapshot(inp, reg, read_ledger(sid), agent)
            return
    events = read_ledger(sid)
    owed = owed_laws(reg, events, agent, paths)
    if owed:
        append(sid, {"k": "deny", "agent": agent, "tool": tool, "paths": [norm_path(p) for p in paths], "owed": owed})
        emit({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": "SKILL LAW — " + " ‖ ".join(owed) +
            " (False positive? run: python .claude/hooks/skill_law.py waive \"<reason>\")"}})
        return
    notes = advise_paths(reg, events, agent, paths)
    if notes:
        for law_id, _ in notes:
            append(sid, {"k": "advised", "law": law_id, "agent": agent})
        emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                     "additionalContext": "\n".join(n for _, n in notes)}})


def on_post(inp: dict[str, Any], _reg: dict[str, Any]) -> None:
    """Ledger: which skills actually ran, which files were written, which repro commands ran."""
    sid, tool = inp.get("session_id", ""), str(inp.get("tool_name", ""))
    tin = inp.get("tool_input") or {}
    agent = agent_of(inp)
    if tool == "Skill":
        append(sid, {"k": "skill", "name": str(tin.get("skill", "")), "agent": agent})
    elif tool in EDIT_TOOLS:
        append(sid, {"k": "edit", "path": norm_path(str(tin.get("file_path") or tin.get("notebook_path") or "")), "agent": agent})
    elif tool == "Bash":
        segs = SEGMENT_SPLIT.split(str(tin.get("command", "")))
        m = next((x for x in (REPRO_START.match(seg) for seg in segs) if x), None)
        append(sid, {"k": "bash", "repro": bool(m), "what": m.group(1).lower()[:20] if m else "", "agent": agent})
        violations = check_snapshot(inp, agent, bool(m))
        if violations:
            append(sid, {"k": "violation", "agent": agent, "what": violations})
            emit({"decision": "block", "reason": "SKILL LAW VIOLATION — " + " ‖ ".join(violations) +
                  ". Restore those files unless the change was intended (git diff / git checkout -- <file>), "
                  "load the owed skill, then redo the change."})


def waive(reason: str) -> int:
    """CLI side of a waiver. The PreToolUse hook already logged it against the RIGHT session
    (it sees session_id); this only validates the reason and confirms to the model."""
    if len(reason.strip().strip("\"'")) < 8:
        print("skill-law: a waiver needs a real reason (8+ chars) — nothing waived.", file=sys.stderr)
        return 2
    print(f"skill-law: waived for the current prompt — reason logged: {reason.strip()[:120]}")
    return 0


def report(days: float = 7) -> int:
    """Compliance per session: prompts routed, skills used, denies, waivers."""
    cutoff = now() - days * 86400
    rows = []
    for f in sorted(STATE_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime):
        if f.stat().st_mtime < cutoff:
            continue
        ev = read_ledger(f.stem)
        prompts = [e for e in ev if e.get("k") == "prompt"]
        routed = [p for p in prompts if p.get("intents")]
        skills = sorted({norm_skill(e.get("name", "")) for e in ev if e.get("k") == "skill"})
        denied = sum(e.get("k") == "deny" for e in ev)
        waived = sum(e.get("k") == "waive" or (e.get("k") == "prompt" and bool(e.get("waived"))) for e in ev)
        rows.append((f.stem[:8], len(prompts), len(routed), len(skills), denied, waived, ", ".join(skills)[:70]))
    print(f"SKILL LAW report — last {days:g} days, {len(rows)} sessions")
    print(f"{'session':9} {'prompts':>7} {'routed':>6} {'skills':>6} {'denied':>6} {'waived':>6}  used")
    for r in rows:
        print(f"{r[0]:9} {r[1]:>7} {r[2]:>6} {r[3]:>6} {r[4]:>6} {r[5]:>6}  {r[6]}")
    with_skill = sum(1 for r in rows if r[3])
    if rows:
        print(f"sessions using ≥1 skill: {with_skill}/{len(rows)} ({100 * with_skill // len(rows)}%)")
    return 0


def drill() -> int:
    """Negative-controlled drill: the law MUST deny before the skill and MUST allow after."""
    import tempfile
    global STATE_DIR
    STATE_DIR = Path(tempfile.mkdtemp(prefix="skill-law-drill-"))
    reg = load_registry()
    sid = "drill"
    zone = "D:/x/.claude/worktrees/w/backend/migrations/0099_x.up.sql"
    results = []

    def decision(tool: str, tin: dict[str, Any], agent: str | None = None) -> str:
        ev = read_ledger(sid)
        return "deny" if owed_laws(reg, ev, agent or "main", target_paths(tool, tin, reg)) else "allow"

    sed_zone = {"command": "sed -i s/a/b/ backend/migrations/0099_x.up.sql"}
    append(sid, {"k": "prompt", "intents": [], "gates": [], "waived": False, "agent": "main"})
    results.append(("zone edit before hard-rules → deny", decision("Edit", {"file_path": zone}) == "deny"))
    results.append(("bash write into zone → deny", decision("Bash", sed_zone) == "deny"))
    results.append(("NEGATIVE: non-zone edit → allow", decision("Edit", {"file_path": "D:/x/README.md"}) == "allow"))
    append(sid, {"k": "skill", "name": "hard-rules", "agent": "main"})
    results.append(("zone edit after hard-rules → allow", decision("Edit", {"file_path": zone}) == "allow"))
    results.append(("worker without its own load → deny", decision("Edit", {"file_path": zone}, agent="w1") == "deny"))
    append(sid, {"k": "prompt", "intents": ["bug"], "gates": ["bug"], "waived": False, "agent": "main"})
    results.append(("bug gate before evidence → deny", decision("Edit", {"file_path": "D:/x/README.md"}) == "deny"))
    append(sid, {"k": "bash", "repro": True, "cmd": "pytest -k x", "agent": "main"})
    results.append(("bug gate after repro → allow", decision("Edit", {"file_path": "D:/x/README.md"}) == "allow"))
    ok = all(r for _, r in results)
    for name, r in results:
        print(f"{'PASS' if r else 'FAIL'}  {name}")
    print("drill:", "GREEN" if ok else "RED")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    """Entry point for hooks (event name as argv[1]) and the CLI."""
    cmd = argv[1] if len(argv) > 1 else ""
    for stream in (sys.stdout, sys.stderr):           # CLI output may contain → — ‖
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    if cmd == "--drill":
        return drill()
    if cmd == "report":
        return report(float(argv[2]) if len(argv) > 2 else 7)
    if cmd == "waive":
        return waive(" ".join(argv[2:]))
    if (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS") or KILL_FILE.exists()
            or os.environ.get("SKILL_LAW", "").lower() == "off"):
        return 0
    try:
        raw = sys.stdin.read()
        inp = json.loads(raw) if raw.strip() else {}
        reg = load_registry()
        if not reg.get("enabled", True):
            return 0
        handler = {"session": on_session, "prompt": on_prompt, "pre": on_pre, "post": on_post}.get(cmd)
        if handler:
            handler(inp, reg)
    except Exception:  # noqa: BLE001 — a hook must never brick a session; fail open, loudly
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            with (STATE_DIR / "errors.log").open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {cmd}\n{traceback.format_exc()}\n")
        except OSError:
            pass
        if cmd in ("pre", "prompt"):
            emit({"systemMessage": f"[skill-law] internal error in '{cmd}' — law NOT applied this time "
                                   f"(see ~/.claude/skill-law/errors.log)."})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
