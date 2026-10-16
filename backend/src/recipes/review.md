# 360-review — the weekly look back
<!-- doc: REFERENCE — a run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Print this checklist and tick each step:
[ ] 1 Numbers  [ ] 2 Say  [ ] 3 Suggest  [ ] 4 Save

Use this when the user wants to look back at how the hunt is going. Job360 counts
what was recorded; you say what the counts show. Nothing here ranks a job or a company.

1. **Read the numbers:** `stats(view="all")` (counts by CV version and by role, plus
   funnel, velocity, calibration, company, lesson_effect, followups) and `export_history`.
2. **Say what the record shows.** Quote every rate WITH its n. When `small_sample` is
   true, say "small sample" and do not call it a pattern. Never compare before and after
   unless `comparable` is true.
   - funnel: a rejection counts as a reply here (overall.reply_rate does not); name which one you quote.
   - velocity: say the still-waiting applications are left out of the median.
   - calibration: quote `verdict.text` as printed; it is advisory. Offer to read the
     `feedback` quotes of high-band rejections with the user.
   - company: cards are facts, never a reason to skip a company or a follow-up.
   - lesson_effect: always add "not proof of cause".
3. **Suggest one or two changes** for next week (titles, places, CV angle)
   and ask the user which to keep.
4. **Save only what the user says yes to** as a `lesson` with `record_event` on the
   application it came from, so the next run starts from it. Never record one
   they did not agree to. A lesson they later disagree with: `record_event`
   `note` with `corrects_event_id` = the lesson's event id ("removed: <why>").
