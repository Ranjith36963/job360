#!/usr/bin/env bash
# Stop hook — remind to run /verify-job360 when app code changed but isn't verified.
#
# Fires only when there are uncommitted changes to code under backend/src or
# frontend/src. Non-blocking: it surfaces a reminder via systemMessage and lets
# the turn end normally (a blocking Stop hook on dirty code would loop forever).
# If it ever gets noisy or wrong, edit or delete this file, or disable via /hooks.
root="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
changed="$(git -C "$root" status --porcelain -- backend/src frontend/src 2>/dev/null | grep -E '\.(py|ts|tsx|js|jsx)$')"
if [ -n "$changed" ]; then
  printf '%s' '{"systemMessage":"⚠️ App code changed but not verified yet. Run /verify-job360 to prove it works (browser + DB + logs) before committing."}'
fi
