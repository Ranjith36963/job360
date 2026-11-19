# Morning report, 19 Nov, 06:05 UTC (from Kits)

## The board

| Task | Result | Runs overnight (% of target) | Achieved? |
|---|---|---|---|
| Gmail check | every run at 92 to 96% | 92, 95, 93, 92, 96, 96, 95, 95 | **Yes** |
| CV review | last two runs at 100% | 0, 0, 80, 100, 40, 100, 100 | **Yes** (two in a row) |
| People hunt | best 67% | 44, 0, 20, 40, 55, 32, 67 | No |
| Bulk apply | 100% on two runs, not in a row | 59, 100, 30, 71, 100 (three runs had nothing to work) | No |
| Bulk hunt | best 80% | 0, 80, 65, 60, 0 | No |
| Assistant browser | 5 to 15% | 10, 5, 10, 15 (four runs in LinkedIn quiet hours) | No |

Two of six reached the target. Four did not.

## What was fixed overnight

26 fixes merged and went live between 21:31 and 05:48. By task:

- **Bulk apply:** #1676 (the queue hid open questions that the submit check counted, so no run could record a finish for
  six hours), #1681, #1687, #1692, #1697.
- **CV review:** #1679, #1693 (the summary line of older CVs; "Built" treated as a name; a weak result ended the run),
  #1703 (replies say how many are stored strong).
- **Bulk hunt:** #1675 (Claude judges every title), #1698 (two passes: distinct titles first), #1702 (pass 1 deadline).
- **People hunt:** #1683, #1688, #1689, #1700 (a person the People check failed is refused), #1704.
- **Gmail check:** #1680, #1696.
- **Assistant browser:** #1677, #1684, #1685, #1686, #1691, #1705 (a run clock on the server).
- **Other:** #1690 (main's recipe line cap), #1701 (lessons).

## Why four are not there

1. **Bulk hunt: the volume is too large for "Claude judges every title" as built.** The 05:11 run saw 84,379 distinct
   titles across 125,958 listings. It cut them with a word list again, read 45 ads in full, and brought 0. Best run of
   the night: 10 of 10 at 00:11 on the single pass, at $9.64. Runs cost $3 to $12 each.
2. **Assistant browser: the run stops itself after 2 to 4 minutes of its 40.** Three fixes told it to keep going; it
   still stopped. #1705 (the server's clock in every reply) is untested: 06:05 is its first run.
3. **People hunt: free lookup tools rarely agree on a person.** The employer's own team page is now the main proof,
   which lifted it from 20% to 67%. Two fixes are still open as drafts (#1694, #1699).
4. **Bulk apply: nothing can be submitted.** 250 queued jobs wait on the owner's answers, sign-ins or the PC. Every
   run now records a finish and puts rows in the right bucket.

## Mistakes by Kits

- #1698 merged two minutes after being marked ready, inside the CV run's window. No harm seen.
- #1703's counter is wrong: the 05:33 run saw "N of 10" restart at 1 after each record call and never saw
  "complete". The run still stored 20 strong on its own count. Needs a fix.
- The two-pass hunt (#1698) cost more and brought fewer than the single pass. It has not earned its place.

## Needs the owner

1. **Answers:** confirm the drafted answers on job360.uk, Needs you. 250 rows wait.
2. **Bulk hunt:** decide between (a) back to the single pass with the profile's title phrases as the first cut, or
   (b) keep two passes and judge titles per source in smaller sets. And move it to twice a day: hourly at $3 to $12 a
   run is too much.
3. **Bulk apply `batch_hold`:** say yes to "the apply run is due only when it has apply work".
4. **PC:** free C: and change the assistant browser task's folder.
5. **PR #1665** (hunt duplicates and proof) is held as a draft; #1694 and #1699 (people hunt) are drafts ready to go.
6. Gmail: Halimah S. wants an answer; Connells and DoiT ask for data consent.
7. Contacts 67, 130 and 131 cannot be matched on LinkedIn's Sent page; their stored names may differ.
8. Scheduled check-ins into a session fail with access denied for every session. Timers inside the session work.
