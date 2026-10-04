# 360-reach — reach a person at the company
<!-- doc: REFERENCE — a /run 360 recipe the connected assistant reads; served by routes/recipes.py -->

1. **Find the right person** for this application (recruiter, hiring manager,
   someone on the team) — start with the company's own site and the job ad.
2. **Check first:** `list_people` so you never add someone twice.
3. **Record them:** `add_contact` with the `application_id`, their `role`,
   and `found_via` — where you found them: `company_site`, `linkedin`,
   `apollo`, `referral`, `job_ad`, `email`, `event` or `other`. Put any detail
   (the page, the post) in `notes`.
4. **Write the message yourself** — short, specific to this job, true facts
   only. Save it with `save_artifact` (`contact_id`, kind `outreach`, channel
   `email`, `linkedin` or `other`). This is a draft.
5. **The user sends it.** Never send it yourself. Only after the user says it
   went out, record `outreach_sent` with `record_event` (`contact_id` and
   `channel`).
