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
- Fix: PR #1681 (auto-merge on, opened 22:45; CI lands it after the 22:41 run window): `record_event(blocked)` on a job-board host (job_checks.classify_host,
  the existing list) returns a reply hint: find the employer's own page first, update_job(apply_url), work
  that page; block on the board only if there is none, and say so. A hint, never a refusal (pattern-audit
  owner rule). Log line `blocked_on_job_board`.
- Decision taken without the owner: I keep #1676's rule (a refill row with an open ask is the owner's, as at
  check_submit) over my own #1660 rule. It makes check_submit the single source, which is the fix I had
  proposed for row 319.
