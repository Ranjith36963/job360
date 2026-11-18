# Kits: overnight coordinator log

Each entry is one hourly check. The last commit time on this file is the heartbeat the backup task reads.

## 22:40 check (18 Nov, UTC)

| Task | Last run | Score | State |
|---|---|---|---|
| Gmail check | 22:26 | achieved (90, 94) | finished 22:28, 0 failures |
| CV review | 20:33 | 100% of available (run 1 of 2) | 2 new strong, 12 weak fixed |
| People hunt | 19:57 | 50% | #1673 and #1674 live; next scored run pending |
| Bulk hunt | 20:11 | 40% | owner's task prompt updated 22:36; 23:11 is the first run with `--every-title` |
| Bulk apply | 22:07 | finish recorded | #1676 live 21:34; first recorded finish since 15:42; lapse count 0 |
| Assistant browser | 22:05 | 10% | early exit; #1677 merged 22:09; 23:05 is the first run with it |

- Owner's waiting count: 225 (the honest count after #1676 stopped hiding open asks).
- Open PR: #1665 (Bulk Hunt test). It needs a merge with main: #1675 edits the same `hunt.md` lines; keep both sides.
- Browser agents test noted that the queued applications stop at `check_submit` because their newest CV review is
  weak. CV review test: this is yours; about 190 weak CVs remain.
- The remote task "Kits overnight eval" fails with access denied. Kits now wakes from an in-session timer at about :47.

## 23:05 check

| Task | Run | Score | Cause | Fix |
|---|---|---|---|---|
| Gmail check | 22:26 | 92% | recruiter reply not drafted; summary form | #1680 merged 23:00 (body decides the match) |
| Bulk apply | 21:41 | 59% | 5 rows blocked on a job board without trying the employer's page; 2 needless asks | #1681 merged 22:58 |
| People hunt | 22:07 | 44% | every outreach review stores weak; one company worked, then stop | #1682 open, auto-merge paused by Kits (see orders-people-hunt.md) |
| CV review | 22:33 | 0% | the run ended its turn while reviewers were still running | #1679 merged 23:00 |
| Assistant browser | 22:05 | 10% | early exit after one application | #1677 merged 22:09; 23:05 is the first run with it |
| Bulk hunt | 22:11 | 0%, lapsed | old prompt; GB and IN only; no `mark_done` | prompt replaced by the owner 22:36; #1665 and coverage slices pending |

- All six sessions are reporting. Gmail holds at 90 or above for the third run.
- `whats_due` 23:04: failures hunt 1, reach 1, network 1; batch 0. Owner's waiting count 225.
- Kits paused #1682: its first-person word list decides which sentences are checked, so an unsupported claim
  about the user with no "I" would go unchecked. Orders written for People hunt test and Bulk Hunt test.
- CV review regressed from 100% (20:33) to 0% (22:33): the 20:33 result does not count toward "two in a row".
