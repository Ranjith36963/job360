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

# Which commands actually CREATE A COMMIT.
#
# The old test was a single substring match on "git commit". Probed 2026-08-11,
# 7 of 8 real commit-creating forms slipped straight through it:
#     git commit -m x                  BLOCKED
#     git -C . commit -m x             ALLOWED   <- global flag before the verb
#     git  commit -m x                 ALLOWED   <- two spaces
#     git -c user.name=x commit -m x   ALLOWED
#     git merge --no-ff foo            ALLOWED   <- a merge tree was never tested
#     git cherry-pick abc              ALLOWED
#     git revert abc                   ALLOWED
#     git rebase --continue            ALLOWED   <- conflict resolution, untested
#
# Parse instead of pattern-match: find the `git` token, skip any global flags
# (and their values) that may sit between it and the subcommand, then compare the
# subcommand against the list. Every one of these produces a tree that no test has
# seen, which is exactly what the stamp exists to prevent.
_creates_commit() {
  local tokens verb i n skip
  # shellcheck disable=SC2206
  tokens=($1)
  n=${#tokens[@]}
  i=0
  while [ "$i" -lt "$n" ]; do
    case "${tokens[$i]}" in
      git|*/git|git.exe|*/git.exe)
        i=$((i+1)); skip=0
        while [ "$i" -lt "$n" ]; do
          if [ "$skip" -eq 1 ]; then skip=0; i=$((i+1)); continue; fi
          case "${tokens[$i]}" in
            # Global flags that take a separate value argument.
            -C|-c|--git-dir|--work-tree|--namespace|--exec-path)
              skip=1; i=$((i+1)); continue ;;
            -*|--*) i=$((i+1)); continue ;;
            *) break ;;
          esac
        done
        [ "$i" -lt "$n" ] || return 1
        verb="${tokens[$i]}"
        case "$verb" in
          commit|merge|cherry-pick|revert|rebase|am) return 0 ;;
          *) return 1 ;;
        esac
        ;;
    esac
    i=$((i+1))
  done
  return 1
}

_creates_commit "$CMD" || exit 0

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

# GUARDS ARE NOT EXEMPT FROM BEING CHECKED.
#
# The early exit below waves through any commit that touches nothing under
# backend/ or frontend/ — correct for docs, but it also means the hooks and
# workflows that ENFORCE everything else commit with zero verification. Three
# guards shipped broken on 2026-08-11 for exactly this reason: a size check that
# swallowed its own exit code, a sed range whose anchor had been deleted, and a
# drift rule blind to the file carrying its bug. All three were syntactically
# valid, so this catches a narrower class — but a YAML or bash parse error in a
# guard is silent until the loop next runs, which can be a day later.
GUARD_FILES="$(git status --porcelain -uall -- .claude/hooks .github/workflows scripts \
               | awk '{print $NF}')"
if [ -n "$GUARD_FILES" ]; then
  for f in $GUARD_FILES; do
    [ -f "$ROOT/$f" ] || continue
    case "$f" in
      *.sh)
        bash -n "$ROOT/$f" 2>/tmp/gate-syn.$$ || {
          echo "[commit-gate] BLOCKED: $f is not valid bash:" >&2
          cat /tmp/gate-syn.$$ >&2; rm -f /tmp/gate-syn.$$; exit 2; }
        rm -f /tmp/gate-syn.$$ ;;
      *.yml|*.yaml)
        python -c "import sys,yaml;yaml.safe_load(open(sys.argv[1],encoding='utf-8'))" \
          "$ROOT/$f" 2>/tmp/gate-syn.$$ || {
          echo "[commit-gate] BLOCKED: $f is not valid YAML:" >&2
          cat /tmp/gate-syn.$$ >&2; rm -f /tmp/gate-syn.$$; exit 2; }
        rm -f /tmp/gate-syn.$$ ;;
      *.py)
        python -c "import sys,ast;ast.parse(open(sys.argv[1],encoding='utf-8').read())" \
          "$ROOT/$f" 2>/tmp/gate-syn.$$ || {
          echo "[commit-gate] BLOCKED: $f does not parse as Python:" >&2
          cat /tmp/gate-syn.$$ >&2; rm -f /tmp/gate-syn.$$; exit 2; }
        rm -f /tmp/gate-syn.$$ ;;
    esac
  done
fi

# GUARDS ARE NOT EXEMPT FROM BEING CHECKED.
#
# The early exit below waves through any commit that touches nothing under
# backend/ or frontend/ — correct for docs, but it also means the hooks and
# workflows that ENFORCE everything else commit with zero verification. Three
# guards shipped broken on 2026-08-11 for exactly this reason: a size check that
# swallowed its own exit code, a sed range whose anchor had been deleted, and a
# drift rule blind to the file carrying its bug. All three were syntactically
# valid, so this catches a narrower class — but a YAML or bash parse error in a
# guard is silent until the loop next runs, which can be a day later.
GUARD_FILES="$(git status --porcelain -uall -- .claude/hooks .github/workflows scripts \
               | awk '{print $NF}')"
if [ -n "$GUARD_FILES" ]; then
  for f in $GUARD_FILES; do
    [ -f "$ROOT/$f" ] || continue
    case "$f" in
      *.sh)
        bash -n "$ROOT/$f" 2>/tmp/gate-syn.$$ || {
          echo "[commit-gate] BLOCKED: $f is not valid bash:" >&2
          cat /tmp/gate-syn.$$ >&2; rm -f /tmp/gate-syn.$$; exit 2; }
        rm -f /tmp/gate-syn.$$ ;;
      *.yml|*.yaml)
        python -c "import sys,yaml;yaml.safe_load(open(sys.argv[1],encoding='utf-8'))" \
          "$ROOT/$f" 2>/tmp/gate-syn.$$ || {
          echo "[commit-gate] BLOCKED: $f is not valid YAML:" >&2
          cat /tmp/gate-syn.$$ >&2; rm -f /tmp/gate-syn.$$; exit 2; }
        rm -f /tmp/gate-syn.$$ ;;
      *.py)
        python -c "import sys,ast;ast.parse(open(sys.argv[1],encoding='utf-8').read())" \
          "$ROOT/$f" 2>/tmp/gate-syn.$$ || {
          echo "[commit-gate] BLOCKED: $f does not parse as Python:" >&2
          cat /tmp/gate-syn.$$ >&2; rm -f /tmp/gate-syn.$$; exit 2; }
        rm -f /tmp/gate-syn.$$ ;;
    esac
  done
fi

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
