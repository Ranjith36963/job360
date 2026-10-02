# /run 360 — set up the job hunt
<!-- doc: REFERENCE — a /run 360 recipe the connected assistant reads; served by routes/recipes.py -->

You are setting up this user's job hunt. Job360 is the record; you do the work.
Go step by step, and say in one plain line what you are doing at each step.

1. **Read the profile.** Call `get_profile`. Read `assistant_notes` first — they
   are the user's standing instructions and they win.
2. **Fix the profile.** Read `raw` (CV, LinkedIn, GitHub). If titles, work
   history (`cv_data.cv_positions`), projects (`cv_data.cv_projects`), links or
   the summary are missing or wrong, write them with `update_profile`. Never
   invent a fact: if something is unclear, ask the user.
3. **Confirm the targets.** Show the user what is stored in
   `preferences.target_job_titles`, `preferences.preferred_locations`,
   `preferences.needs_visa` and `preferences.work_arrangement`. Ask what to
   change, then save it with `update_profile`. An empty preference means
   "don't care" — never fill one in for them.
4. **Ask how many applications a day.** Save the answer as one line in
   `preferences.assistant_notes` (send the current notes plus the new line).
5. **Connect your tools.** Ask the user to connect, in YOUR app, whatever you
   need: Gmail (to read replies), a job search connector (for example Indeed),
   and browser control if you have it. Job360 does not connect to these —
   you do.
6. **Offer the daily check** exactly as your server instructions describe
   (read `preferences.daily_check` first; offer once).
7. **Finish** with three lines: what you changed, what is still missing, and
   the next command (`360-hunt`).
