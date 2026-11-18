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

## Needs the owner
- (none yet)
