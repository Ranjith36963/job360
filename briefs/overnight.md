# Overnight orders: every scheduled task to 90% by 06:00 UTC, 19 Nov

From Kits, for the owner. Written 21:20 UTC, 18 Nov. The owner is asleep until about 06:00 UTC.
Kits coordinates. Each test session owns one scheduled task and fixes it.

## The goal

Each scheduled task reaches **90% or more of its target, with full coverage, on two runs in a row**.
Score = the share of the target the run achieved. A hard-rule break scores 0.

## How we talk (Kits cannot message you, and cannot read three of you)

- **Your report file:** `briefs/overnight/<your-session>.md` on branch `kits/briefs-2026-11-18`.
  Append one entry after every run of your task. `git pull --rebase` before each push.
- **Kits' orders to you:** the section for your session in this file, plus any file
  `briefs/overnight/orders-<your-session>.md`. Re-read both every hour before you start work.
- The owner will not answer overnight. Do not stop to ask. Decide, write the decision and its reason
  in your report file, and keep going. Kits reviews the decisions in the morning.

## Your hourly loop

Set your own check-in for about 25 minutes after your task's start minute, every hour until 06:00 UTC.
On each check-in:

1. **Read the run.** Get the latest run of your task (the scheduled task's last run, then its
   transcript) and the Job360 reads you need. Reads only.
2. **Score it** against your target and coverage below. Write the entry in your report file:
   time, score, what was achieved, what was missed, and the single biggest cause.
3. **Fix the biggest cause.** One small PR, a failing test first, under 400 lines, auto-merge on.
   If the cause is not in code (the owner's answers, the PC being off, a connector signed out),
   write it under "Needs the owner" and move to the next cause.
4. **Stop fixing** when your task has two runs in a row at 90% or more. Keep scoring until 06:00.

## Rules for every session

- Never write to the owner's live Job360 account. No `whats_due` with `duties` or `start`, no test
  jobs, no asks. Reads are fine.
- Claude is the intelligence. A fixed pattern may give a hint; it never drops, refuses or picks.
  See `briefs/pattern-audit.md` for the findings in your area.
- Keep it simple. Tell Claude what to judge in the recipe. Add no new refusals unless a real run
  shows the plain instruction failing.
- Safety rules stay as they are: passwords, CAPTCHAs, `check_submit`, `check_outreach`, truthful
  answers from the profile, the protected categories.
- Merging to main ships to production. Do not merge in the 5 minutes before your task's start
  minute or the 10 minutes after it, so a deploy does not restart the server under a run.
- You cannot edit the owner's scheduled task prompts. If a prompt needs changing, write the exact
  new prompt under "Needs the owner".
- Owner decisions already made: the hunt covers every plan country and region, on-site, remote and
  contract, and keeps every ad whatever it says about sponsorship. Apply to all of them.
  Availability is immediate; relocation is yes; work-authorisation answers stay truthful per
  country. People hunt uses free tools only; for agency jobs, target the named recruiter (1 to 2
  people); a person proven by the employer's own team page counts.

## Targets, by session

### Bulk Hunt test (task starts at :11)

- **Target:** 10 new jobs at or above the score line, per run.
- **Coverage:** every row of the profile's search plan is searched (every country and region;
  on-site, remote and contract), and every level in the profile. The run reports which rows it
  searched and how many titles it saw and judged.
- **Known causes:** the run must use `--every-title` (live since 20:00; the 20:11 run skipped it
  because the owner's task prompt still carries `--max-years 3` and an exclude list); on-site abroad
  kept only when the ad states sponsorship; one search per source, not per plan row; #1665 needs a
  merge with main (#1675 edits the same `hunt.md` lines: keep both sides).
- **Volume:** `--every-title` hands over about 10 times more rows. If a run chokes on it, the fix is
  judging distinct titles once and a memory of titles already judged. Build that only if a real run
  shows the need.

### CV review test (task starts at :33)

- **Target:** 10 new CVs written and stored strong per run. If fewer than 10 new jobs are queued,
  all of them, plus weak CVs fixed until the run has done 10 pieces of work.
- **Coverage:** every queued job without a strong CV is either done this run or listed as left.
- **State:** the 20:33 run hit the target (2 of 2 new strong, 12 weak fixed). Confirm it holds, then
  work section 2 of `briefs/pattern-audit.md`. About 190 old weak CVs remain.

### People hunt test (task starts at :56)

- **Target:** every due job worked; 5 people per job (1 to 2 named recruiters for an agency job;
  3 is enough for a firm under about 50 staff); 5 network people when due.
- **Coverage:** every application above the line gets its first people search within a day.
- **Known causes:** every outreach review stores weak (section 3 of the audit: fact checks applied
  to facts about the recipient); `failures_in_row.reach` is 1, find which run lapsed and why.

### Gmail check test (task starts at :26)

- **Target:** every mail since the last check is read, and each job mail is matched or parked.
- **Coverage:** the search covers every open application.
- **State:** achieved (90, then 94). Keep scoring. Work section 4 of the audit.

### Bulk apply test (task starts at :41)

- **Target:** every queued job that needs nothing from the owner gets `check_submit` and is
  submitted. Every other row is put in exactly one bucket with its exact reason. The run always
  records `mark_done`.
- **Coverage:** no queued row is left unlooked-at.
- **State:** no run recorded a finish from 15:42 to 21:00. Cause found and fixed in #1676 (Kits):
  the queue hid open asks that `check_submit` counted, so `mark_done` refused every stop. Confirm on
  the first run after it deploys. Then: a sponsorship or work-rights question must not stop for the
  owner when a saved answer for that country exists; the `batch_hold` change; section 5 of the audit.
- **Honest limit:** most rows wait on the owner's answers. Score the run on the rows it could work.

### Browser agents test (assistant browser task starts at :05)

- **Target:** the run keeps taking PC tasks until the queue is empty or its 40 minutes are up.
- **Coverage:** every task kind is reached: LinkedIn sends, Easy Apply, sign-in forms, alert sweeps,
  people searches.
- **Known causes:** the 19:05 run stopped after 185 seconds with about 45 tasks left; the 20:06 run
  ended in 4 seconds (the PC did not pick it up); the CV file cannot reach the PC while C: is full.
- **Honest limit:** if the owner's PC is off or its disk is full, write that under "Needs the owner".

## Report entry format

```
## HH:MM run  (score NN%)
- Achieved:
- Missed:
- Biggest cause:
- Fix: PR #NNNN, or "Needs the owner: ..."
- Decision taken without the owner (if any), and why:
```
