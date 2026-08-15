#!/usr/bin/env bash
# post.sh — the whole body of the `slack` composite action.
#
# It lives in its own file, not inline in action.yml, for ONE reason: a guard
# you have never seen go red is not a guard. Six guards shipped in this repo
# this week that could not fire. Because this is a plain script, both drills
# (force a failure / prove the happy path) run the EXACT code the workflows
# run, from a laptop, with no GitHub Actions runner in the loop.
#
# Inputs, all via environment (set by action.yml):
#   SLACK_BOT_TOKEN   bot token. Blank => hard failure, on purpose.
#   SLACK_CHANNEL     channel NAME (preferred, readable) or ID. '#' optional.
#   SLACK_TITLE       one-line headline.
#   SLACK_BODY        the detail. Free text; may contain quotes/newlines.
#   SLACK_SEVERITY    critical | warn | info   (default: info)
#
# Exit codes:
#   0  Slack accepted the message (ok=true)
#   1  anything else — missing token, missing channel, transport error,
#      non-2xx, or ok=false. ALWAYS with the reason printed.
set -uo pipefail

fail() {
  # ::error:: makes it a red annotation in Actions; the plain echo makes the
  # same text readable when this script is run by hand during a drill.
  echo "::error::SLACK ALERT FAILED — $1"
  echo "SLACK ALERT FAILED — $1" >&2
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### ❌ Slack alert FAILED"
      echo
      echo "$1"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  exit 1
}

TOKEN="${SLACK_BOT_TOKEN:-}"
CHANNEL="${SLACK_CHANNEL:-}"
TITLE="${SLACK_TITLE:-}"
BODY="${SLACK_BODY:-}"
SEVERITY="${SLACK_SEVERITY:-info}"

# ── THE INVERSION ──────────────────────────────────────────────────────────
# `.github/actions/alert` (Resend/email) ends every failure path in `exit 0`
# with a ::warning::. That contract — "never fail the caller" — is exactly why
# this harness has been MUTE for weeks: RESEND_API_KEY was never added to
# Actions, so the composite skipped with a warning nobody read, and 7
# triage:needs-human issues sat unanswered for 3-8 days. For an ALERTING path
# a silent skip IS the bug: it manufactures confidence. So a missing token is
# a hard, red, unmissable failure here.
[ -n "$TOKEN" ]   || fail "SLACK_BOT_TOKEN is empty. The harness has no voice. Add the secret under Settings -> Secrets and variables -> Actions and pass it as the slack_bot_token input."
[ -n "$CHANNEL" ] || fail "channel input is empty — nothing to post to."
[ -n "$TITLE" ]   || fail "title input is empty — a message with no headline is noise."

# Channel names are passed as `log-github-ci`, not `#log-github-ci`; tolerate
# either so a caller that writes the '#' out of habit still works.
CHANNEL="${CHANNEL#\#}"

case "$SEVERITY" in
  critical) MARK="🔴 CRITICAL" ;;
  warn)     MARK="🟠 WARN" ;;
  info)     MARK="🔵" ;;
  *)        MARK="🔵" ; echo "::warning::unknown severity '$SEVERITY' — treated as info" ;;
esac

TEXT="$MARK  *${TITLE}*"
if [ -n "$BODY" ]; then
  TEXT="${TEXT}
${BODY}"
fi

# jq builds the JSON so a quote, newline or backtick anywhere in the body
# cannot break out of the payload. Bodies here carry model output, scraped log
# text and live page text — this is a real injection path, not a theoretical
# one.
command -v jq >/dev/null 2>&1 || fail "jq is not installed on this runner — cannot build the payload safely."
payload=$(jq -n --arg channel "$CHANNEL" --arg text "$TEXT" \
  '{channel: $channel, text: $text}') || fail "could not build the JSON payload."

resp_file="$(mktemp)"
code=$(curl -sS --max-time 25 -o "$resp_file" -w '%{http_code}' \
  -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json; charset=utf-8" \
  -d "$payload") || fail "curl could not reach slack.com/api/chat.postMessage (network/transport error)."

# Slack answers HTTP 200 even for application errors, so the HTTP code alone
# proves nothing — `ok` is the real verdict. Both are checked.
[ "$code" = "200" ] || fail "Slack returned HTTP $code. Response: $(head -c 300 "$resp_file")"

ok=$(jq -r '.ok // false' < "$resp_file" 2>/dev/null || echo "false")
if [ "$ok" != "true" ]; then
  err=$(jq -r '.error // "no error field in response"' < "$resp_file" 2>/dev/null || echo "unparseable response")
  detail=$(jq -r '(.response_metadata.messages // []) | join("; ")' < "$resp_file" 2>/dev/null || echo "")
  # The Slack error STRING is the whole diagnosis: channel_not_found,
  # not_in_channel, invalid_auth, missing_scope, ratelimited... Print it.
  fail "Slack rejected the message to channel '$CHANNEL' — error: $err${detail:+ ($detail)}"
fi

ts=$(jq -r '.ts // "?"' < "$resp_file" 2>/dev/null || echo "?")
echo "slack ok — posted to '$CHANNEL' (ts=$ts): $TITLE"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "slack -> \`#$CHANNEL\` ($SEVERITY): $TITLE" >> "$GITHUB_STEP_SUMMARY"
fi
rm -f "$resp_file"
exit 0
