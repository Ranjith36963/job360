# 360-hunt — find jobs that fit, and record them
<!-- doc: REFERENCE — a run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Print this checklist and tick each step:
[ ] 1 Targets  [ ] 2 Search  [ ] 3 Judge  [ ] 4 Bring  [ ] 5 Skip  [ ] 6 Report

Job360 never searches, ranks or recommends jobs. YOU search, with your own
tools (a job search connector, company career pages, the web, links the user
pastes). Then you bring only the good ones into Job360.

1. **Read the targets.** Call `get_profile`: target titles, locations,
   `needs_visa`, `assistant_notes`. Call `list_applications` so you never bring
   a job twice. **Done when** you hold the targets and the list. STOP if the
   list of places is empty: ask the user before you search.
2. **Search** with your own tools for those titles and places. Prefer the
   employer's own posting over a re-post; a job on Indeed or LinkedIn: bring the
   employer's own posting instead. **Where** comes ONLY from
   `preferred_locations` (and `assistant_notes`): a place that is not on that
   list is out, even if it is where the user lives today. A remote role counts
   only if the ad lets them work from where they are. **Done when** you have a
   candidate list.
3. **Judge each ad yourself.** Read the whole ad. Does it fit the profile? If
   the user needs visa sponsorship, does the ad offer it, refuse it, or not
   say? Never guess - "not mentioned" is an answer. Duplicate hard stop: a job
   already in `list_applications`, or with the kit's `duplicate.flag`, is not
   brought again.
4. **Bring only the fits.** For each one: `bring_job` (title, company,
   location, full description, apply URL) and ALWAYS the job facts you know:
   `country` (ISO code, e.g. `FR`), `remote` (true/false) and `found_on`
   (`indeed`, `linkedin`, `company_careers`, `job_board`, `referral`,
   `visa_sponsor_list`, `pasted_by_user` or `other`). Leave out only what you
   truly do not know; fix it later with `update_job`. Then `save_fit` with your
   score, your verdict in one sentence, and the gaps. **Done when** each fit is
   saved. Blocked (a site will not open, a tool is missing): `ask_user`, a
   Needs-you note, then continue with the next job.
5. **Skip the rest silently** - do not bring a job just to reject it.
6. **Report** a short list: company, title, place, your score, visa answer, and
   the link to the Job360 application page. Inside `run 360 daily`, do not ask
   which to apply to: apply per `settings.apply_mode.effective` with
   `get_recipe("apply")` (the kit first). Alone, ask which ones to apply to.
