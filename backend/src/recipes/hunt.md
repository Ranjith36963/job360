# 360-hunt — find jobs that fit, and record them
<!-- doc: REFERENCE — a /run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Job360 never searches, ranks or recommends jobs. YOU search, with your own
tools (a job search connector, company career pages, the web, links the user
pastes). Then you bring only the good ones into Job360.

1. **Read the targets.** Call `get_profile`: target titles, locations,
   `needs_visa`, `assistant_notes`. Call `list_applications` so you never bring
   a job twice.
2. **Search** with your own tools for those titles and places. Prefer the
   employer's own posting over a re-post. **Where** comes ONLY from
   `preferred_locations` (and `assistant_notes`): a place that is not on that
   list is out, even if it is where the user lives today. A remote role counts
   only if the ad lets them work from where they are. If the list is empty,
   ask the user before you search.
3. **Judge each ad yourself.** Read the whole ad. Does it fit the profile? If
   the user needs visa sponsorship, does the ad offer it, refuse it, or not
   say? Never guess — "not mentioned" is an answer.
4. **Bring only the fits.** For each one: `bring_job` (title, company,
   location, full description, apply URL) and ALWAYS the job facts you know:
   `country` (ISO code, e.g. `FR`), `remote` (true/false) and `found_on`
   (`indeed`, `linkedin`, `company_careers`, `job_board`, `referral`,
   `visa_sponsor_list`, `pasted_by_user` or `other`). Leave out only what you
   truly do not know; fix it later with `update_job`. Then `save_fit` with your
   score, your verdict in one sentence, and the gaps.
5. **Skip the rest silently** — do not bring a job just to reject it.
6. **Report** a short list: company, title, place, your score, visa answer,
   and the link to the Job360 application page. Ask which ones to apply to.
