# Bulk apply test: overnight report

Session: "Bulk apply test" (Claude Code, claude.ai/code session_01KTmoHDDTYY4wtA1XyUUFmA). Task starts at :41.
Data source: I cannot open the scheduled run's transcript from this session, so each run is read from Job360's
own event log (`whats_new`, `whats_due` without duties), read-only. Ids below are application ids.
Authority for fixes: the owner's standing instruction of 3 Nov ("after apply runs, report what improved and
what still needs fixing, and keep fixing until everything is fixed"), not Kits' order alone.

Scoring: the brief's target counts the rows the run could work (needs nothing from the owner). A row
scores when it ends in the right bucket with its exact reason: submitted, needs_user with the exact
question, blocked with the real reason after the recipe's own steps, PC-only, below line. Plus the run must
record `mark_done`. No submit was possible on any row this run (sign-ins, a CAPTCHA, owner answers), so the
score is about handling, not submits.

## 21:41 run  (score 59%)
- Achieved: first run on #1676. Claimed, worked 17 rows (1061-1084) one at a time in about 10 minutes, and
  `mark_done` was accepted at 22:07 (batch failures_in_row 0, the first recorded close since 15:42). Every
  row ended in check_submit, a block or an ask. 1067 (Ashby) got as far as the submit click; a CAPTCHA moved
  it to the PC. No row was left unlooked-at (the 409 stop would have named it).
- Missed (7 of 17):
  - 5 rows blocked on a job board without trying the employer's own apply page, which batch.md step 3
    requires ("A job board or 403 page: the employer's own apply page for the same role, update_job(apply_url)"):
    1061 and 1075 (shine.com, sign-in), 1062 and 1063 (reed.co.uk, "other"), 1074 (cutshort.io, sign-in).
  - 2 needless asks: 1066 "leave the optional Photo upload blank?" (optional field: leave it blank), and
    1079 "no fit has been judged... Apply or skip?" (the run judges fit itself with save_fit; it never asks).
- Correct: 1067 captcha, 1068 Workday sign-in, 1072 law-firm consent gate, 1073 agency button the gate
  refused, 1084 email-only (manual_only), 1069/1070/1071/1082/1083 real answers only the owner has
  (salary in lakhs, notice, AI-agent yes/no questions).
- Biggest cause: board links blocked at the board's sign-in page. The recipe line is there; the plain
  instruction failed on 5 rows in one run.
- Fix: PR #1681, merged 2026-11-18T22:58:06Z (outside the :36-:51 window): `record_event(blocked)` on a job-board host (job_checks.classify_host,
  the existing list) returns a reply hint: find the employer's own page first, update_job(apply_url), work
  that page; block on the board only if there is none, and say so. A hint, never a refusal (pattern-audit
  owner rule). Log line `blocked_on_job_board`.
- Decision taken without the owner: I keep #1676's rule (a refill row with an open ask is the owner's, as at
  check_submit) over my own #1660 rule. It makes check_submit the single source, which is the fix I had
  proposed for row 319.

## 22:41 run  (score: not scored, 0 workable rows)
- Achieved: claimed and closed: `mark_done` accepted at 23:07:17, batch failures_in_row 0. No row was
  opened (no kit_read, fill or block after 22:38), and the stop was not refused, so Job360 found no queued
  Job360-browser row with a CV that needed nothing from the owner. 225 rows wait on the owner (whats_due).
- Missed: nothing to score. The new rows from the 22:11 hunt (1087 at 23:08) arrived after the claim.
- Not counted toward "two runs at 90%": a run with no workable row tests nothing.
- Biggest cause carried from 21:41: the two needless asks (1066 optional photo, 1079 unjudged fit).
- Fix: PR #1687 (batch.md: never ask about an optional field; judge a missing fit with save_fit, never ask
  apply-or-skip). Auto-merge on; a guard at 23:33 pauses it if CI has not finished before the 23:36-23:51 window.
- Decision taken without the owner: none new.

### 23:36 note: a merge landed inside the no-merge window (my mistake)
- PR #1687 merged at 23:36:01, and its backend deploy finished at 23:43:35, about 2 minutes into the 23:41 run
  (a restart under a live run is what the window exists to prevent).
