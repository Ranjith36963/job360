#!/usr/bin/env bash
# triage_trusted_origin.sh — may triage.yml auto-authorise `agent:fix` on this issue?
#
# Exit 0 = trusted origin. Exit 1 = not trusted (a human must click).
# Usage:  bash scripts/triage_trusted_origin.sh <author-login> <issue-title>
#
# ── WHY THIS IS ITS OWN FILE ─────────────────────────────────────────────────
# This predicate is the ONLY authorisation control on a write-capable agent
# (repair.yml). It lived inline in triage.yml, where nothing could execute it,
# so nothing ever tested it — and it was wrong for 15 days without a sound.
# Out here it is a program with a test (scripts/harness_selftest.sh).
#
# It is deliberately NOT under backend/ or frontend/, so repair.yml's path cage
# ("only backend/** and frontend/** may change", repair.yml:365) still forbids
# the repair agent from widening its own authorisation. Moving the rule out of
# the YAML did not move it inside the cage.
#
# ── THE BUG THIS FIXES (measured 2026-08-15) ─────────────────────────────────
# GitHub spells the SAME bot two different ways, and both spellings are correct
# in their own context:
#
#   "github-actions[bot]"  — the WEBHOOK EVENT payload spelling.
#                            `github.event.issue.user.login` (triage.yml:67).
#   "app/github-actions"   — the REST/GraphQL API spelling, which is what
#                            `gh issue view --json author --jq .author.login`
#                            returns, because the API reports the App actor.
#
# triage.yml gated on the webhook spelling but read the value through `gh`, so
# it compared "app/github-actions" against "github-actions[bot]" and could
# never match. Verified: all 30 issues in this repo report author
# "app/github-actions". Consequence, measured on live issues #220 and #270 —
# both opened by our own detector, both titled "INVARIANT: ...", both judged
# auto-fixable by triage — the comment reads:
#
#   "Not auto-authorised: this issue did not come from a trusted detector."
#
# ...about an issue our own SQL wrote. `agent:fix` has been applied exactly
# ONCE in this repo's history (issue #156, 2026-07-27) and repair.yml has not
# run since. This one grep is why the repair loop went quiet.
#
# The fix ACCEPTS EITHER SPELLING. Swapping one for the other would just move
# the break to the other door: triage.yml:67 still gates on the webhook
# spelling and works. Both are real; both are the same bot.
#
# ── WHAT IS DELIBERATELY *NOT* LOOSENED ──────────────────────────────────────
# Matching is EXACT, on a closed set of two literals, via `case` with quoted
# patterns. It is not a substring, prefix or regex match. "github-actions" is
# a name a stranger can put in their own login ("app/github-actions-helper",
# "notgithub-actions[bot]"), and a substring match would hand a write-capable
# agent to anyone who registered one. The title whitelist is likewise a fixed
# set of prefixes, matched here in dumb bash, so no model — including triage
# itself — can widen it.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <author-login> <issue-title>" >&2
  exit 2
fi

author="$1"
title="$2"

# 1. OUR OWN BOT, in either of the two spellings GitHub uses for it.
#    Quoted `case` patterns are exact whole-string matches — no globbing.
case "$author" in
  "github-actions[bot]" | "app/github-actions") ;;
  *) exit 1 ;;
esac

# 2. A TRUSTED DETECTOR TITLE. Auto-authorisation rests on ONE property: the
#    issue body contains no model-generated and no attacker-supplied text.
#      PRODUCT / ABSENCE / WATCHDOG / INVARIANT — bodies are our own SQL counts
#        and fixed strings. They qualify.
#      "Loop 2 RED:" was REMOVED 2026-08-03 — its body is `tail -40` of the
#        LIVE pipeline log, i.e. text influenced by whatever an employer posts
#        on a job board we scrape. Auto-authorising it would let third-party
#        text start a WRITE-capable agent. Do not add it back.
case "$title" in
  "PRODUCT: "* | "ABSENCE: "* | "WATCHDOG: "* | "INVARIANT: "*) exit 0 ;;
  *) exit 1 ;;
esac
