# 360-apply — tailor, apply with the user, record it
<!-- doc: REFERENCE — a /run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Use this for one application the user chose. The user confirms every submit.

1. **Read.** `get_application` (the job, your fit verdict, earlier versions)
   and `get_profile`.
2. **Write the CV and cover letter yourself**, tailored to this ad, using only
   true facts from the profile. Save each with `save_artifact` (kind `cv` or
   `cover_letter`, a short `label` such as "v1 — agents focus").
3. **Ask before you guess.** If the form needs something the profile does not
   have (notice period, salary, a portfolio question), stop and ask the user.
   Never invent an answer. Call `ask_user` with the `application_id` and the
   exact question, and ask in chat too. When the user answers in chat, call
   `answer_ask` with the ask id and their words, so the answer is remembered
   and never asked twice. (It can also be answered on the Job360 Needs-you
   page; read `open_asks` from `whats_new` before acting.)
4. **Fill the form** if you can control a browser. Stop BEFORE the final
   submit button and show the user what you entered. Submit only after the
   user says yes to this one application.
   If you cannot fill it (login wall, CAPTCHA, upload you cannot do), stop,
   tell the user the exact step you are stuck on, and give them the link.
5. **Record it** only when the user confirms it was sent:
   `record_application` with the channel (company site, email, LinkedIn…).
   This saves an immutable receipt of exactly what was sent.
6. **Finish** with the receipt link and when to follow up.
