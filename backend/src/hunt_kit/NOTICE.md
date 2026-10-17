# Notice and attribution
<!-- doc: REFERENCE — licences and credits for the hunt kit's lists, data and patterns -->

## Company lists (`companies.json`)

Merged from three MIT-licensed open-source projects. Only public company board
names and tokens were taken (no code, no scraped job data). The `source` field of
each row says where it came from. Full licence texts follow below.

- **career-ops** (career-ops-hq/career-ops), `career-ops` rows
- **jobsync** (Gsync/jobsync), `jobsync` rows
- **job-board-aggregator** (Feashliaa/job-board-aggregator), `job-board-aggregator` rows (Greenhouse, Lever, Ashby, BambooHR and Workday boards)

## Methods

The endpoint patterns for the extra company-board types (Workday, Workable, Recruitee, Personio,
Teamtailor, BambooHR) and the ideas of country-eligibility locks, board discovery, liveness checks
and a seen-jobs history follow the MIT-licensed career-ops (santifer, career-ops-hq) and
ai-job-search projects. The code in this folder is original.

- `kit_ats.py`: endpoint patterns for Workday CXS, Workable, Recruitee, Personio, Teamtailor and BambooHR adapted from career-ops providers (https://github.com/career-ops-hq/career-ops, MIT licence); no code copied. Workday's pattern was also checked against ApplyPilot (AGPL) as a pattern-only reference; no code copied.

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
