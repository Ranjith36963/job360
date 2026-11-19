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