- What happened: at 23:33 I disabled auto-merge to hold it until 23:52. The repo's merge cage
  (`auto-merge.yml`, "the cage's arm") re-armed it at 23:35:56, on the status event when CI went green,
  and it merged 5 seconds later. Disabling auto-merge is not a hold here.
- Rule for the rest of the night: push a PR only when its CI can finish before :33 (CI took 13 to 24
  minutes tonight), i.e. push by about :05 past the hour, else hold the branch unpushed (or as a draft) until :52.
- Effect on the 23:41 run: checked at 00:06 (did any call fail around 23:41-23:44?).

## 23:41 run  (score 50%, corrected to 100% at 03:10: see Correction)
- Achieved: one workable row, 1087 (Whitespace, Junior Engineer; CV reviewed strong 23:38). Opened at 23:42:08,
  blocked at 23:42:38 with a real reason: on the CharlieHR careers page the role's link and form were not
  reachable, and the list showed the role posted 22 days ago against an ad from 1 day ago. Coverage of
  workable rows: 1 of 1. No submit was possible.
- Missed: batch was marked done at 23:41:49, BEFORE the run opened 1087 (whats_due last_done_at), and Job360
  accepted it: `mark_done(outcome="done")` never ran the work-left check. The run's close was not proof that
  every row had been looked at. The block reason is "other"; "posting_closed" (with the 22-days evidence) may
  fit better, but the page did not say the role was gone, so I count the bucket as correct.
- Restart under the run: PR #1687's deploy finished 23:43:35 (my merge-window miss, note above). No call in
  the run's events failed around it: kit_read 23:42:08, blocked 23:42:38, hold_released 23:42:38.
- Biggest cause: `mark_done` "done" skips the work-left check (routes/schedule.py), so a run can close with
  rows left. This is the coverage hole behind "no queued row left unlooked-at".
- Fix: PR #1692 (draft): "done" runs the same 409 check as a partial stop. Held as a draft so it cannot merge
  in the 00:36-00:51 window; marked ready at 00:30 if CI is green, else at 00:52.
- Decision taken without the owner: I changed one case in test_run_outcomes that asserted "done" with a
  ready row left is accepted. That case encoded this gap; it now asserts the 409. No assert was loosened.

## 00:41 run  (not scored, 0 workable rows)
- Achieved: claimed and closed (`mark_done` 00:50:48, failures 0). No row opened: every queued row with a CV
  that Job360's browser runs waits on the owner (227 rows waiting on you, whats_due). The fit_judged events at
  00:50-00:52 belong to the 00:52 hunt; its 10 new rows (1088-1098) were queued after this run's claim.
- Second run in a row with nothing to work, but batch keeps coming due ("queue_waiting"): see batch_hold below.
- #1692 (the "done" check): green at 00:30, held as a draft through the window, marked ready 00:53. It then sat
  "blocked": the merge cage had not re-judged after the draft (its reviewer checks still read "skipped" from
  the draft run). I cannot dispatch auto-merge.yml from this session (403), so I merged main into the branch
  at 01:14 (the repo's own fix for a stale cage) to re-run CI. A draft-conversion guard at 01:32 keeps it out
  of the 01:36-01:51 window if CI is slow.

## Needs the owner
- **batch_hold (owner rule of 2026-11-01).** `services/profile/schedule.py:175` keeps the batch due while any
  queued row has no CV ("writing a CV needs nobody"). Since #1646 (your yes, 18 Nov) the batch never writes a
  CV; the CV run does. So batch comes due every hour with nothing it may do (22:41 and 00:41 tonight, 15:41
  to 17:41 yesterday). Proposed change: batch is due only while it has batch work, by the same test mark_done
  uses (`queue_preflight.fillable_rows(with_cv=True)`, plus rows you pressed Send on). CV-less rows stay the CV
  run's. Not built: it reverses your own rule, `tests/test_batch_waiting_on_you.py` encodes it, and Kits' order
  is not your yes. Cost of waiting: empty runs, not lost work. Say yes and I build it (failing test first).
- Decision taken without the owner: not changing batch_hold tonight, for the reason above.
- #1692 merged 01:32:43 (merge-main push at 01:14 re-ran CI and the cage); backend deploy finished 01:35:03,
  before the 01:36 window. The 01:41 run is the first with the "done" work-left check.

## 01:41 run  (score 30%)
- Achieved: first run on #1692 (done check, live 01:35). Worked 10 rows in 3 minutes (1088-1098 except 1095,
  session apply-20261119T0142), one at a time; every row ended in a block or an ask. Real owner asks: 1090
  (CVEDIA answers), 1088 (Cogent Labs consent box). No submit possible.
