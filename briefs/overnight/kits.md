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

## 00:05 check (19 Nov)

| Task | Run | Score | State |
|---|---|---|---|
| Gmail check | 23:26 | 95% | **two in a row overnight (92, 95): achieved.** Session stops fixing, keeps scoring. |
| Bulk apply | 22:41 | not scored | 0 workable rows; finish recorded 23:07. #1687 merged 23:35. 227 rows wait on the owner. |
| People hunt | 22:56 | 0% | run failed at 22:57; #1683 and #1688 merged; #1689 open (run works one company and stops). |
| Assistant browser | 23:05 | 5% | the run ignores "keep going" and writes its summary after 3 minutes; structural fix being built (release returns the next task). |
| CV review | 23:33 | not yet scored | first run on #1679. |
| Bulk hunt | 22:11 | finished 23:07, score pending | 23:11 run was not due (19 s). **00:11 is the first run on the new prompt.** #1665 ready, auto-merge on. |

- `whats_due` 00:04: failures reach 1, network 1; hunt 0, batch 0, inbox 0. Owner's waiting count 227.
- #1682 merged at 23:09 with the gap Kits flagged (re-armed before the orders were read). #1688 closed it at 23:50:
  every sentence is judged again unless the reviewer marks it as about the recipient. The gap was live for 41 minutes;
  no outreach is sent automatically (every `check_outreach` answers stop), so nothing went out under it.
- Main went red after #1684 and #1685 (`batch.md` over the 70-line cap). #1690 fixes it; it must merge first.

### Note for every session: how to hold a PR
Turning auto-merge off is **not** a hold. The repo's merge cage re-arms it when CI goes green (seen on #1687 and
#1688). To keep a PR out of your no-merge window, **mark it as a draft**, or do not push until CI can finish in time.

### Bulk Hunt test
Your decision "same company, same title, a different city is still refused" is noted. It is main's existing rule,
and the reply names the override (`not_duplicate_of` with `different_location`), and the kit's known list keeps the
place in its key, so the run still sees the ad. That is acceptable for tonight. The owner's rule stands: another city
is another job. Make the recipe say so plainly: when `bring_job` answers `duplicate_of` for a different city, bring it
again with the override.
