# 360-prep — get ready for an interview
<!-- doc: REFERENCE — a run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Print this checklist and tick each step:
[ ] 1 Read  [ ] 2 Facts  [ ] 3 Prepare  [ ] 4 Save  [ ] 5 After


Use this when an application has an interview requested or scheduled.

1. **Read.** `get_application` (the ad, the CV version that was sent, the
   receipt, contacts, research notes) and `get_profile` (including `lessons`).
2. **Confirm the facts.** When, how (video/phone/onsite), with whom. If any is
   missing, ask the user (`ask_user` with the application_id, and in chat).
3. **Prepare, using only true facts from the profile and the CV that was sent:**
   - the 5 questions this ad most likely leads to, each with a short answer
     built from a real role or project;
   - 3 short stories (situation, what they did, result with a number);
   - the gaps the fit verdict named, and an honest way to talk about each;
   - 3 good questions to ask them, including visa sponsorship if the user
     needs it and it is still unknown.
4. **Save it** with `save_artifact` (kind `answers`, label "interview prep v1")
   so it is versioned with the application.
5. **After the interview**, ask the user how it went and record it:
   `record_event` `interview_done` (detail = their words), then `lesson` for
   anything worth remembering next time.
