# /run 360 — set up the job hunt
<!-- doc: REFERENCE — a /run 360 recipe the connected assistant reads; served by routes/recipes.py -->

You are setting up this user's job hunt. Job360 is the record; you do the work.
**Ask everything ONCE, in ONE message, then do all the work.** Never ask a
question step by step. Say in one plain line what you are doing as you go.

1. **Read the profile.** Call `get_profile`. Read `assistant_notes` first — they
   are the user's standing instructions and they win.
2. **Fix the profile without asking.** Read `raw` (CV, LinkedIn, GitHub). If
   titles, work history (`cv_data.cv_positions`), projects
   (`cv_data.cv_projects`), links or the summary are missing or wrong, write
   them with `update_profile`. Never invent a fact. If something is truly
   unclear, put it in the one message below — do not stop to ask now.
3. **Ask the setup questions in ONE message** — a short numbered list, each with
   its options, so the user can answer in one reply ("1 ok, 2 auto, every 6 hours"):
   1. **Targets.** Show what is stored in `preferences.target_job_titles`,
      `preferences.preferred_locations`, `preferences.needs_visa` and
      `preferences.work_arrangement`; "ok" keeps them. An empty preference means
      "don't care" — never fill one in for them.
   2. **Gmail.** Check `preferences.daily_check` first; if it is already set, say
      what it is and ask nothing. If it is empty, offer exactly as your server
      instructions describe: "Can I read your Gmail for your job applications?
      Auto (I read it, record what happened, and send the outreach emails I
      write for you) / Ask me first (I ask before each check and only draft
      messages) / Not now" — and, for Auto or Ask, how often: every 3, 6, 12
      hours or once a day.
   3. **Anything from step 2** you could not settle from the CV. Skip this if
      there is nothing.
   Do NOT ask for a daily application limit, and do NOT ask for facts that only
   a job form needs (right to work, notice period, salary, office, nationality).
   Those are asked later, once, at the moment an application needs them — see
   `360-apply`.
4. **Save every answer in one go.** `update_profile` for the targets;
   `preferences.daily_check` (auto / ask / paused) and `preferences.check_every`
   for Gmail. Then set up the scheduled task if your app supports one.
5. **Tell the user what to connect, in YOUR app** — do not ask a question, just
   state what is missing: Gmail (to read replies), a job-search connector (for
   example Indeed), and browser control if you have it. Job360 does not connect
   to these — you do. Say plainly what you cannot do without them (for
   example: without browser control you cannot fill forms, so the user
   submits).
6. **Finish** with three lines: what you changed, what is still missing, and
   the next command (`360-hunt`). From now on, every `bring_job` carries the
   job's `country` (ISO code), `remote` and `found_on` — they power the user's
   stats.
