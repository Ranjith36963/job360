# 360-review — the weekly look back
<!-- doc: REFERENCE — a run 360 recipe the connected assistant reads; served by routes/recipes.py -->

Print this checklist and tick each step:
[ ] 1 Digest  [ ] 2 Numbers  [ ] 3 Patterns  [ ] 4 Lessons  [ ] 5 Suggest  [ ] 6 Save


Use this when the user asks for the weekly review, or on the first daily run of a new week. Once a week.
The week is Monday to Sunday in the user's time zone. Job360 counts; it never ranks or recommends a job,
and neither do you on these numbers.

1. **Digest.** `whats_new(since=<this Monday 00:00 in the user's zone>)`, paging with `after_id` until
   done. Say what happened this week in plain words, by kind: applied, replies, interviews, rejections,
   offers, no answer (ghosted). Nothing happened = say so. `stats(view="digest")` for the interview
   rounds, recurring competencies and open gaps of the week.
2. **Numbers.** `stats(view="all")` and `export_history` for the full record. Quote every rate WITH its
   n ("2 of 7"); `small_sample` true = say "small sample". Funnel: a rejection counts as an answer.
   Velocity: still-waiting applications are left out. Calibration: quote `verdict.text` as printed, it
   is advisory; offer to read the `feedback` quotes of high-band rejections with the user. Company cards
   are facts, never a reason to skip a company or a follow-up.
3. **Patterns.** `patterns` from the same reply (`available` false = quote its `text`, go to step 4).
   Read `signals` as printed: a signal needs 2 decided applications; `clear_lead` = 10+ points over the
   overall rate. Ghosted is "no answer", never a rejection. `rejection_reasons` are the employers' exact
   words: group similar ones yourself and say which you merged. `skill_gaps`: name the Critical and
   High gaps with their counts (hard = a must-have). Never add a skill to a CV the user does not have.
   If the user wants a learning plan, write it and `save_artifact` kind `skill_gap_report` on the newest
   weak-fit application, first line `schema: 1`.
4. **Did the lesson help?** `lesson_effect`: for each agreed lesson, the reply and interview rates
   before vs after, with both n. Always add "not proof of cause"; `comparable` false = the samples are
   too small to compare.
5. **Suggest one or two changes** for next week about the user's own targeting, CV and habits
   (titles, places, CV angle, which gap to close) - never a ranking of jobs or companies. Ask which
   to keep.
6. **Save only what the user says yes to** as a `lesson` with `record_event` on the application it came
   from, so the next run starts from it. Never record one they did not agree to. A lesson they later
   disagree with: `record_event` `note` with `corrects_event_id` = the lesson's event id ("removed: <why>").
**Done when** the user heard the digest and the numbers with their n, and answered every suggestion.
