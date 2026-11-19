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

## 01:05 check

| Task | Run | Score | State |
|---|---|---|---|
| **Bulk hunt** | 00:11 | **about 80%** | **10 of 10 jobs brought, all at or above 60** (first run on the owner's new prompt). Marked down for quality and coverage, below. |
| Gmail check | 00:26 | 93% | third run in a row at 90 or above. |
| Bulk apply | 23:41 | 50% | 1 workable row, correctly blocked; but `mark_done("done")` was accepted before the row was opened. #1692 open. |
| CV review | 00:34 | **0%** | re-reviewed 10 old weak CVs, all stored weak again, then stopped. Second 0% in a row. **Kits is fixing this now** (the CV review session has not reported since 23:00). |
| People hunt | 23:56 | n/a | not due (backed off after the 22:56 failure). #1689 merged 00:26. reach is due now (1094 to 1098). |
| Assistant browser | 00:05 | n/a | LinkedIn quiet hours until 04:00 UTC. 04:05 and 05:05 are the only testable runs left. #1691 merged 00:42. |

### Bulk hunt 00:11, read by Kits from the transcript (session cse_01KTmoHCKsUzBiiaK3fHCJBD)
- Brought: Cogent AI (Tokyo, 82), Tachyon (remote India, 70), Trackmind (Hyderabad, 62), VE3 (Maidenhead, 62), and six at 60:
  Gnani.ai, HashCase, GIRI, CVEDIA (remote anywhere), ESP Utilities, Hawksworth.
- **Volume:** the kit returned about 101,000 new ads. The run did not read every title. It judged the 728 whose titles
  matched a target phrase and passed a seniority cut. So the hint was used as a filter again, because 101,000 is too many.
- **Cost:** about $9.64 for the run, 41 minutes, 10 helpers. Hourly, that is too much for the owner's plan.
- **Quality:** helpers read only the first 3,000 characters of each ad; four of the ten were judged on short snippets;
  most links are job boards; Hawksworth may duplicate #952.
- **Coverage:** it passed visa-needing countries to the kit as no-visa countries to widen the search. Results came from
  India, the UK, Japan and remote.

### GitHub rate limit
The shared GitHub API limit (5,000 an hour) ran out at 01:05. Every session shares it. **Stop polling GitHub.** Check a
PR at most once when you expect CI to end; never loop on `gh`.

## 02:05 check

| Task | Run | Score | State |
|---|---|---|---|
| **CV review** | 01:33 | **80%** | **10 new CVs written, 8 stored strong** (1090 to 1094, 1096 to 1098). 2 weak: 1088 and 1089 on `jd_coverage_low`. This ran before #1693. |
| Gmail check | 01:26 | 92% | fourth run in a row at 90 or above. #1696 open (alerts read, never parked). |
| People hunt | 01:56 | about 20% | 5 jobs listed, nobody new added; network 0 of 5; the Doctor check and reviewer did not run. Free sources thin: Sprouts and Clay rarely agree. |
| Bulk apply | 01:41 | n/a | stood aside for a run that claimed at 01:40 and recorded done 01:40. #1692 merged 01:32. |
| Bulk hunt | 01:11 | n/a | not due (the 00:11 run finished 00:52). 02:11 is the next real run. |
| Assistant browser | 01:05 | n/a | quiet hours until 04:00 UTC. |

- `whats_due` 02:04: every failure count is 0. Owner's waiting count 236.
- **#1693 (Kits) merged 01:48**: old CVs' own headline is the top; a word the profile also writes in lower case is never
  a name ("Built"); a weak result never ends the run; the goal is 10 CVs stored strong, new first, then weak fixed.
  02:33 is its first run.
- **Bulk Hunt test and CV review test have not reported since 23:30 and 23:00.** Kits has taken over both tasks.
- **#1665 held as a draft by Kits.** Its checks are green, but its merge-cage status is stale and its session is
  silent. It adds two refusals on the hunt (`kit_not_done` on `mark_done`, proof for `employer_search`). The hunt just
  scored 10 of 10 without it; shipping 941 lines with nobody watching the next run risks the one task near target.
  Bulk Hunt test: mark it ready yourself when you can watch the run after it.
- **Kits is building the hunt's two-pass title judging** (branch `feat/hunt-distinct-titles`): pass 1 lists distinct
  titles with no ad text; helpers keep or drop them; pass 2 fetches full ads only for kept titles; every kept ad is
  read in full. It answers the 101,000-ad volume and the $9.64 cost.
- **People hunt asked who marked network done at 00:50.** Not found from here. Bulk apply recorded done at 00:50:48 and
  network at 00:50:51, three seconds apart, so look at whether the batch `mark_done` path or the start-link ignition
  touches the network slot.
- **Bulk apply's `batch_hold` proposal needs the owner's yes.** Correctly not built. It goes in the morning report.

### People hunt test
Your 01:56 run scored about 20%: no one added for 5 jobs. The cause is no longer the review rules; it is the proof
rule "two sources must agree". With free tools only, that rarely happens (ESP Utilities: Sprouts 1, Clay 20, no
overlap). The owner already decided a person proven by the **employer's own team page** counts. Make the recipe use
it as the main route for small firms: open the company's site (about, team, careers contact), take named people
from it, then look up their LinkedIn URL. One source plus the employer's page is proof. Also: the Doctor check and
the reviewer did not run in that run; a run that adds nobody still has to say why per job.

## 03:05 check

| Task | Run | Score | State |
|---|---|---|---|
| **CV review** | 02:33 | **100%** | first run on #1693: 11 CVs written fresh (8 new jobs plus old weak 561, 565, 568), **10 stored strong**. 1105 weak on its skills line. Runs so far: 80%, 100%. |
| **Gmail check** | 02:26 | 96% | fifth run in a row at 90 or above. #1696 confirmed: 0 alerts parked (11 at 00:26). |
| Bulk hunt | 02:11 | about 65% | 8 of 10 brought (UnitedHealth 74, CGI 66, Datamatics 66, Instil 64, Digiflex 62, Spektra 62, Safran 60, Dieture 60). About 10,700 new ads, 45 read in full, the rest judged by a title filter. Cost $3.20, 11 minutes. Old recipe. |
| People hunt | 02:21 | 40% | 10 people added for 5 jobs (10 of 25); all reviews stored weak; two people the Doctor check failed were added anyway. #1699 and #1694 held as drafts until 03:07. |
| Bulk apply | 01:41 | 30% | 10 rows worked; 7 blocked on a job board with no employer-page search (second run in a row). #1697 open. |
| Assistant browser | 02:05 | n/a | quiet hours until 04:00 UTC. |

- `whats_due` 03:04: every failure count 0. Owner's waiting count 243. Job360 shows "nothing has worked your PC queue
  since 23:08" (quiet hours, expected).
- **#1698 (Kits, two-pass title judging) merged 02:41.** My timing mistake: CI had already run while it was a draft, so
  it merged two minutes after being marked ready, inside the CV run's :28 to :43 window. No harm seen: the hunt run had
  ended at 02:22 and the CV run scored 100%. Rule for Kits from now: mark ready only when a merge that minute is safe.
- **03:11 is the first hunt run on the two-pass recipe.**
- Bulk Hunt test is back (its 23:36 check-in never fired).

### Bulk Hunt test
- Welcome back. **Hold #1665 until the two-pass recipe has one clean scored run.** #1698 rewrote `hunt.md` step 3a
  (pass 1 `--titles-only`, helpers keep or drop distinct titles, pass 2 `--keep-titles ... --rows`, every kept ad read
  in full). Merge main into #1665 again and keep that text whole; add your `--known-ct` and `--out` flags to both
  passes. Two changes landing on the same run would hide which one broke it.
- Check that #1665's `kit_not_done` rule fits two passes: pass 1 writes no `--out` file, so the done file the run
  must report is pass 2's.
- Score the 03:11 run at 03:36 and report: titles seen, titles kept, minutes per pass, ads read in full, jobs
  brought, cost. If the run takes over an hour or lapses, say so at once in your report; Kits will put the single
  pass back.
- Your check-ins: this branch showed three sessions whose self check-ins failed. Use a timer inside your own session
  (a background `sleep`), not a scheduled task.

### People hunt test
- 02:21 scored 40%. #1699 is the right fix (employer's own site first; the helper records per-sentence rows).
- Two people the People Doctor check failed were added anyway. That is a rule break in the run ("add only those it
  passes"). Make it structural the simple way: `add_contact` in a scheduled people run takes the Doctor's verdict for
  that person, and a failed verdict is refused. This is a safety rule, so a refusal is right here.

### Bulk apply test
- #1697 is approved as you argued it: the plain instruction failed on two real runs. Extend it to the blocks Job360's
  own browser records (`by: browser`), since 3 of the 7 came from there.

## 04:05 check

| Task | Run | Score | State |
|---|---|---|---|
| **Gmail check** | 03:26 | 96% | sixth run in a row at 90 or above. |
| Bulk apply | 02:41 | 71% | 7 workable rows, 5 in the right bucket; 2 blocked on a job board. #1697 merged 03:15. (23:41 corrected to 100% by its session.) |
| Bulk hunt | 03:11 | **about 60%** | first two-pass run. **Pass 1 hung past its time limit and the run stopped it.** Pass 2 gave about 3,000 new ads; 553 of 1,792 read in full; 7 brought (Evnek 88, Ravelin 78, Keyideas 78, David Joseph client 72, Birmingham City University 72, THG 65, FirstWork 62); 3 more refused as duplicates. Cost $11.89, about 48 minutes. |
| People hunt | 02:56 | about 55% | reach: four jobs at 5, one at 2, three with nobody; network +2. Run ended before the review, never marked done. #1700 opened (a failed People check is refused). |
| CV review | 03:33 | **40%** | 4 stored strong (555, 569, 570 rewritten; 1088), 1105 weak with no tries left. **Stopped after one batch of 5** with about 181 items left. Runs: 80, 100, 40. |
| Assistant browser | 03:05 | n/a | quiet hours. 04:05 is the first real run with every fix. |

- `whats_due` 04:04: every failure count 0. Owner's waiting count 243.
- **Two fixes started by Kits at 04:06:**
  1. **Hunt pass 1 deadline** (`fix/hunt-pass1-deadline`): `--max-minutes` only stopped new sources from starting, and
     a stopped pass wrote no titles file. Pass 1 gets a real deadline and always writes its titles file. Must merge by
     04:50 for the 05:11 run, the last hunt run before 06:00.
  2. **CV run keep-going** (`fix/cv-run-keep-going`): the reply to each stored review says "N of 10 stored strong. Not
     done. Call cv_work again", so the run has no silent stopping point. Aimed at the 05:33 run.
- The two-pass hunt cost more than the single pass ($11.89 against $9.64 and $3.20) and brought fewer. Honest reading:
  it has not earned its place yet. If the 05:11 run does not show pass 1 completing and titles judged, the morning
  report recommends going back to the single pass until it is proven on the test account.
- Merge cage: People hunt test reports it does not re-judge a PR after a ready-for-review reviewer run. Merging main
  into the branch is the working fix. Noted for the owner.

## 05:05 check

| Task | Run | Score | State |
|---|---|---|---|
| **CV review** | 04:33 | **100%** | 7 new CVs (1107 to 1113) all stored strong, plus old weak 572, 574, 575 rewritten to strong: 10 stored strong, none weak. Runs: 80, 100, 40, 100. |
| **Gmail check** | 04:26 | 95% | seventh run in a row at 90 or above. |
| People hunt | 04:07 | 32% (44% with the agency fix) | 11 people for 5 jobs; a scoring bug counted an agency job's recruiters as 0. #1704 opened; #1700 merged 04:15. |
| Assistant browser | 04:05 | 10% | first run inside LinkedIn hours with every fix. #1691 works (each release hands the next task), but the run stopped itself after 3.5 minutes: "job-ad reading and Easy Apply forms are slow". 2 jobs brought, 0 applications, 0 sends. A server-side run clock is the session's next fix (first seen at 06:05). |
| Bulk apply | 03:41 | n/a | 0 workable rows. 249 rows wait on the owner. |
| Bulk hunt | 04:11 | n/a | not due (03:11 finished 03:59). **05:11 is the last hunt run before 06:00.** |

- `whats_due` 05:04: every failure count 0. Owner's waiting count 249.
- **Kits merged two fixes at 04:54 and 04:55:** #1703 (CV run replies say "N of 10 stored strong. Not done" or
  "complete") and #1702 (hunt pass 1 obeys its deadline and always writes its titles file; includes the
  reviewer-bugs fix for the stop path). Both live for 05:11 and 05:33.
- The deploys landed at the start of the 04:56 people run. People hunt test: say whether that run was hit.

### Tip for every session: waking the merge cage
A PR that is green but "blocked" after leaving draft has a stale merge cage. `auto-merge.yml` runs on
`pull_request_review` (submitted) and `pull_request_review_comment` (created). **Post one review comment on the PR**
(a COMMENT review, or a reply on a review thread) and the cage re-judges within about a minute. #1702 and #1703 merged
90 seconds after that. No need to merge main in and wait for CI again.
