# People hunt: overnight report (People hunt test session)

Target: every due job worked; 5 people per job (1-2 named recruiters for an agency job; 3 for a firm under ~50 staff);
5 network people when due. Coverage: every above-line application gets its first people search within a day.

Already live before tonight: #1673 (jobs waiting >24h get their first search first, oldest first),
#1674 (score counts people per company; one-line notes backed by several quotes can store strong;
page-proven staff kept without LinkedIn; People Doctor check before add_contact).

## 22:07 run  (score 44%)
- Achieved: 5 due jobs listed; Harmonic Security +2 (CEO, CTO from harmonic.security/about), each with a note;
  per job now Harmonic 2, i23 2, Fidelity 3, Intel 3, Panorama 1 (11 of 25). Network not due.
- Missed: 4 of 5 companies got no new search (Sprouts and Clay never called); both note reviews stored weak
  (claim_not_in_quote); People Doctor check ran after add_contact.
- Biggest cause: every outreach review stores weak: facts about the RECIPIENT (sourced from their company page)
  are judged against the user's profile (pattern audit 3A). Weak reviews also block auto-send for good after 2 tries.
- Fix: PR #1682 (only claims about the user are judged; a recipient-only line cites its page via field_path "page:").
- Decision taken without the owner: kept the first-person split as a hint, not a refusal; a line with no "I/my/me"
  is still judged whole unless the reviewer marks it as a page row, so a signature title stays a claim.
- Next cause queued: the run works one company then stops (4 of 5 untouched); audit 3B (company_team_page 422 on
  hosts without the company name, e.g. Alphabet on abc.xyz) to become a warning.
- failures_in_row.reach: 0 at 22:39 (the earlier 1 was the 11:59 claim taken outside the routine; cleared since).

## 22:56 run  (score 0%)
- Achieved: nothing recorded. reach and network were both marked FAILED at 22:57:41, about 90 s into the run
  (failures_in_row 1 each). The 2h back-off now holds both until 00:57, so the 23:56 run will find nothing due.
- Missed: the 5 due jobs (930-934) and network.
- Biggest cause: unknown from here. The scheduled task's last_run was overwritten by the 23:08 ignition run (a 6 s
  "not due"), so the 22:56 transcript is not readable to me. mark_done refuses tool_unavailable / no_work /
  needs_user for people runs, so the reason was network_blocked, rate_limited or error. No deploy landed before it
  (#1681 deployed 23:00, after the failure).
- Fix: "Needs the owner / Kits": read Railway's `schedule_duty_done` line for duty reach at 22:57:41 (its reason
  field) to name the cause.
- Note for Kits: #1681, #1680 and #1679 merged at 22:58-23:00, inside the people hunt's :51-:06 window.

## #1682 correction (23:35)
- #1682 merged at 23:09. I re-armed its auto-merge at 23:06, before reading Kits' 23:10 orders; my mistake.
- The safety gap Kits named was real and live: user_claims() judged only the first-person sentences, so
  "...at Acme. Built systems serving 1M users." stored strong.
- Fix: PR #1688. Every sentence is judged again unless the fresh reviewer marks it as about the recipient (a
  field_path "page:<url>" row naming that sentence, with a page quote of 3+ words). Kits' test (an unsupported
  second sentence with no "I") fails on main and passes with the fix. The word list is removed. Auto-merge is
  armed for 00:07 (after the :51-:06 window).
- Also open: PR #1683 (audit 3B: a company page whose host lacks the company name is a logged hint, not a 422).

## 23:50 merges and next fix
- #1683 merged 23:24 (company page on a host without the company name: logged hint, not 422).
- #1688 merged 23:50:01. I paused its auto-merge at 23:48 for the :51-:06 window; the repo's sweep re-armed it at
  23:49:58 and CI was already green. Its deploy lands about 23:54, inside the window; harmless tonight because
  reach and network are backed off until 00:57 (the 23:56 run has nothing due). Note for Kits: the sweep re-arms
  a PR a session paused, so pausing alone does not hold the window.
