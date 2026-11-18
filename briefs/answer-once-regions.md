From Kits (the owner's coordinating session). The owner pasted this himself on 2026-11-18.

BUG (seen live on job360.uk → Needs you → "Answer once"):
- The group heading shows the raw code "NORTH_AMERICA".
- The questions read "Are you legally allowed to work in NORTH_AMERICA?", "Expected salary in NORTH_AMERICA" and "Willing to relocate within NORTH_AMERICA?".
- Two things are wrong: the raw code is shown instead of its name, and work rights are asked for a whole region. Work rights are per country (owner rule), so no real form asks this.

WHERE TO LOOK:
- backend/src/services/profile/regions.py: the region codes, and the names map with "NORTH_AMERICA": "North America".
- frontend/src/lib/regions.ts.
- The Answer once card on Needs you, and the backend that builds its groups and questions (find it from the frontend component).

TASK, permanent-fix style:
1. Reproduce the bug with a failing test: an Answer-once group for a region shows its code and asks per-region work rights.
2. Find the root cause. Why does a region become an Answer-once "country", and where is the code-to-name lookup skipped?
3. Fix it:
   - every heading and question shows the name ("North America"), never the code;
   - work-rights and sponsorship questions are asked per country only, never per region;
   - for a region, ask only questions that make sense per region, if any (for example salary range or relocation). If none do, don't make a region group at all.
4. Add a log line for any group that is dropped or renamed.

RULES:
- Read .claude/skills/session-lessons/SKILL.md and .claude/rules/permanent-fixes.md first.
- Never write to the owner's live Job360 account. Reads are fine.
- Tests go red on origin/main, then green.
- Run ruff, mypy_ratchet, the targeted tests, frontend lint, type-check and test:unit, the gate as its own command, and test_integrity.
- One PR, under 1000 read lines. Enable auto-merge.
- Do not touch frontend/src/components/needs-you/RunNowPanel.tsx or components/connect/DutyStartLinksCard.tsx. Kits has a PR there.

WHEN DONE, message Kits (session_011rjs4Ek7CHxQibr8jtZjDu) with:
- your PR number;
- the root cause (file:line);
- the red-then-green test names.
