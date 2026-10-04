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
5. **Sending depends on the mode.** Read `preferences.daily_check` from
   `get_profile`.
   - `auto` (or the older `scheduled`): you may send this message from the
     user's own Gmail (your own connector) — only if you saved it with
     channel `email` and the contact has an email address. First make sure it
     was not sent already: look in the user's Gmail Sent folder for a message
     to that address since this version was saved, and check `list_people`
     for a `sent` mark on this contact recorded after this version's
     `recorded_at`. If either exists, do not send. Right after sending,
     record `outreach_sent` with `record_event` (`contact_id`, `channel`, and
     the Gmail message id as `source`).
   - `ask`, `paused`, `declined` or empty: draft only. The user sends it.
     Never send it yourself. Only after the user says it went out, record
     `outreach_sent` with `record_event` (`contact_id` and `channel`).
   - Any mode: a LinkedIn message is always sent by the user. Never reply to
     an answer; just record it. Never submit a job application without the
     user's yes for that one application.
