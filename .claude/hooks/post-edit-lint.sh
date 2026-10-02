#!/usr/bin/env bash
# post-edit-lint.sh — PostToolUse(Edit|Write|MultiEdit). Lints the ONE file just edited.
#
# backend/**/*.py -> ruff check --fix, then report what is left
#
# Exit 2 = the leftovers are fed back to the model so it fixes them now, while the
# file is still in its head. The edit itself is never undone. Any tool error exits 0:
# a lint hook must never wedge a session. Kill switch: .claude/QUALITY-GATE-OFF
set -uo pipefail
[ -n "${GITHUB_ACTIONS:-}${CI:-}" ] && exit 0

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
[ -f "$ROOT/.claude/QUALITY-GATE-OFF" ] && exit 0

REL="$(python -c "
import json, os, sys
try:
    p = json.load(sys.stdin).get('tool_input', {}).get('file_path', '')
    if p:
        r = os.path.relpath(os.path.abspath(p), os.path.abspath(sys.argv[1]))
        print(r.replace(os.sep, '/'))
except Exception:
    pass
" "$ROOT" 2>/dev/null)"
[ -n "$REL" ] || exit 0
case "$REL" in ../*) exit 0 ;; esac
[ -f "$ROOT/$REL" ] || exit 0

OUT=""
case "$REL" in
  backend/*.py)
    cd "$ROOT/backend" || exit 0
    F="${REL#backend/}"
    python -m ruff check --fix --quiet "$F" >/dev/null 2>&1
    RC=0
    OUT="$(python -m ruff check --output-format concise "$F" 2>&1)" || RC=$?
    # ruff: 0 clean, 1 findings, 2+ ruff itself failed. A failure must not look
    # like "clean" — say so (exit 1 = shown, never blocks), still fail open.
    if [ "$RC" -ge 2 ] || { [ "$RC" -ne 0 ] && [ -z "$OUT" ]; }; then
      echo "[post-edit-lint] ruff could not lint $REL (exit $RC) — lint SKIPPED, not passed: $(printf '%s' "$OUT" | head -3)" >&2
      exit 1
    fi
    ;;
  # frontend: NOT here. eslint on ONE file measured 96 s on this machine (2026-10-02)
  # — far too slow per edit. Frontend lint + type-check + unit tests run once at the
  # Stop gate instead (stop-quality-gate.sh -> scripts/agent-gate.sh).
  *) exit 0 ;;
esac

# ruff prints "All checks passed!" / nothing when clean; eslint prints nothing.
if [ -n "$OUT" ] && ! printf '%s' "$OUT" | grep -q "All checks passed"; then
  {
    echo "[post-edit-lint] $REL still has lint problems after auto-fix. Fix them now:"
    printf '%s\n' "$OUT" | head -30
  } >&2
  exit 2
fi
exit 0
