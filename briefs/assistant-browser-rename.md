From Kits (the owner's coordinating session). The owner pasted this himself on 2026-11-18. The task is his decision.

TASK: rename "own browser" to "assistant browser" in every user-visible place.

The owner's naming, final:
- "Assistant browser (your Chrome)" = the user's own Chrome on their PC, driven by their assistant (Claude in Chrome).
- "Job360 browser" = Job360's server browser. Keep that name as it is.

WAIT before starting: Kits has a PR in flight on branch `fix/cv-start-link`. It touches frontend/src/components/needs-you/RunNowPanel.tsx, components/connect/DutyStartLinksCard.tsx and backend/src/services/browser_agent/duty_trigger.py. Start once Kits tells the owner it is merged. If you start sooner, merge main in step by step after it lands, and resolve every conflict hunk by hunk.

WHAT TO CHANGE:
1. Every user-visible "own browser", "own-browser" or "your own browser" becomes "assistant browser". This covers all frontend text: buttons, headings, toasts, empty states and aria-labels. Find the files with `grep -rlni "own browser\|own-browser" frontend/src` (about 18 files). Change it in backend text a user sees too: labels in services/browser_agent/routing.py (e.g. LINKEDIN_LABEL "Your own browser: runs when your PC is on"), ask/needs-you texts, and API `why`/`text` strings. Example: the button "Run my own-browser tasks now" becomes "Run my assistant-browser tasks now".
2. DO NOT rename code identifiers, routes, table or duty names, MCP tool names or recipe names (own_browser, own_browser_queue, /api/own-browser/*, get_recipe("own_browser")). Assistants depend on them.
3. Recipe prose: leave it alone, unless a recipe tells the assistant to show the user a sentence that contains "own browser".
4. Update the tests that assert the old text, without loosening any assert. Add one frontend test proving the button "Run my assistant-browser tasks now" calls the run-now route, and that it starts the assistant-browser start link when one is saved. That second part is the link added by `fix/cv-start-link`.

RULES:
- Read .claude/skills/session-lessons/SKILL.md and .claude/rules/permanent-fixes.md first.
- Never write to the owner's live Job360 account.
- From backend/, run ruff, scripts/mypy_ratchet.py, test_recipes, test_tools_states_recipes and the targeted tests.
- From frontend/, run `npm ci` (if node_modules is stale), lint, type-check and test:unit.
- Run `bash scripts/agent-gate.sh` as its own command, then test_integrity.
- One PR, under 1000 read lines. Enable auto-merge.

WHEN DONE, message Kits (session_011rjs4Ek7CHxQibr8jtZjDu) with:
- your PR number;
- the files changed;
- a grep showing no user-visible "own browser" is left (code identifiers excepted).
