# 360-research — know the company before you apply
<!-- doc: REFERENCE — a run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Print this checklist and tick each step:
[ ] 1 Read  [ ] 2 Open  [ ] 3 Research  [ ] 4 Save  [ ] 5 People  [ ] 6 Finish


Use this for one application, after the user decided to apply and before
outreach. Every fact you save must say where it came from.

1. **Read.** `get_application` (the job, your fit verdict) and `get_profile`.
2. **Is the job still open?** Open the apply link yourself. If it is gone,
   `record_event` `withdrawn` with detail "posting closed" and stop.
3. **Research with your own tools**: what the company does, the team this role
   sits in, recent news, and whether the company has sponsored visas before
   (the ad, their careers page, public sponsor lists). Never guess: "not found"
   is an answer.
4. **Save it** as one `record_event` `note` on the application: 3-6 short
   lines, each ending with its source URL. Visa facts go in `save_fit`'s visa
   fields only when a source states them.
5. **Who to talk to:** if you find the hiring manager or recruiter, `list_people`
   first, then `add_contact` (notes = where you found them). Not finding one is
   fine — it is not a gate.
6. **Finish** with two lines: what matters for the CV and letter, and the
   next command ("run 360 apply <link>").
