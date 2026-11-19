# Browser agents test: overnight report

Task: Job360 assistant browser (hourly, local), trigger trig_0199qXCRxqD3Dy2eH6pCb6U5, starts at :05 (Europe/London).
Target: the run keeps taking PC tasks until the queue is empty or 40 minutes are up. Coverage: every task kind reached
(LinkedIn sends, Easy Apply, sign-in forms, alert sweeps, people searches).
Fixes merged today for this task: #1662 tab close, #1663 employer-domain codes, #1666 blocked on shared host,
#1669 compact kit, #1671 queue rotation + below-line drop (19:23), #1677 take again after hold_released (22:09).

## 22:05 run  (score 10%)
- Session cse_01KTmoHBvwM1pMFnjpBzMmjq: 72 turns, 205 s, then the summary.
- Achieved: the LinkedIn alert duty swept 3 alerts and brought 1 job (application 1086, Harmonic Security, fit 66);
  1 application taken (587 Upstarts Club, check_submit wants approval because its newest CV review is weak,
  hold_released).
- Missed: about 49 queued applications untouched; Easy Apply 0; LinkedIn sends 0 (7 held until 22 Nov by the
  weekly invitation limit, which is correct); people search not due. Doctor check skipped before the bring. The tab
  was left open: the run tried to navigate it to a blank page (refused) instead of tabs_close_mcp.
- Biggest cause: the same early exit as 19:05. After record_event hold_released the reply had no "next", and the
  recipe's stop branch had no "take again", so the assistant wrote the summary. This run started 4 minutes before
  #1677 merged, so it ran without the fix.
- Fix: PR #1677 (merged 22:09). The 23:05 run is the first with it; I score it at 23:30.
- Second cause seen: the run's summary says the ~49 queued applications will hit the same check_submit gate
  (weak newest CV review, approval needed). If true, even a run that keeps taking will only release them. That is
  the CV review task's area; I note it for Kits.
- Decision taken without the owner: none.

## 23:05 run  (score 5%)
- Session cse_01KTmoHBCb9gShZDbwadSnyh, run pc-2306: 65 turns, 189 s, then the summary. First run with #1677.
- Achieved: alert duty swept 2 alerts and brought 1 job (application 1087, Whitespace, fit 70); the tab group was
  closed (tabs_close_mcp: "Group is now empty"). The run read the recipe with #1677's "End of the run" line.
- Missed: no application taken (about 40 waiting); Easy Apply 0; LinkedIn sends 0 (7 held to 22 Nov by the weekly
  limit, correct). The linkedin_check_pending duty failed: contacts 67, 130, 131 not matched to the names on the
  Sent page, released `other` (failures_in_row 2).
- Biggest cause: the assistant ignores the keep-going signal. Both releases answered
  `"next": "Keep going: 50 tasks still wait; call take again."`, and the recipe it read says the summary comes only
  when take answers empty/closed or at 40 minutes. It wrote the summary 3 minutes in. The server and recipe already
  say the right thing; the model chooses to stop at a decision point.
- Fix: building one now. `release` (and `hold_released` for an application) in a PC run takes the next task for the
  same session and returns it in the same reply, so the assistant always holds a task instead of a choice.
  PR armed only after 00:15 so it cannot merge inside the 00:05 run window.
- Decision taken without the owner: the structural fix above (it removes a decision point; it changes no safety
  rule, and take's own gates — pace, weekly limit, backoff, pause — still decide what is handed out).

## 00:05 run  (score n/a: quiet hours by design)
- Session cse_01KTmoHB8VLprJeoxza8ijfk: 13 turns, 26 s. Correct behaviour: the recipe allows LinkedIn only from
  04:00 to 00:00 UK time, and all 50 queued tasks are LinkedIn (7 sends, the alert sweep, about 40 Easy Apply
  applications). It took nothing, claimed nothing, closed its tab and wrote the summary.
- Consequence: the 01:05, 02:05 and 03:05 runs will be the same. The first real tests of tonight's fixes are 04:05
  and 05:05 (two runs in a row, as the 90% rule needs).
- PRs since 23:30:
  - #1684 and #1685 merged (audit 6.1 / 6.2).
  - #1690 merged: main's batch.md was 71 lines after those two, red on test_recipes.
  - #1691 open: release / hold_released hands the next task in the same reply, the fix for the 23:05 early stop.
  - #1686 open: audit 6.3, conflict resolved.
- Decision taken without the owner: none.

## 01:05 run  (score n/a: quiet hours by design)
- Session cse_01KTmoHCiq6gAQav4mXmrsWL: 12 turns, 25 s. Same correct idle as 00:05: 50 tasks, all LinkedIn, outside
  04:00-00:00 UK. Nothing taken; the tab was closed. LinkedIn is not paused; yesterday's 78 pages reset at the UK day.
- Merged since 00:30: #1686 (alert ranking from the profile only, 00:33) and #1691 (a release hands the next task,
  00:42). Every fix for this task is now on main. 04:05 is the first run that can show them.
- Decision taken without the owner: none.

## Needs the owner
- Scheduled task prompt (trig_0199qXCRxqD3Dy2eH6pCb6U5) could also say it plainly. Proposed exact prompt:
  `Job360 PC browser assistant run, in my own Chrome (Claude in Chrome). / Call get_recipe("own_browser") and follow it exactly. / Keep calling own_browser_queue take until it answers empty or closed, or 40 minutes pass; never write the summary before that. / Obey every Job360 reply and warning.`
- linkedin_check_pending: 3 contacts (67, 130, 131) could not be matched by name on LinkedIn's Sent page. Their
  stored names may differ from their LinkedIn names; worth a look in the morning.
