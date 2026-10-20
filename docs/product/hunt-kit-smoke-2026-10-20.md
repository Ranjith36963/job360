<!-- doc: LOG | append-only | written: 2026-10-20 by the "apply hunt potential" session (live smoke test, owner's yes in chat) -->
# Hunt kit live smoke test (2026-10-20)

**What ran:** the kit exactly as served by `https://job360.uk/api/hunt-kit` (15 files, every sha256 matched;
includes `kit_bigboards.py`, `kit_regional.py`, `kit_ats_more.py`), on the owner's home PC in London
(residential UK connection), in a new empty folder:

```
python hunt_kit.py --titles "AI Engineer" --work-countries GB,IN,AE --limit-boards 5 --stats --history none --out smoke.jsonl --max-minutes 20
```

**Result:** exit 0 after about 2 minutes; **309 jobs kept**; every kept row had a company and an http(s)
URL. Kept by source: freehire 253, linkedin 28, githublists 7, greenhouse 6, naukri 5, hn 3, gulftalent 2,
himalayas 2, bayt 2, jobstash 1. Read-only: nothing was written to Job360 and no history was kept.

## Coverage table (verbatim `--stats`)

```
source                     boards_tried        boards_ok        jobs_seen    title_matches location_matches         years_ok             kept
4dayweek                              5                5              245                0                0                0                0
adp                                   1                1                0                0                0                0                0
adzuna                                0                0                0                0                0                0                0
arbeitnow                             5                5              581                7                1                0                0
arbeitsagentur                        2                2              200               65                0                0                0
ashby                                 5                5              161                0                0                0                0
bamboohr                              5                5               37                0                0                0                0
bayt                                 21               21               44                3                2                2                2
breezy                                1                1                4                0                0                0                0
cornerstone                           1                1              239                0                0                0                0
cryptojobs                            1                1                5                0                0                0                0
eightfold                             5                5              500                0                0                0                0
eures                                 1                0                0                0                0                0                0  <- answered nothing (blocked, broken or no key)
freehire                             53               53             4942              349              298              253              253
gem                                   1                1               25                0                0                0                0
getonbrd                              4                4               14                2                0                0                0
githublists                           2                2              204               16                7                7                7
glassdoor                             1                0                0                0                0                0                0  <- answered nothing (blocked, broken or no key)
googlejobs                            4                4                0                0                0                0                0
greenhouse                            5                4              769               12               12                7                6
gulftalent                           28               28               25                3                2                2                2
higheredjobs                          1                1                0                0                0                0                0
himalayas                             5                5               72               11                2                2                2
hn                                    2                2              198                5                3                3                3
icims                                 5                4              204                0                0                0                0
jazzhr                                1                1               12                0                0                0                0
jibe                                  1                1              100                0                0                0                0
jobbank                               2                2                3                0                0                0                0
jobicy                                1                1               50                0                0                0                0
jobnet                                1                1               57                8                0                0                0
jobspresso                            1                1                1                0                0                0                0
jobstash                              5                5              481                3                1                1                1
jobstreet                            11               10              287               82                0                0                0
jobtech                               2                2              115               10                0                0                0
jobvite                               5                5              730                3                0                0                0
join                                  5                5               49                0                0                0                0
jooble                                0                0                0                0                0                0                0
justjoin                              5                5               57                3                0                0                0
keka                                  5                5               85                0                0                0                0
landingjobs                           1                1                0                0                0                0                0
larajobs                              1                1                0                0                0                0                0
lever                                 5                4               60                0                0                0                0
linkedin                             10                9               90               28               28               28               28
manfred                               1                1                9                0                0                0                0
mycareersfuture                       2                2              200               93                0                0                0
naukri                                2                2              200                5                5                5                5
nav                                   2                2               50                3                0                0                0
nodesk                                1                1                0                0                0                0                0
nofluffjobs                           5                5               42                0                0                0                0
oraclecloud                           5                5             4115                0                0                0                0
paylocity                             5                5               27                0                0                0                0
personio                              5                5               40                0                0                0                0
phenom                                1                1              917                3                0                0                0
pinpoint                              1                1                4                0                0                0                0
powertofly                            5                5               81                0                0                0                0
pythonjobs                            1                1               18                0                0                0                0
recruitee                             5                5               74                0                0                0                0
reed                                  0                0                0                0                0                0                0
remoteok                              1                1               23                0                0                0                0
remotive                              1                1               10                0                0                0                0
remotli                               5                5              133                2                0                0                0
smartrecruiters                       5                5             7324                2                0                0                0
softgarden                            1                1               81                1                0                0                0
solidjobs                             1                1               88                1                0                0                0
speedrun                              3                3               45                8                0                0                0
startupjobs                           1                1                5                0                0                0                0
taleo                                 5                1               10                0                0                0                0
teamtailor                            5                5               89                0                0                0                0
thehub                                6                6                3                1                0                0                0
themuse                               5                5               10                1                0                0                0
trudvsem                              5                5                0                0                0                0                0
tyomarkkinatori                       2                2               35                0                0                0                0
ultipro                               1                1               26                0                0                0                0
usajobs                               0                0                0                0                0                0                0
vdab                                  2                2              194                7                0                0                0
whatjobs                              0                0                0                0                0                0                0
workable                              5                3               33                0                0                0                0
workday                               5                4               50                0                0                0                0
workingnomads                         1                1               16                0                0                0                0
wwr                                   4                4               73                0                0                0                0
zohorecruit                           2                2                7                0                0                0                0
boards_listed: adp=1, ashby=3647, bamboohr=11316, breezy=1, cornerstone=1, eightfold=5, gem=1, greenhouse=8447, icims=10110, jazzhr=1, jibe=1, jobvite=12, join=36, keka=300, lever=4479, oraclecloud=77, paylocity=10252, personio=29, phenom=1, pinpoint=1, recruitee=42, smartrecruiters=296, softgarden=1, taleo=8, teamtailor=9, ultipro=1, workable=84, workday=6899, zohorecruit=2
boards_this_run: adp=1, ashby=5, bamboohr=5, breezy=1, cornerstone=1, eightfold=5, gem=1, greenhouse=5, icims=5, jazzhr=1, jibe=1, jobvite=5, join=5, keka=5, lever=5, oraclecloud=5, paylocity=5, personio=5, phenom=1, pinpoint=1, recruitee=5, smartrecruiters=5, softgarden=1, taleo=5, teamtailor=5, ultipro=1, workable=5, workday=5, zohorecruit=2
total kept: 309   skipped as already seen: 0   boards skipped as dead: 0
```

## Warnings on stderr (verbatim, all of them)

```
warning: eures answered HTTP 307 on page 1 (nothing read)
warning: glassdoor stopped for this run: HTTP 403 (nothing read)
warning: linkedin stopped for this run: HTTP 429 (nothing read)
```

## Sample jobs (one per source)

| source | company | title | place | url |
|---|---|---|---|---|
| feed:linkedin | Ageas UK | AI Engineer | London, UK | https://www.linkedin.com/jobs/view/4480251187 |
| feed:naukri | Coforge | Gen AI Engineer | Hyderabad, Pune, Greater Noida, India | https://www.naukri.com/job-listings-gen-ai-engineer-coforge-hyderabad-pune-greater-noida-6-to-11-years-201026004713 |
| feed:gulftalent | Boston Consulting Group | Forward Deployed AI Engineer | Dubai, UAE | https://www.gulftalent.com/uae/jobs/forward-deployed-ai-engineer-648509 |
| feed:bayt | techcarrot | AI Engineer | Dubai, UAE | https://www.bayt.com/en/uae/jobs/ai-engineer-75330476/ |
| freehire | Barclays | AI Engineer | Glasgow, UK | https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays/job/Glasgow-Campus/AI-Engineer_JR-0000113706-1 |
| greenhouse | Anthropic | Applied AI Engineer, Startups | London / visa_onsite rows | https://job-boards.greenhouse.io/anthropic/jobs/5432575008 |

## Problems found (one row each)

| # | source or step | what happened (verbatim) | likely cause | proposed fix |
|---|---|---|---|---|
| 1 | eures | `warning: eures answered HTTP 307 on page 1 (nothing read)`; stats row `1 0 0` | The EURES search endpoint now redirects (307), and the reader treats any non-200 as nothing. | Follow a same-host 307/308 (a POST keeps its body), or point the reader at the redirect target; add a test with a 307 fixture. |
| 2 | glassdoor | `warning: glassdoor stopped for this run: HTTP 403 (nothing read)` | Glassdoor refuses scripted requests even from a residential UK connection. | The stop-on-403 works. Consider leaving it out of the default `--sources`, so every run doesn't report a dead source. |
| 3 | linkedin | `warning: linkedin stopped for this run: HTTP 429 (nothing read)` after 9 of 10 searches answered (28 jobs kept) | The guest search rate limit hit on the 10th request. | The stop works. Make the default run fewer searches or pause longer between them, so a normal run stays under the limit. |
| 4 | googlejobs | stats row `googlejobs 4 4 0`: 4 requests answered, **0 jobs seen** | Responses arrive but the parser finds nothing (layout change, a consent page, or results built by JavaScript). | Save one live response as a fixture, see what it holds, fix the parser; until then say "returns nothing yet" in the README. |
| 5 | naukri (years) | `Coforge Gen AI Engineer` (URL says `6-to-11-years`) kept with `years_min=None`; `Prodapt … 12-to-18-years` kept with `years_min=3`. Both pass the default `--max-years 5`. | The reader does not map Naukri's own experience field into `years_min`, so the years filter reads the free text, or nothing. | Map Naukri's minimum-experience field to `years_min` (fall back to the `N-to-M-years` part of the URL); test with both rows. |
| 6 | hn (links) | `Mastra AI Engineer` → `https://github.com/mastra-ai/mastra`; `Metcor AI Engineer` → `https://metcor.co.uk` | The reader takes the first link in the comment, often the company's repo or homepage, not the posting. | Prefer links that look like a posting (ATS hosts, `/jobs/`, `/careers/`); otherwise link to the HN comment itself. |
| 7 | bayt and gulftalent (my earlier claim) | On 2026-10-19 I told the other session both returned 403 from the owner's PC. Today both answered (21/21 and 28/28) and kept real Dubai jobs. | My 2026-10-19 probe hit their public search web pages; the #918 readers use other endpoints. | Corrected here; lesson recorded in `session-lessons`. |
| 8 | step (my own) | The first download folder (`tmp/smoke`) already held `backend/` and `kit.tar` from an earlier task. | I reused a temp folder name without checking it was new and empty. | Redone in a fresh folder checked empty first; lesson recorded. |

Not problems, for the record: several national services matched titles but kept nothing (mycareersfuture 93,
jobstreet 82, arbeitsagentur 65 title matches) because those jobs are on-site outside GB/IN/AE and the ads do
not offer sponsorship, so the rules worked as designed. Keka saw 85 jobs on 5 boards with no "AI Engineer"
title, which is expected for 5 boards. Dead boards (taleo 1 of 5, workable 3 of 5, lever/icims/workday 4 of 5
answered) are normal for an open list. freehire gave 82% of the kept jobs because `--limit-boards 5` keeps
the company boards small on purpose.

## Rerun after #936 (same day, same PC, fresh folder `smoke2-20261020`)

Same command, kit downloaded fresh from production after #936 (Naukri years, LinkedIn budget, HN links)
went live. Exit 0, **280 lines** kept (309 the first time). Warnings, verbatim:

```
warning: eures answered HTTP 307 on page 1 (nothing read)
warning: glassdoor stopped for this run: HTTP 403 (nothing read)
warning: linkedin stopped for this run: HTTP 429 (nothing read)
```

Rows that changed against the first run (`source tried ok seen title loc years kept`):

```
linkedin      2   1    10    1   1   1    1     (first run: 10 tried, 9 ok, 28 kept)
naukri        2   2   200    6   6   2    2
hn            2   2   198    5   3   3    3
googlejobs    4   4     0    0   0   0    0
workable      5   0     0    0   0   0    0     (first run: 3 of 5 answered)
bayt         21  21    44    3   2   2    2
gulftalent   28  28    25    3   2   2    2
nav           2   2    50    3   0   0    0
keka          5   5    85    0   0   0    0
freehire     53  53  4942  350 299 254  254
```

**Fixed and seen working:**

| # | fix | proof from the rerun |
|---|---|---|
| 5 | Naukri years | `DS STREAM AI Engineer` years_min=4, URL `…-4-to-9-years-…`; `Everestek AI Engineer` years_min=3, URL `…-3-to-6-years-…`. The `6-to-11` and `12-to-18` jobs are now dropped by `--max-years 5`. |
| 6 | HN links | `Mastra` → `https://jobs.ashbyhq.com/mastra/531a3fe8-…`; `Axmed` → `https://axmed.teamtailor.com/jobs/8175859-ai-engineer`; `Metcor` (no posting link) → `https://news.ycombinator.com/item?id=49930470`. |

**Still open, and new:**

| # | source | what happened (verbatim) | likely cause | proposed fix |
|---|---|---|---|---|
| 9 | linkedin | 2 tried, 1 ok, 429 on the 2nd request | The guest limit is per IP and lasts hours: this PC used 10 requests that morning. #936's 8-request budget cannot help a second run on the same day. | Treat the 429 as "come back later" in the warning text, and note in the README that one run a day from one IP is the safe budget. |
| 10 | googlejobs | 200, 93,476 bytes, 0 jobs. Saved page has `SG_REL` ×1, `sg_trbl` ×1, `/httpservice/retry/enablejs` ×2; the parser's markers `data-async-fc` and `520084652` ×0 | Google answers a script request with its JavaScript challenge page ("SearchGuard"), not results. The block check (`_BLOCK` in `kit_bigboards.py`) does not know this page, so the run says "0 jobs" instead of "blocked". | Add `SG_REL\|sg_trbl\|/httpservice/retry/enablejs` to `_BLOCK` and stop the source with "Google JS challenge"; add this page as a negative fixture. Google Jobs then belongs in the browser path, not the script. |
| 11 | eures | Kit: 307 again. One plain `curl` POST with the kit's body: status `000` (connection dropped, no headers) | EURES drops or redirects scripted POSTs; not a parser bug. | Same as #1; do not retry in a loop. |
| 12 | workable | 5 tried, 0 answered (first run 3 of 5) | Unknown from one run: either rate limiting on the widget API or the 5 sampled boards are dead. | Capture one response before changing code. |

Glassdoor (#2) is unchanged: 403.
