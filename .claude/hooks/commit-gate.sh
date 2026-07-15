#!/usr/bin/env bash
# PreToolUse hook: blocks `git commit` unless a fresh gate stamp matches the
# current tree state. The stamp is written ONLY by scripts/agent-gate.sh after
# a real green run. Exit 2 = block the tool call. See agentic-loop/hardening.md.
#
# Tiered (2026-07-15): commits that touch NOTHING under backend/ or frontend/
# (docs, scripts, config) skip the gate entirely — there is no code to test.
set -uo pipefail
INPUT="$(cat)"
CMD="$(echo "$INPUT" | python -c "import sys,json;print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null)"

case "$CMD" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# Docs/scripts/config-only tree: no changes at all under backend/ or frontend/
# means nothing testable is being committed — allow instantly.
if [ -z "$(git status --porcelain -- backend frontend)" ]; then
  exit 0
fi

STAMP="$ROOT/.claude/gate-stamp"
CURRENT="$({ git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin)"

if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$CURRENT" ]; then
  exit 0
fi

echo "BLOCKED: no fresh gate stamp for this exact tree state. Run: bash scripts/agent-gate.sh (targeted tests, fast) or bash scripts/agent-gate.sh --full (whole suite), then commit without further edits." >&2
exit 2