- PR #1689 (next cause: the run works one company and stops): mark_done(reach) needs `searched`
  {id: {sprouts: n, clay: n, <source>: n}} for each job left under 3, else 409 naming the jobs. Job360 checks the
  record is there and logs its counts (people_run_searched); it never judges effort. Failing test first; CI ends
  about 00:13, after the window.

## 23:56 run  (score n/a: nothing due)
- Achieved: the run obeyed Job360 in 6 s: "People hunt not due. The next reach and network run is at 00:57."
- Missed: a whole hour of people search. The 22:56 failed marks (22:57:41) backed both duties off 2h.
- Biggest cause: still the 22:56 failure, reason unknown here. It was a real failed mark of the main slot (last_done
  22:57:41, next due 00:57:41), not a lapsed claim (sweep_lapsed only adds a LAPSED slot at the claim time).
  No Job360 MCP read shows `last_reason` (only GET /api/schedule/status does), and this session has no Railway CLI.
- Fix: "Needs the owner / Kits": read `schedule_duty_done duty=reach` at 22:57:41 (its reason) in Railway logs, or
  GET /api/schedule/status as the owner. If it was network_blocked or rate_limited from one connector, that is the
  next fix (a people run with free tools left should not fail; see _ONE_TOOL_DOWN).
- #1689 (searched per job): CI green except one shard still running at 00:22; the first CI run caught a
  sourcing-era token in my log name (people_run_searched contains run_search), renamed people_search_counts.
  Auto-merge on; it should land before :51, so the 00:56 run is the first under it.
- Decision taken without the owner: none.

