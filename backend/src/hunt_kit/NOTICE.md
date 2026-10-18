# Notice and attribution
<!-- doc: REFERENCE — licences and credits for the hunt kit's lists, data and patterns -->

## Company lists (`companies.json`)

Merged from three MIT-licensed open-source projects. Only public company board
names and tokens were taken (no code, no scraped job data). The `source` field of
each row says where it came from. Full licence texts follow below.

- **career-ops** (career-ops-hq/career-ops), `career-ops` rows
- **jobsync** (Gsync/jobsync), `jobsync` rows
- **job-board-aggregator** (Feashliaa/job-board-aggregator), `job-board-aggregator` rows (Greenhouse, Lever, Ashby, BambooHR and Workday boards)
- `harvest` rows: board addresses found in public job lists on GitHub (SimplifyJobs New-Grad-Positions and Summer2027-Internships, vanshb03 Summer2027-Internships, crypto-jobs-fyi crawler) by `backend/scripts/harvest_hunt_boards.py`. Only the company name and board address were kept. The script is a Python re-write of freehire's `scripts/harvest_boards.py` and `scripts/ats_boards.py` (MIT, licence below).

## Methods

The endpoint patterns for the extra company-board types (Workday, Workable, Recruitee, Personio,
Teamtailor, BambooHR) and the ideas of country-eligibility locks, board discovery, liveness checks
and a seen-jobs history follow the MIT-licensed career-ops (santifer, career-ops-hq) and
ai-job-search projects. Apart from the re-writes named below, the code in this folder is original.

