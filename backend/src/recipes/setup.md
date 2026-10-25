# run 360 — set up the job hunt
<!-- doc: REFERENCE — a run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Print this checklist and tick each step:
[ ] 0 Resume  [ ] 1 Six rounds (targets, you, visa, logistics, equality, settings)
[ ] 2 Fix profile  [ ] 3 Check your tools  [ ] 4 Watched daily run  [ ] 5 Schedule  [ ] 6 Finish

This is an INTERVIEW: you ask, the user answers, you save their words. Never answer for them from the CV.
Job360 is the record. Every edit holding the user's own answer: `"source": "user_said"` (`agent_inferred` only
for what you worked out yourself). Plain words. Rules: `get_recipe("rules")`. If a Job360 tool ignores or refuses an argument you expected, call doctor: a parameter it lists that your tool list lacks means your connector is stale; tell the user to reconnect Job360.

0. **Resume.** `get_profile`: read `assistant_notes`, `fields`, `settings.setup_progress`.
   Say "N of 6 done", **resume** at `setup_progress.next`; a field already set:
   show it, ask "still right?". All six done: jump to step 3.
1. **Six rounds, in this order.** 3-5 questions a round; visa, salary and equality
   one at a time; follow up until clear. An empty preference means "don't care" - never fill one in for them.
   (1) **targets** - ask EVERY one: a. roles -> `preferences.target_job_titles`
   (REQUIRED); b. level and years -> `preferences.experience_level` (entry|mid|
   senior|lead|executive) + `preferences.experience_years_min`/`_max` (their exact
   range, e.g. 1 and 3); c. countries/cities -> `preferences.preferred_locations`;
   d. WHERE to search, per country ("ANY" = every other country): remote OK
   (`remote_ok`)? on-site (`onsite`: yes|only_if_sponsored|no)? -> `user_info.right_to_work`
   + `preferences.work_arrangement`; "anywhere" is not a place: ask which, or ANY;
   e. salary -> `preferences.salary_by_country`, a min-max range in its own
   currency and period, never converted; f. what they look for ->
   `preferences.looking_for`; g. dealbreakers -> `preferences.dealbreakers`;
   h. companies to avoid -> `preferences.avoid_companies`; i. **score line**: "I give
   each job a fit score from 0 to 100. Which score is your line? I will show you
   jobs above and below it separately." -> `assistant_settings.apply_min_score`
   (REQUIRED; never suggest or assume a number).
   (2) **you** - `user_info.contact`. (3) **visa** - `user_info.right_to_work` per
   country, citizenship, the sanctions question once ("prefer not to say" is
   allowed; store `sanctions_country_citizen`; reuse it only when a form asks, and confirm it then).
   (4) **logistics** - notice, start, per-country relocate, travel, licence,
   `user_info.languages`. (5) **equality** - `user_info.equality`; a skip still
   marks the round done; if the user skips, ask again only on the first form that
   needs it; a stored one is still confirmed on each form. (6) **settings** - one at a time: *Jobs at or above your line* - ask me
   / apply on its own (`assistant_settings.above_line` = ask | auto); *jobs below
   your line* - ask me / drop it (`assistant_settings.below_line` = ask | skip); not chosen = ask.
   *Submitting* - `confirm` (default) / `auto_when_sure` (you submit when
   `check_submit` says go; Indeed and LinkedIn always ask; the first is a practice
   run) -> `assistant_settings.submit_mode`. *A daily limit* - a number or none, no
   cap by default, never assume a number -> `assistant_settings.daily_cap`.
   *Gmail* - the offer and `check_every` from rules -> `preferences.daily_check`
   (REQUIRED; "not now" = `paused`, a real answer), `preferences.check_every`. And: "turn on
   form filling (browser control) in your own app".
   **Read back**, then on OK save after each round in ONE `update_profile` call: its paths
   (each with `"source": "user_said"`; a sensitive one - visa / right-to-work, salary, equality - only after you ask "may I remember this for future applications?", and on a yes add `"remember": true` to that edit; on a no it is NOT saved, use it this session only) plus `assistant_settings.setup_progress` with that round's `done_at`. No clock? Use `now` from
   `whats_new` (a future time is refused). Job360 refuses `targets` done without roles, a score
   line and a place to search, and `settings` done without a Gmail answer: ask what is missing. A refused save saved NOTHING: fix the named path, resend ALL
   paths. Print "N of 6 done". The user may stop: "type run 360 to continue". **Done when** the
   round is saved. STOP if they stop. **Waiting for your OK:** more freedom for you (a side
   set to `auto`, a score line that moves jobs onto an `auto` side, `auto_when_sure`, a higher or
   removed limit, Gmail `auto`) is NOT applied; it comes back in `waiting`. Say: "Open Job360,
   Needs you, and click Confirm once. I cannot confirm it for you; until then the safe setting
   stands." Safer choices apply at once. A round needing the user's OK can't be marked done in
   the same call: resend WITHOUT setup_progress (parked for Confirm), mark it done after.
2. **Fix the profile from `raw`** (`get_profile(include_raw=true)`: CV, LinkedIn,
   GitHub). Write `cv_data.cv_positions`, `cv_data.cv_projects` (one per real
   GitHub project), links, summary when missing or wrong (`"source": "agent_inferred"`: you
   read these). Never invent a fact; never overwrite a target the user gave.
3. **Check your tools.** From your OWN tool list, call `report_capabilities` (which of gmail, calendar,
   job_search, people_lookup... you have, and any needing sign-in); tell the user what it says is missing.
   Job360 never connects these - you do; their Home shows the links. **Done when** you reported and said it.
4. **Watched first daily run.** Follow `get_recipe("daily")` with the user
   watching. **Done when** it printed its report.
5. **Schedule.** Claude Code: a routine. ChatGPT: a Task. claude.ai: tell the user
   to type "run 360 daily". Hour rule: `get_recipe("rules")`, Gmail.
6. **Finish** with three lines: what changed, what is missing, next "run 360
   hunt". Every `bring_job` carries `country` (ISO code), `remote`, `found_on`.
