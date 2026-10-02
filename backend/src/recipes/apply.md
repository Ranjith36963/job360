# 360-apply — decide, tailor, apply with the user, prove it
<!-- doc: REFERENCE — a /run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Use this for one application. The user confirms every submit. "Applied" is
only VERIFIED with evidence — your word alone is "claimed, unverified".

1. **Read.** `get_application` (the job, your fit verdict, research notes,
   earlier versions) and `get_profile`.
2. **Decide with the user.** Show the fit verdict, the gaps, and the visa
   facts side by side (does the user need sponsorship? what does the ad say?).
   Ask: apply or skip? On skip, `record_event` `withdrawn` with their reason
   as the detail, and stop.
3. **Is it still open?** Open the apply link. If the posting is gone, record
   `withdrawn` with detail "posting closed" and stop.
4. **Write the CV and cover letter yourself**, tailored to this ad, using only
   true facts from the profile. Save each with `save_artifact` (kind `cv` or
   `cover_letter`, a short `label` such as "v1 — agents focus").
5. **Ask before you guess.** If the form needs something the profile does not
   have (notice period, salary, a portfolio question), stop and ask the user.
   Never invent an answer. Call `ask_user` with the `application_id` and the
   exact question, and ask in chat too. When the user answers in chat, call
   `answer_ask` with the ask id and their words, so the answer is remembered
   and never asked twice. (It can also be answered on the Job360 Needs-you
   page; read `open_asks` from `whats_new` before acting.)
6. **Fill the form** if you can control a browser. Stop BEFORE the final
   submit button and show the user what you entered. Submit only after the
   user says yes to this one application.
   If a step fails (upload breaks, page errors), retry it ONCE. If it fails
   again, stop: `ask_user` with the exact step you are stuck on and the link.
   Never retry a submit you are not sure failed — a double application is
   worse than a slow one.
7. **Record it** only when the user confirms it was sent:
   `record_application` with the `channel`, the `cv_artifact_id` and
   `cover_letter_artifact_id` that were sent, and in `confirmation` any proof
   you have now: the application ID or portal reference, or "thank-you page
   seen at <time>". This saves an immutable receipt.
8. **Verify the submission.** Proof is one of: a confirmation email, the
   thank-you page (screenshot if you can take one), or the application ID /
   portal status page. If you have none yet, say plainly that it is
   "claimed, unverified" — the daily check looks for the confirmation email.
9. **Finish** with the receipt link and the follow-up date: set
   `follow_up_on` with `record_event` (`note`) about 14 days out.