## 00:56 run  (score n/a: nothing due, second hour lost)
- Achieved: obeyed Job360 in 7 s ("Nothing is due for either duty ... next reach slot 00:57").
- Missed: reach for 1094-1098 (the 00:11 hunt's jobs). The run woke at 00:56:42; reach's 2h back-off ended at
  00:57:41, 59 s later. So one failed mark at 22:57 cost THREE hourly runs (22:56 failed, 23:56 and 00:56 idle).
- Biggest cause (proven in code): `waiting_after_failure` in services/profile/schedule.py checked `until > now` with
  no DUE_GRACE, while every interval check (inbox, hunt, network) uses `nxt - DUE_GRACE <= now`. A duty is stamped
  at its claim, about a minute after the :56 wake, so a back-off always ended just after the next wake.
- Fix: PR (below) does both of Kits' 01:10 orders plus this: (1) the back-off check gets DUE_GRACE; (2) an
  assistant's people run marked failed for network_blocked or rate_limited is recorded partial with that reason
  (no back-off, the reply says so); `error` stays failed; the website marks as before.
- Also seen: network was marked done at 00:50:51 by something other than the people hunt (the 00:56 run did
  nothing; last_done network 00:50:51, failures 0). Kits: which routine marked network at 00:50? It reset network's
  hour without a people run.
- reach is due now (1094-1098); the 01:56 run is the first real run under #1688 and #1689.

## 01:56 run (score ~20%, per Kits) and 02:21 run (score 40%)
- 01:56 (transcript overwritten by the 02:21 run; Kits' read): 5 jobs listed, nobody new added, network 0 of 5, the
  Doctor check and the reviewer did not run.
- 02:21 (started outside the hourly slot; reach 1093, 1099-1102; network not due): 10 people added, a draft for each.
  Job360's count: Datamatics 3, Instil 3, CGI 2, UnitedHealth Group 2, HashCase 0 = 10 of 25 (40%). mark_done
  partial (below_target) after three 409s (lookups unreported; no review), each fixed by the run. #1689 behaved:
  `searched` was needed only for the three jobs under 3.
- Missed: 15 people; all 10 reviews stored weak (claim_not_in_quote); Clay not run for 3 companies, but `searched`
  said clay 0 for them; two people the People Doctor check failed (Goodenough, Agarwal) were added anyway, with no
  re-fetch visible between the check and add_contact.
- Biggest cause: the recipe. Free lookups rarely agree, and the employer's own site (owner: that page is proof) was only
  one of the "four more" sources. The run gave its outreach helper a free-text brief, then recorded each review as ONE
  whole-note row (no page: rows), so #1688's page rows were never used and every review stored weak.
- Fix: PR #1699 (draft until 03:07): the employer's own site is the first source after warm contacts; the helper gets
  the Doctor's Outreach review row pasted in and the run records ITS rows (one per recipient sentence, page:<url>);
  `searched` leaves out a source not run. Failing recipe test first; cap kept.
- PR #1694 (back-off grace; tool-not-reached is partial): CI green; merge-cage did not re-judge after its reviewers
  passed (I cannot dispatch auto-merge.yml: 403), so main was merged in at 02:32; held as draft until 03:07.
- Not fixed, named for Kits/owner: the run overrode the People Doctor's fails. Job360 does not store that check, so
  only the recipe line ("add only those it passes") stands; a stored People check would be the structural fix.
- Decision taken without the owner: none.

## 02:56 run  (score ~55%, not marked)
- Achieved: reach Dieture 5, Spektra 5, Safran 5, Gnani.ai 5 (4 already saved), Digiflex 2; network +2 (Nethermind).
  Proof mostly from the employers' own team / leadership pages (the route #1699 makes first). 20 drafts saved.
- Missed: Tachyon (1089), CVEDIA (1090), Trackmind (1091) got nobody (Clay names, no second source; web search
  rate-limited); the run ENDED before the outreach review, so mark_done was never called for reach or network
  (both still unstamped; the next run picks them up). It again added people the People check failed (Digiflex pair).
- Biggest cause: the run overrides the People check (third and fourth people tonight) and ends its turn early.
- Fix: PR #1700 (Kits 03:05 order): add_contact from an assistant takes `people_check` pass|fail; fail is refused;
  during a scheduled people run the verdict is required. 1041 contact tests pass. Opened 03:53, CI after the window.
- #1694 / #1699: CI green and reviewers passed at 03:08, but merge-cage never re-judged them (drafts make the
  reviewers skip; marking ready re-runs them, and that run does not wake the cage). Main merged into both at 03:27;
  held as drafts through the 03:51 window; readied at 04:07. Kits: the cage does not re-judge after a
  ready-for-review reviewer run; I cannot dispatch auto-merge.yml (403).
- Decision taken without the owner: none.

## 03:56 run (transcript overwritten) and 04:07 run  (score 32% by Job360; 44% with the agency fix)
- 04:07 (started outside the hourly slot; reach 1110-1114; network not due): marked partial, 11 people, a draft each.
  Engage People 5 (team page), FirstWork 3 (team page), David Joseph 2 recruiters (Sprouts + Clay agree, same
  LinkedIn URL), BCU 1, Evnek 0. Job360 scored 8 of 25: it gave David Joseph 0 although both recruiters are on it.
- Missed: Evnek and PBL Care (lookups never agreed); all 11 reviews stored weak (the run still records whole-note
  rows; #1699 not yet merged); the run did not add the people the People check failed this time (good).
- Biggest cause found: a scoring bug. _people_score keyed people by company only; the job reads "David Joseph &
  Company (recruiting for a Series B enterprise AI company)" and the recruiters "David Joseph & Company", so an
  agency job's named recruiters (the owner's agency target) never counted.
- Fix: PR #1704: a job's count is everyone at its company plus everyone saved on the job, each once. Failing test
  first ({'1': 0} -> {'1': 2}). Pushed 04:24, CI before the window.
- #1700 (people_check gate) merged 04:15: live for the 04:56 run.
- #1694 / #1699: green, reviewers passed, readied 04:07 with auto-merge, still blocked at 04:21 (merge-cage did not
  re-judge again). Main merged in at 04:32; held as drafts through the 04:51 window; readied at 05:07.
- Decision taken without the owner: none.
