# Hardening — prose rules become machinery

Report finding #1: the entire safety model is honor-system. These three changes fix the worst of it. Apply before launching any worker.

## 1. Make `git push` impossible (permissions)

Add to `.claude/settings.json` (project-level so every worktree session inherits it):

```json
{
  "permissions": {
    "deny": [
      "Bash(git push:*)",
      "Bash(git push)",
      "... keep the existing 11 MCP denies ..."
    ]
  }
}
```

## 2. The gate script — the ONLY way to earn a commit

`scripts/agent-gate.sh` (bash via Git Bash, which the repo already uses for hooks):

```bash
#!/usr/bin/env bash
# Runs the canonical gates and writes a stamp binding the PASS to this exact tree state.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

tree_fingerprint() {
  { git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin
}

CHANGED="$(git status --porcelain -- backend frontend | awk '{print $2}')"
BACKEND_CHANGED=$(echo "$CHANGED" | grep -c '^backend/' || true)
FRONTEND_CHANGED=$(echo "$CHANGED" | grep -c '^frontend/' || true)

if [ "$BACKEND_CHANGED" -gt 0 ]; then
  echo "[gate] backend suite..."
  (cd backend && python -m pytest -q -p no:randomly)
fi
if [ "$FRONTEND_CHANGED" -gt 0 ]; then
  echo "[gate] frontend gates..."
  (cd frontend && npm run -s test:unit && npm run -s type-check && npm run -s lint)
fi

mkdir -p "$ROOT/.claude"
tree_fingerprint > "$ROOT/.claude/gate-stamp"
echo "[gate] PASS — stamp written"
```

Properties: the stamp is a hash of HEAD + working tree + index. Any edit after the gate run changes the fingerprint and invalidates the stamp — no "ran tests, then changed one more thing, then committed."

## 3. The commit-gate hook — blocks ungated commits

`.claude/hooks/commit-gate.sh`:

```bash
#!/usr/bin/env bash
# PreToolUse hook: blocks `git commit` unless a fresh gate stamp matches the current tree.
set -uo pipefail
INPUT="$(cat)"
CMD="$(echo "$INPUT" | python -c "import sys,json;print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)"

case "$CMD" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
STAMP="$ROOT/.claude/gate-stamp"
CURRENT="$({ git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin)"

if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$CURRENT" ]; then
  exit 0
fi

echo "BLOCKED: no fresh gate stamp for this exact tree state. Run: bash scripts/agent-gate.sh (then commit without further edits)." >&2
exit 2
```

Register in `.claude/settings.json` so it applies to every session and worktree:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "bash .claude/hooks/commit-gate.sh", "timeout": 15 }
        ]
      }
    ]
  }
}
```

Keep the existing non-blocking Stop reminder — it's a good second signal.

## 4. What stays honor-system (acceptable, documented)
- One-mission-per-worker and files-owned discipline (enforceable later via a hook that diffs touched paths against MISSIONS.md — add if a worker ever violates it).
- No-server-in-worktrees (mitigated by fixed ports: a second bind just fails).
- Migration prohibition (mitigated: migrations/ could be added to a worker-side deny on Edit/Write paths if ever violated).

## 5. Verify the hardening (do this once)
1. Edit any file, try `git commit` → must be BLOCKED.
2. Run `bash scripts/agent-gate.sh` → green → commit → must succeed.
3. Edit one more char after gating → commit → must be BLOCKED again.
4. Try `git push` in a session → must be denied by permissions.