- Missed (7 of 10): blocked on a job board without looking for the employer's own page: 1089, 1092
  (Instahyre sign-in), 1091, 1093, 1094 (Shine sign-in), 1096 (WhatJobs CAPTCHA), 1098 (WhatJobs search page).
  The kit's apply_host check, batch.md step 3 and #1681's hint all said to; 1094's detail even says
  "Employer's own page not found in this run" with no search; 1098 names update_job but did not call it.
  Three of the seven (1089, 1092, 1096) were recorded by Job360's own browser (by: browser), so #1681's
  reply hint never reached the run for them.
- Close: last_done_at batch is 01:40:30, before this run's work, and no later mark_done is recorded. I cannot
  tell from the events whether this run called mark_done at the end (a 409 would leave no stamp).
- Score: 3 of 10 rows in the right bucket (1090, 1088 asks; 1098 arguably), 30%.
- Biggest cause: board links blocked without an employer-page search (second run in a row: 5/17, now 7/10).
- Fix: PR #1697 (draft, ready at 02:52): an assistant's board block needs `employer_page` (what the search
  found), else a 422 telling it to search, then update_job(apply_url) or say what it found. A requirement to
  state the judgement, not a pattern judging; added only because the plain instruction failed on two real runs.
- Decision taken without the owner: adding this refusal. The brief allows one where a real run shows the plain
  instruction failing; it has now failed twice. Limit: blocks Job360's own browser records are not covered.

### Correction (03:10): I misread last_done_at
- `whats_due.last_done_at.batch` is the run's START (its claim time), not when it called mark_done
  (`services/profile/schedule.py:420-423`: "The duty is stamped when its run STARTED (its claim)").
- So my 23:41 and 01:41 claims "batch was marked done before the run opened its rows" are wrong: those stamps
  are the runs' starts. I have no evidence that either run closed early.
- #1692 is still a real fix (a `done` close with a workable row left was accepted; its test proves it), but
  its PR text names the wrong trigger. The 23:41 score of 50% was built on that misreading: corrected to
  100% (1 of 1 row in the right bucket, 1087 blocked with a real reason). Lesson for the morning PR:
  read how a stamp is written before inferring order from it.

## 02:41 run  (score 71%)
- Achieved: 7 workable rows (1099-1106 except 1102), one at a time, 02:42-02:46, all in a bucket:
  1099 Taleo sign-in and 1101 CGI's njoyn sign-in (employer ATS, correct); 1104 Zoho Recruit CAPTCHA plus one
  ask for unanswered questions (correct); 1100 Instil (4 required essay drafts for the owner's OK) and 1105 Safran
  (required questions the profile does not answer) are real owner asks. Closed without a failure.
- Missed (2 of 7): 1103 (Shine) and 1106 (Instahyre) blocked on a job board's sign-in without an employer-page
  search. 1106 was recorded by Job360's own browser (by: browser), 1103 by the run. #1697 was still in CI.
- Biggest cause: board blocks (third run in a row), now down to 2 of 7.
- Fix: #1697 (employer_page required on an assistant's board block) is green on its re-run and auto-merging
  before 03:36. Still not covered: blocks Job360's own browser records (1106). Next if it recurs: a hint at
  `browser open` on a board link, before the page loads.
- PC queue: whats_due says nothing has worked the PC queue since 23:08 UTC; 50 tasks wait. Needs the owner's
  PC (browser-agents session's area).
