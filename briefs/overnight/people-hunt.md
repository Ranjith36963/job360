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
