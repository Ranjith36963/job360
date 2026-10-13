# 360-hunt — find jobs that fit, and record them
<!-- doc: REFERENCE — a run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Print this checklist and tick each step:
[ ] 1 Targets  [ ] 2 Plan  [ ] 3 Search  [ ] 4 Judge  [ ] 5 Bring  [ ] 6 Report

Job360 never searches, ranks or recommends jobs. YOU search, with your own
tools (a job search connector, company career pages, the web, links the user
pastes). Then you bring only the good ones into Job360.

1. **Read the targets.** Call `get_profile`: target titles, level,
   `search_brief`, `assistant_notes`. Call `list_applications` so you never
   bring a job twice. `search_brief.gaps` not empty: ask each `ask` once,
   then save it to the `path` that gap names with `update_profile`. For
   `user_info.answers` save `{question: <the ask text exactly>, answer: "Yes"|"No",
   approved: true}` only after the user answers. Read again.
   **Done when** you hold titles, the brief with no gaps, and the list. STOP if
   `search_brief.rows` is empty: ask the user before you search.
2. **Plan.** Print one line per `search_brief.rows` row: the place, its
   `counts_if` lines, the titles, and the sources you will try there (official
   job connectors and employers' own job boards first; then that country's own
   job sites, public pages, no login; sites the user named in
   `assistant_notes`; the user's `preferences.target_companies`: read each
   company's public job board - Greenhouse
   `boards-api.greenhouse.io/v1/boards/<co>/jobs`, Lever
   `api.lever.co/v0/postings/<co>`, Ashby
   `api.ashbyhq.com/posting-api/job-board/<co>`; `<co>` is the employer's board token, not its display name: read it off the careers page link, e.g. boards.greenhouse.io/<token>, jobs.lever.co/<site>, jobs.ashbyhq.com/<name>). **Where** comes ONLY from those rows: a place that is not
   on that list is out, even if it is where the user lives today. A job counts
   only if one of its row's `counts_if` lines is true. **Done when** every row
   has a line.
3. **Search** row by row. Use an official job connector first (e.g. Indeed's
   connector in Claude). Prefer the employer's own posting over a re-post; from
   a LinkedIn or Indeed ad follow "Apply on company site" and read the
   employer's posting. A page reader that returns nothing: use the employer's
   page instead. Never
   log in to LinkedIn or Indeed to search unless the user said yes to that site
   in this chat, after you told them "the site may restrict your account":
   then only read, at most 10 pages, stop at the first CAPTCHA, and never on a
   scheduled run. **Handing off:** a helper gets `get_recipe("hunt")` and
   `search_brief` word for word plus one line naming its rows, never your own
   summary; check every job it returns against the rows before you bring it.
   **Done when** every row was searched.
4. **Judge each ad yourself.** Read the whole ad, and the `checks` that
   `bring_job` / `get_job` return: `title_only` or `partial` text - fetch the
   full ad first; unknown apply host - tell the user; `reposted` - say so. Does it fit the profile? Visa:
   does the ad offer sponsorship, refuse it, or not say? Never guess - "not
   mentioned" is an answer. Duplicate hard stop: a job already in
   `list_applications`, or with the kit's `duplicate.flag`, is not brought again.
5. **Bring only the fits.** For each one: `bring_job` (title, company,
   location, full description, apply URL, and `posted_at` / `employer_published_at`
   as YYYY-MM-DD when the ad shows them) and ALWAYS the job facts you know:
   `country` (ISO code, e.g. `FR`), `remote` (true/false) and `found_on`
   (`indeed`, `linkedin`, `company_careers`, `job_board`, `referral`,
   `visa_sponsor_list`, `pasted_by_user` or `other`). Leave out only what you
   truly do not know; fix it later with `update_job`. Then `save_fit` with your
   score, your verdict in one sentence, and the gaps. Skip the rest silently -
   do not bring a job just to reject it. **Done when** each fit is saved.
   Blocked (a site will not open, a tool is missing): `ask_user`, a Needs-you
   note, then continue with the next job.
6. **Report.** First the coverage table, one line per row and source: place |
   source | search used | ads seen | ads brought | why none. A row with no line
   means the hunt is not done. More than 7 in 10 finds from one place while
   the brief has more rows: say so and search the thin rows again. Then two
   lists - "Above your line (N)" and "Below your line (N)"
   (`settings.apply_min_score.effective`; null = ask for it first): company,
   title, place, your score, visa answer, and the link to the Job360
   application page. Inside `run 360 daily`, apply per each job's
   `save_fit` `line.switch` with `get_recipe("apply")` (the kit first); `ask`
   means ask. Alone, ask which ones to apply to.