- `kit_ats.py`: endpoint patterns for Workday CXS, Workable, Recruitee, Personio, Teamtailor and BambooHR adapted from career-ops providers (https://github.com/career-ops-hq/career-ops, MIT licence); no code copied. Workday's pattern was also checked against ApplyPilot (AGPL) as a pattern-only reference; no code copied.
- `kit_ats.py`: the iCIMS, Oracle Recruiting Cloud, SuccessFactors, Taleo, Jobvite and Eightfold readers are Python re-writes of career-ops `providers/icims.mjs`, `oraclecloud.mjs`, `successfactors.mjs`, `taleo.mjs`, `jobvite.mjs` and `eightfold.mjs` (MIT, licence below). Same public endpoints and fields; page caps are lower and the kit sends its own honest User-Agent.

- `hunt_kit.py` `classify_liveness` (the `--check-live` check): the check order, the closed-posting phrases (English, German, Dutch, Spanish, French) and the "a promise is not a closed notice" clause guard follow career-ops `liveness-core.mjs` (MIT, commit 1d5e39a: `HARD_EXPIRED_PATTERNS` lines 24-84, `normalizeForMatch` 13-21, bot walls 124-134, `classifyLiveness` 224-306); the rule that only a positive death signal closes a job, and the Russian and "job post is closed" phrases, follow freehire `internal/job/liveness/liveness.go` (MIT, commit 615cf00, lines 49-108; upstream has since moved). Re-written in Python; the phrases are kept as the references spell them.
- `hunt_kit.py` dead-board memory (`--retry-dead`): the 3-miss limit, the 30-day recheck and the rule that only a real 404 counts follow career-ops `dead-boards.mjs` (MIT, lines 9-10, 31-53). The history-file field and the handling of throttled runs are the kit's own.
- `hunt_kit.py` `--why-missed`: the idea (a closed set of reasons, one proposed change, nothing edited, no network) and the board-from-link table follow career-ops `coverage-check.mjs` (MIT, lines 1-11, 40-50, 54-82) and, for the other boards, `backend/scripts/harvest_hunt_boards.py`. The verdict names are the kit's own.

## Search guidance (README "Search every source", "Indeed and LinkedIn")

Instructions only, no code copied. The `site:` query idea follows career-ops (MIT, `modes/scan.md` "Level 3"); the social "hiring" query follows job-apply-plugin (MIT, `skills/job-search/SKILL.md`); the low-volume LinkedIn logged-out pages and the stop-at-a-login-page rule follow ai-job-search (MIT, `linkedin-search` and `/add-portal`) and JobSpy (MIT). Connector addresses are the public endpoints published by freehire, Himalayas and FoundRole.

## Feeds (`kit_feeds.py`)

Open feeds are read through their public APIs. Link back to the original listing when you show a job.

- Hacker News "Who is hiring" via the Algolia HN Search API (user-submitted public posts)
- Jobicy API v2 (link back, do not over-call)
- Working Nomads exposed jobs API
- We Work Remotely RSS (link back)
- The Muse public API v2
- Arbeitnow job board API (attribute Arbeitnow)
- Himalayas jobs API (attribution and link back)
- Remotive API (link back and credit; do not redistribute the feed)
- RemoteOK API (link back and mention the source; kept in `extra.attribution`)
- Adzuna API (needs your own free key)
- Jooble API (needs your own free key)
- National job services: Bundesagentur fuer Arbeit Jobsuche API (public client id from its own site), Arbetsformedlingen JobTech search API, MyCareersFuture search API, Job Bank search feed. Endpoints and field names follow career-ops' providers (MIT: `arbeitsagentur.mjs`, `platsbanken.mjs`, `mycareersfuture.mjs`, `jobbankca.mjs`); no code copied. Link back to the original listing.
- `kit_gov.py` (more national job services; Python re-writes of the endpoints, parameters and field names below, no code copied; unofficial site APIs, link back to the original listing). Each fetcher cites file and line in its header comment. All MIT; licence texts are in each repository.
  - freehire (https://github.com/strelov1/freehire, commit 615cf00): `internal/ingest/sources/eures.go`, `jobnet.go`, `tyomarkkinatori.go`, `trudvsem.go` (EURES, Jobnet, Job Market Finland, Rabota Rossii)
  - career-ops (https://github.com/career-ops-hq/career-ops, commit 1d5e39a): `providers/vdab.mjs`, `providers/jobstreet.mjs` (VDAB incl. its public front-end key; SEEK, Jobstreet, JobsDB)
  - ai-job-search (https://github.com/MadsLorentzen/ai-job-search, MIT, Copyright (c) 2026 Mads Lorentzen, commit 407dc34): `.agents/skills/jobnet-search/cli/src/commands/search.ts`, `helpers.ts` (Jobnet search parameters and the `x-csrf` header)
- SimplifyJobs New-Grad-Positions and Summer2027-Internships `listings.json` on GitHub (public lists, no licence file; read at run time, never stored; link back to the original listing). The idea of reading them follows freehire (MIT, `scripts/harvest_boards.py`).

- Keyed official APIs (`kit_keyed.py`): Reed, USAJOBS and WhatJobs. Endpoints, parameters, auth headers, paging rules and field names follow freehire (https://github.com/strelov1/freehire, MIT licence, licence text below), commit 615cf00, files `internal/ingest/sources/reed.go`, `usajobs.go`, `whatjobs.go` and the response shapes in `reed_test.go`, `whatjobs_test.go` and `testdata/reed_search.json`, `testdata/usajobs_search.json`. Python re-write; no code copied. Each needs your own free key, read only from the environment. Link back to the original listing.

Patterns studied from career-ops (MIT) and ai-job-search (MIT); no code copied.

## Place data (`places.json`, built by `build_places.py`)

- Country names, ISO codes and UN regions: datasets/country-codes (https://github.com/datasets/country-codes), Public Domain Dedication and License (PDDL).
- City and region names: GeoNames (https://www.geonames.org), licensed under Creative Commons Attribution 4.0 (https://creativecommons.org/licenses/by/4.0/). Data was filtered by population and reformatted.

---

## career-ops (MIT)

MIT License

Copyright (c) 2026 Santiago Fernández de Valderrama

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## jobsync

MIT License

Copyright (c) 2024 gsync

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## job-board-aggregator

MIT License

Copyright (c) 2026 Riley Dorrington

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## freehire (MIT)

MIT License

Copyright (c) 2026 freehire contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
