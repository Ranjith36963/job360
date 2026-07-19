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

# P4 — do not fail OPEN when the parse fails.
#
# Previously an empty CMD (python missing, JSON shape changed, malformed input)
# fell straight through the `*)` arm below and ALLOWED the commit. The one input
# the gate could not understand was the one it waved through — and silently,
# because 2>/dev/null hides the reason.
#
# We cannot block on every unparseable Bash call (this hook sees ALL of them, so
# that would wedge the session). Instead, fall back to scanning the RAW payload:
# if the words "git commit" appear anywhere in it, treat it as a commit and let
# the stamp check decide. Worst case we gate a command that merely mentions the
# phrase — annoying, but it fails toward safety instead of away from it.
if [ -z "$CMD" ]; then
  case "$INPUT" in
    *"git commit"*)
      echo "[commit-gate] WARNING: could not parse tool_input; recovered 'git commit' from raw payload." >&2
      CMD="git commit"
      ;;
    *) exit 0 ;;
  esac
fi

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
