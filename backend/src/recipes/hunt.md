# 360-hunt — find jobs that fit, and record them

Job360 never searches, ranks or recommends jobs. YOU search, with your own
tools (a job search connector, company career pages, the web, links the user
pastes). Then you bring only the good ones into Job360.

1. **Read the targets.** Call `get_profile`: target titles, locations,
   `needs_visa`, `assistant_notes`. Call `list_applications` so you never bring
   a job twice.
2. **Search** with your own tools for those titles and places. Prefer the
   employer's own posting over a re-post.
3. **Judge each ad yourself.** Read the whole ad. Does it fit the profile? If
   the user needs visa sponsorship, does the ad offer it, refuse it, or not
   say? Never guess — "not mentioned" is an answer.
4. **Bring only the fits.** For each one: `bring_job` (title, company,
   location, full description, apply URL), then `save_fit` with your score,
   your verdict in one sentence, and the gaps.
5. **Skip the rest silently** — do not bring a job just to reject it.
6. **Report** a short list: company, title, place, your score, visa answer,
   and the link to the Job360 application page. Ask which ones to apply to.
