# run 360 — set up the job hunt
<!-- doc: REFERENCE — a run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Print this checklist and tick each step:
[ ] 0 Resume  [ ] 1 Fix profile  [ ] 2 Six rounds (you, visa, logistics,
equality, targets, settings)  [ ] 3 Connect  [ ] 4 Watched daily run
[ ] 5 Schedule  [ ] 6 Finish

You are setting up this user's job hunt. Job360 is the record; you do the
work. Plain words, one line on what you are doing. Full rules (settings,
memory shapes, Gmail modes): `get_recipe("rules")`.

0. **Resume.** `get_profile`: read `assistant_notes`, `fields` and
   `settings.setup_progress`. Rounds done: say "N of 6 done" and **resume** at
   the first unfinished round. Never ask what `fields` holds - show it and ask
   "still right?". **Done when** you know the first unfinished round.
   All six done: jump to step 4.
1. **Fix the profile from `raw` (CV, LinkedIn, GitHub), no questions.** Write
   titles, `cv_data.cv_positions`, `cv_data.cv_projects`, links and summary with
   `update_profile` when missing or wrong. Never invent a fact; anything truly
   unclear is asked in round 1. **Done when** the profile matches `raw`.
2. **Six rounds**, one topic each, 3-5 questions per round, follow-ups until
   clear: (1) you - `user_info.contact`; (2) visa - `user_info.right_to_work`
   per country, citizenship, and the sanctions question (ask it once; "prefer
   not to say" is allowed; store `sanctions_country_citizen`; reuse it only
   when a form asks); (3) logistics - notice, start, per-country relocate,
   travel, licence, `user_info.languages`; (4) equality - `user_info.equality`;
   (5) targets - titles, `preferences.preferred_locations`,
   `preferences.work_arrangement`, `preferences.needs_visa`,
   `preferences.salary_by_country` (an empty preference means "don't care" -
   never fill one in for them); (6) settings - see below.
   Ask visa, salary and equality questions **one at a time**. Salary is a
   range per country (min and max; the same figure twice if they have one) in
   its own currency and period, never converted. Equality: skipping
   is fine, "prefer not to say" is a real answer, a skip still marks the round
   done; **if the user skips, ask again only on the first form that needs it**.
   **Read back** the answers; on OK **save after each round** (the round's
   paths plus `assistant_settings.setup_progress` with that round's `done_at`),
   then print "N of 6 done". The user may stop any time: "type run 360 to
   continue". **Done when** the round is saved. STOP if the user stops.
   Round 6 asks: *Applying* - `ask_each` (default) / `apply_all` /
   `selective_above_score` (a score line; `settings.apply_min_score.effective`,
   default 75). *Submitting* - `confirm` (default) / `auto_when_sure` (you submit
   when `check_submit` says go; Indeed and LinkedIn always ask; the first one is
   a practice run). *A daily limit* - optional: a number or none (no cap by
   default; never assume a number). *Gmail* - the offer and `check_every` from
   rules (only if `preferences.daily_check` is empty). And: "turn on form
   filling (browser control) in your own app". Save with
   `assistant_settings.apply_mode`, `assistant_settings.apply_min_score`,
   `assistant_settings.submit_mode`, `assistant_settings.daily_cap`,
   `preferences.daily_check`, `preferences.check_every`.
   **Waiting for your OK:** a choice that gives you MORE freedom (`apply_all`, a
   lower score line, `auto_when_sure`, a higher or removed limit, Gmail `auto`)
   is NOT applied: it comes back in `waiting`. Tell the user: "Open Job360,
   Needs you, and click Confirm once. I cannot confirm it for you; until then
   the safe setting stands." Safer choices apply at once.
3. **Tell the user what to connect in their app:** Gmail (to read replies), a
   job-search connector (for example Indeed), browser control. Job360 does not
   connect to these - you do. Say what you cannot do without them (no browser
   control = the user submits). **Done when** you said it.
4. **Watched first daily run.** Follow `get_recipe("daily")` now with the user
   watching; they approve each tool once. **Done when** it printed its report.
5. **Schedule.** Claude Code: a routine. ChatGPT: a Task. claude.ai: tell the
   user to type "run 360 daily". Hour rule: `get_recipe("rules")`, Gmail.
6. **Finish** with three lines: what changed, what is missing, next "run 360
   hunt". Every `bring_job` carries the job's `country` (ISO code), `remote` and
   `found_on` - they power the user's stats.
