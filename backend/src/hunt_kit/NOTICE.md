# Notice and attribution
<!-- doc: REFERENCE — licences and credits for the hunt kit's lists, data and patterns -->

## Company lists (`companies.json`)

Merged from three MIT-licensed open-source projects. Only public company board
names and tokens were taken (no code, no scraped job data). The `source` field of
each row says where it came from. Full licence texts follow below.

- **career-ops** (career-ops-hq/career-ops), `career-ops` rows
- **jobsync** (Gsync/jobsync), `jobsync` rows
- **freehire** (strelov1/freehire, MIT), `freehire` rows: Avature, PageUp, Comeet, HiBob and Freshteam boards that freehire's own tests and adapter comments name (`internal/ingest/sources/*_test.go`, `avature.go`); only the company name and board address were kept.
- **job-board-aggregator** (Feashliaa/job-board-aggregator), `job-board-aggregator` rows (Greenhouse, Lever, Ashby, BambooHR and Workday boards)
- **freehire** (strelov1/freehire, MIT, commit bed5ec9), `freehire` rows: company names and board addresses that appear in its adapter tests, adapter docs and design notes (OpenCATS, Odoo, recruiting-solutions.org, TeamEx, werecruit and the other `kit_ats_misc2.py` types). Only the name and board address were kept.
- `harvest` rows: board addresses found in public job lists on GitHub (SimplifyJobs New-Grad-Positions and Summer2027-Internships, vanshb03 Summer2027-Internships, crypto-jobs-fyi crawler) by `backend/scripts/harvest_hunt_boards.py`. Only the company name and board address were kept. The script is a Python re-write of freehire's `scripts/harvest_boards.py` and `scripts/ats_boards.py` (MIT, licence below).

- `nl-ind` and `gb-ho` rows, and the `sponsor_register` and `match` fields (and `country` on those rows) on any row: employers from official visa-sponsor registers, matched to their public job boards by `build_companies.py`. Only the employer name was used (to find the board); no register data is shipped beyond the register id and country.
  - Netherlands: IND (Immigratie- en Naturalisatiedienst) "Public register Work", recognised sponsors for regular labour and highly skilled migrants, https://ind.nl/en/public-register-recognised-sponsors/public-register-work (public register published by the Dutch government; read in October 2026).
  - United Kingdom: Home Office "Register of licensed sponsors: workers", https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers, Skilled Worker route rows. Contains public sector information licensed under the Open Government Licence v3.0 (https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
  - United Kingdom: Companies House "Free Company Data Product" (BasicCompanyDataAsOneFile), https://download.companieshouse.gov.uk/en_output.html, used only to keep UK sponsors whose SIC codes start with 62, 63 or 72. Contains public sector information licensed under the Open Government Licence v3.0.
  - Board endpoints are the ones the kit already reads (see Methods); the slug-probe idea is the kit's own `--discover`, limited to the two strongest names.

- `commoncrawl` rows (Keka Hire `keka` and Zoho Recruit `zohorecruit` boards, mostly Indian employers; `country` `IN` on these rows means most of the board's jobs were in India when `build_india.py` read it): the board host names come from the Common Crawl URL index (https://index.commoncrawl.org/, keyless CDX API; indexes CC-MAIN-2026-39, CC-MAIN-2025-51 and CC-MAIN-2025-30, query `*.keka.com/careers*`), used under the Common Crawl Terms of Use (https://commoncrawl.org/terms-of-use). Only the host names were kept (seed list `india_boards.txt`); no crawl content is used or shipped. `build_india.py` reads each seed board once with the kit's own reader (at most 3 at a time) and keeps boards that list at least one job; the employer name is the one the board's own careers page shows, else the tenant name.

## Methods

The endpoint patterns for the extra company-board types (Workday, Workable, Recruitee, Personio,
Teamtailor, BambooHR) and the ideas of country-eligibility locks, board discovery, liveness checks
and a seen-jobs history follow the MIT-licensed career-ops (santifer, career-ops-hq) and
ai-job-search projects. Apart from the re-writes named below, the code in this folder is original.

- `kit_ats.py`: endpoint patterns for Workday CXS, Workable, Recruitee, Personio, Teamtailor and BambooHR adapted from career-ops providers (https://github.com/career-ops-hq/career-ops, MIT licence); no code copied. Workday's pattern was also checked against ApplyPilot (AGPL) as a pattern-only reference; no code copied.
- `kit_ats.py`: the iCIMS, Oracle Recruiting Cloud, SuccessFactors, Taleo, Jobvite and Eightfold readers are Python re-writes of career-ops `providers/icims.mjs`, `oraclecloud.mjs`, `successfactors.mjs`, `taleo.mjs`, `jobvite.mjs` and `eightfold.mjs` (MIT, licence below). Same public endpoints and fields; page caps are lower and the kit sends its own honest User-Agent.

- `kit_ats_more.py`: the Rippling, Breezy, Pinpoint, Phenom, UKG UltiPro, ADP Workforce Now, Cornerstone, Gem, JazzHR, Softgarden, JOIN and Jibe readers are Python re-writes of career-ops `providers/rippling.mjs`, `breezy.mjs`, `pinpoint.mjs`, `phenom.mjs`, `ultipro.mjs`, `adp-workforcenow.mjs`, `csod.mjs`, `gem.mjs`, `jazzhr.mjs`, `softgarden.mjs`, `join.mjs` and `jibeapply.mjs` (https://github.com/career-ops-hq/career-ops, MIT, commit 1d5e39a), plus the Pinpoint description fields from freehire `internal/ingest/sources/pinpoint.go` and the Paylocity reader from freehire `internal/ingest/sources/paylocity.go` (with `bracketSlice` in `nextflight.go` and the fixtures in `paylocity_test.go`) (https://github.com/strelov1/freehire, MIT, commit bed5ec9 for the Paylocity files; commit 615cf00 for Pinpoint). Same public endpoints and fields; the kit sends its own honest User-Agent and caps pages lower. The tests use the sample responses in career-ops `tests/providers/*.test.mjs`. The `companies.json` rows with `source` `career-ops` for these boards come from career-ops `templates/portals.example.yml` (and its provider tests for Breezy and Pinpoint).
- `kit_ats_misc1.py`: the Betterteam, CATS (catsone), ClinchTalent, Crelate, eRecruiter, gr8people, HR-Manager, iSmartRecruit, Loxo and Mindsight readers are Python re-writes of the endpoints, selectors and field names in freehire `internal/ingest/sources/betterteam.go`, `catsone.go`, `clinch.go`, `crelate.go`, `erecruiter.go`, `gr8people.go`, `hrmanager.go`, `ismartrecruit.go`, `loxo.go`, `mindsight.go` (with `jsonld.go`, `schema.go`, `helpers.go` and the fixtures in their `_test.go` files) (https://github.com/strelov1/freehire, MIT, commit bed5ec9), no code copied. Each reader cites file and line in its header comment and marks what the reference does not state as NOT IN REFERENCE. The test fixtures are written by hand in the shape of those tests, with placeholder values.
- `kit_ats_extra.py` (Avature, PageUp, Comeet, HiBob and Freshteam; Python re-writes of the endpoints, parameters, headers and field names in freehire `internal/ingest/sources/avature.go`, `pageup.go`, `comeet.go`, `hibob.go`, `freshteam.go` (MIT, commit bed5ec9) and career-ops `providers/avature.mjs`, `pageup.mjs`, `comeet.mjs` (MIT), no code copied; unofficial public endpoints, link back to the original posting). Each reader cites file and line in its header comment, or says NOT IN REFERENCE. MIT; the licence texts are in each repository.
- `hunt_kit.py` `classify_liveness` (the `--check-live` check): the check order, the closed-posting phrases (English, German, Dutch, Spanish, French) and the "a promise is not a closed notice" clause guard follow career-ops `liveness-core.mjs` (MIT, commit 1d5e39a: `HARD_EXPIRED_PATTERNS` lines 24-84, `normalizeForMatch` 13-21, bot walls 124-134, `classifyLiveness` 224-306); the rule that only a positive death signal closes a job, and the Russian and "job post is closed" phrases, follow freehire `internal/job/liveness/liveness.go` (MIT, commit 615cf00, lines 49-108; upstream has since moved). Re-written in Python; the phrases are kept as the references spell them.
- `hunt_kit.py` dead-board memory (`--retry-dead`): the 3-miss limit, the 30-day recheck and the rule that only a real 404 counts follow career-ops `dead-boards.mjs` (MIT, lines 9-10, 31-53). The history-file field and the handling of throttled runs are the kit's own.
- `hunt_kit.py` `--why-missed`: the idea (a closed set of reasons, one proposed change, nothing edited, no network) and the board-from-link table follow career-ops `coverage-check.mjs` (MIT, lines 1-11, 40-50, 54-82) and, for the other boards, `backend/scripts/harvest_hunt_boards.py`. The verdict names are the kit's own.

- `kit_ats_more.py`: the Keka Hire and Zoho Recruit readers are original. Their endpoints come from our own read of the boards' public career pages in October 2026 (Keka: `<tenant>.keka.com/careers/` and its `/careers/api/embedjobs/default/active/<id>` list, the only path its robots.txt allows; Zoho Recruit: `<tenant>.zohorecruit.in|com/jobs/Careers`, allowed by its robots.txt). No third-party code.
- `kit_ats_more.py`: the RippleHire reader is original, from our own read of public RippleHire career sites in October 2026 (`<tenant>.ripplehire.com/candidate/candidatejobsearch` and `/candidate/candidatejobdetail`, the calls the public career page makes). Its robots.txt redirects to a login page, so no rules are published. No third-party code.

## Search guidance (README "Search every source", "LinkedIn, Glassdoor, Google Jobs and Indeed")

Instructions only, no code copied. The `site:` query idea follows career-ops (MIT, `modes/scan.md` "Level 3"); the social "hiring" query follows job-apply-plugin (MIT, `skills/job-search/SKILL.md`); the low-volume LinkedIn logged-out pages and the stop-at-a-login-page rule follow ai-job-search (MIT, `linkedin-search` and `/add-portal`) and JobSpy (MIT); the script reader for them is `kit_bigboards.py` (below). Connector addresses are the public endpoints published by freehire, Himalayas and FoundRole.

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
  - NAV arbeidsplassen (`nav`, Norway; Nav, the Norwegian Labour and Welfare Administration): no reference repo. The endpoint `https://arbeidsplassen.nav.no/stillinger/api/search` (params `q`, `size`, `from`, `sort=published`) and its field names were read off the live service on 2026-10-19; no key, no login; robots.txt is `User-agent: *` / `Disallow:` (everything allowed). Only the ad title, employer, place, date, one-sentence summary and work language are read; each job links back to `arbeidsplassen.nav.no/stillinger/stilling/<uuid>`. TERMS TO KNOW (https://arbeidsplassen.nav.no/vilkar-og-retningslinjer, read 2026-10-19): the employer-services terms say that use of automated services (robots, spiders, indexing) and other methods of systematic or regular copying of the content on arbeidsplassen.no is not allowed without Nav's explicit written consent. This reader is low volume (at most 5 titles x 2 pages per run, run by the user for their own hunt, nothing stored or republished), but it is an unofficial site API. Nav also offers an official free API for job ads (https://arbeidsplassen.nav.no/vilkar-api: "everyone may use it"; its terms let consumers republish ads and require removing them when they go inactive); that feed is at pam-stilling-feed.nav.no and needs a bearer token (Nav publishes a public one at /api/publicToken), so it was not used here (the hunt kit takes no keys). Contact: nav.team.arbeidsplassen@nav.no. Owner decision 2026-10-19: ship this low-volume reader (1.5 s between requests, stops at the first refusal) with the terms clause known; remove it if Nav objects.
- `kit_regional.py` (India and Gulf job boards: Naukri, Bayt, GulfTalent; Python re-writes of the endpoints, parameters, headers and field names below, no code copied; unofficial site endpoints, link back to the original listing). Each fetcher cites file and line in its header comment. Both MIT; licence texts are in each repository.
- `kit_sites_a.py` (Djinni, remote.com, WORK180, WUZZUF, InfoJobs, Instaffo, JobiDanmark and the Deel and Dover employer boards; Python re-writes of the endpoints, parameters, headers and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/djinni.go`, `remotedotcom.go`, `work180.go`, `wuzzuf.go`, `infojobs.go`, `instaffo.go`, `jobdanmark.go`, `deel.go`, `dover.go`, `nextflight.go` and `internal/ingest/sources/AGENTS.md`, no code copied; unofficial site endpoints, link back to the original listing). Each fetcher cites file and line in its header comment. MIT; the licence text is in the repository.
  - freehire (https://github.com/strelov1/freehire, commit bed5ec9): `internal/ingest/sources/naukri.go`, `bayt.go`, `gulftalent.go`, `fingerprinthttp.go` (the Chrome header set) and the response shapes in `naukri_test.go`, `bayt_test.go`, `gulftalent_test.go` and `testdata/naukri_search.json` (Naukri Android-app search API and its headers; Bayt job pages; GulfTalent sitemap and job pages)
  - JobSpy (https://github.com/speedyapply/JobSpy, commit 1988484): `jobspy/naukri/__init__.py` (the `keyword` search parameter), `jobspy/bayt/__init__.py` and `constant.py` (Bayt title-search URL, date filter and search-card layout)
- `kit_bigboards.py` (LinkedIn, Glassdoor and Google Jobs; owner decision 2026-10-19; Python re-writes of the endpoints, parameters, headers and field names below, no code copied; unofficial site endpoints, link back to the original listing). Each fetcher cites file and line in its header comment. MIT; the licence text is in the repository.
  - JobSpy (https://github.com/speedyapply/JobSpy, commit 1988484): `jobspy/linkedin/__init__.py`, `constant.py`, `util.py` (the logged-out guest search, its card layout, the empty-page marker, the job page's description and criteria), `jobspy/glassdoor/__init__.py`, `constant.py`, `model.py` (location lookup, the `/graph` search and details queries, the per-country hosts, the response fields) and `jobspy/google/__init__.py`, `constant.py`, `util.py` (the `udm=8` search, the `callback:550` paging, the ad layout, the two header sets and the page's own async parameter). Not taken: the CSRF token value JobSpy hard-codes for Glassdoor (the kit asks Glassdoor's own public page for one), proxies, and any Indeed code. Low volume by design: 5 searches, 3 pages and 10 detail fetches per site per run, 5 seconds apart, and the site stops at the first non-200, login or CAPTCHA page. These sites' terms bar automated access: the risk is a block.
- `kit_bigco.py` (Amazon, Google, Apple, Meta, IBM and Uber career sites; Python re-writes of the endpoints, parameters and field names of freehire `amazon.go`, `google.go`, `apple.go`, `metacareers.go`, `ibm.go`, `uber.go` (https://github.com/strelov1/freehire, commit bed5ec9) and career-ops `providers/amazon.mjs`, `google.mjs`, `ibm.mjs` (commit 1d5e39a), no code copied; each company's own front-end endpoints, link back to the original posting). Each fetcher cites file and line in its header comment. MIT; the licence texts are in each repository.
- `kit_startups.py` (Wellfound, Work at a Startup, Getro, Built In and Welcome to the Jungle; Python re-writes of the endpoints, parameters, headers and field names in freehire (MIT, `internal/ingest/sources/wellfound.go`, `workatastartup.go`, `getro.go`) and career-ops (MIT, `providers/getro.mjs`, `builtin.mjs`, `wttj.mjs`), no code copied except the `__NEXT_DATA__` regular expression from career-ops `getro.mjs:123` and the Built In card regular expressions from `builtin.mjs:123, :129-135`; unofficial site endpoints, link back to the original listing). Each fetcher cites file and line in its header comment. MIT; the licence text is in the repository.
- `kit_ats_eu.py` (Emply, Factorial, Jobylon, Manatal, PeopleForce, TalentAdore, TalentHR, TalentLyft, Traffit and Trakstar boards; Python re-writes of the endpoints, parameters and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/emply.go`, `factorial.go`, `jobylon.go`, `manatal.go`, `peopleforce.go`, `talentadore.go`, `talenthr.go`, `talentlyft.go`, `traffit.go`, `trakstar.go`; no code copied; each employer's own public career-site endpoints, link back to the original posting). Each fetcher cites file and line in its header comment. The `freehire` rows of `companies.json` are board addresses that appear in freehire's test files, source comments and `openspec/changes/archive/2026-07-01-add-traffit-source/design.md` (its 14 validated Traffit tenants); the `career-ops` Factorial row (`careers.factorialhr.com`) is career-ops `templates/portals.example.yml:2385` (MIT). The kit has not re-probed any of them. MIT; the licence text is in the repository.
- `kit_pubsec.py` (NEOGOV, AppliTrack, SchoolSpring and EDJOIN boards; Python re-writes of the endpoints, parameters and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/neogov.go`, `applitrack.go`, `schoolspring.go`, `edjoin.go` and career-ops (MIT, commit 8f48928) `providers/neogov.mjs`, `applitrack.mjs`, `schoolspring.mjs`; no code copied; each employer's or platform's own public endpoints, link back to the original posting). Each fetcher cites file and line in its header comment. PeopleSoft (`peoplesoft.go`, `peoplesoft.mjs`) is not read: its list needs a cookie session the kit's `http` cannot carry. The `freehire` and `career-ops` rows of `companies.json` for these types are board addresses from the references' tests and comments (freehire `neogov_test.go` Cochise College, `applitrack.go` header saskatoon, `schoolspring_test.go` keyword "network administrator", `edjoin_test.go` job type 25; career-ops `templates/portals.example.yml:3152-3171` Clark College, Camas, Vancouver Public Schools); the kit has not re-probed them. MIT; the licence texts are in the repositories.
- `kit_ats_misc3.py` (Radancy/TalentBrew, UKG Ready, eArcu, BeeSite and Workable marketplace boards; Python re-writes of the endpoints, parameters and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/radancy.go`, `ukgready.go`, `earcu.go`, `beesite.go`, `workablemarketplace.go` and career-ops (MIT, commit 8f48928) `providers/radancy.mjs`; no code copied; each employer's or platform's own public endpoints, link back to the original posting). Each fetcher cites file and line in its header comment. HumanBit (`humanbit.go`) is not read: its listing page is about 5.6 MB of Next.js flight data, over the kit's page-size limit. BeeSite posting pages on the employer's own domain are not opened (they are off the board's host). The `freehire` and `career-ops` rows of `companies.json` for these types are board addresses from the references' tests and comments (`ukgready_test.go` Albanese Confectionery and Rapid Robert's, `radancy_test.go` AstraZeneca, `radancy.mjs` Munich Re, `earcu_test.go` Cambridge University Press & Assessment, `beesite_test.go` Mercedes-Benz and Porsche, `workablemarketplace_test.go` Intuition Machines). MIT; the licence texts are in each repository.
- `kit_ats_misc4.py` (ADP MyJobs and Eploy boards; Python re-writes of the endpoints, parameters, headers and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/adpmyjobs.go`, `eploy.go` and career-ops (MIT, commit 8f48928) `providers/eploy.mjs`; no code copied; each platform's own public endpoints, link back to the original posting). Each fetcher cites file and line in its header comment. Cornerstone (`csod.mjs`) and Jibe (`jibe.go`) are already read by `kit_ats_more.py`. Dayforce (`dayforce.go`) is not read: its listing POST needs a cookie-persisting client (next-auth CSRF cookie echoed in a header) that the kit's `http` does not have. The `freehire` rows of `companies.json` for these types are board addresses from freehire's tests (`adpmyjobs_test.go` stellantisexternalcx; `eploy_test.go` careers.pei.group); the kit has not re-proved them live.
- `kit_ats_misc5.py` (SelfRecruit, Enlizt, Hurma, Compleo and Spark boards; Python re-writes of the endpoints, parameters, headers and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/selfrecruit.go`, `enlizt.go`, `hurma.go`, `compleo.go`, `spark.go`; no code copied; each platform's own public endpoints, link back to the original posting). Each fetcher cites file and line in its header comment. Zoho Recruit (`zoho.go`, career-ops `providers/zoho-recruit.mjs`) is not read a second time: the `zohorecruit` reader in `kit_ats_more.py` already covers it. The `freehire` rows of `companies.json` for these types are board addresses from freehire's tests (`selfrecruit_test.go` dressup, `enlizt_test.go` tributodevido, `hurma_test.go` scrumlaunch, `compleo_test.go` wafx, `spark_test.go` volo); the kit has not re-probed them. MIT; the licence text is in the repository.
- `kit_latam.py` (Gupy, HiringRoom, Recrutei, InHire, Solides and Vagas.com.br boards; Python re-writes of the endpoints, parameters, headers and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/gupy.go`, `hiringroom.go`, `recrutei.go`, `inhire.go`, `solides.go`, `vagas.go` and career-ops (MIT, commit 8f48928) `providers/hiringroom.mjs`; no code copied; each platform's own public endpoints, link back to the original posting). Each fetcher cites file and line in its header comment. The `freehire` rows of `companies.json` for these types are board addresses from freehire's tests (`gupy_test.go` caju, origoenergiainterno; `hiringroom_test.go` stefaniniitsolutionsperu; `recrutei_test.go` digisystem; `inhire_test.go` contaazul, involves, neoway; `solides_test.go` dynamox, reivax; `vagas.go` the national board); the kit has not re-probed them. career-ops `providers/gupy.mjs` (a keyword search of the whole Gupy portal) is not read. MIT; the licence texts are in the repositories.
- `kit_asia2.py` (MokaHR, HRMOS, HERP, Quickin and PyjamaHR boards; Python re-writes of the endpoints, parameters, headers and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/mokahr.go`, `hrmos.go`, `herp.go`, `quickin.go`, `pyjamahr.go` and career-ops (MIT, commit 8f48928) `providers/mokahr.mjs`; no code copied; the AES-128-CBC step for MokaHR is a stdlib re-implementation checked against FIPS-197; each platform's own public endpoints, link back to the original posting). Each fetcher cites file and line in its header comment. HerKey (`herkey.go`) is not read. The `career-ops` rows of `companies.json` for MokaHR are the tenants named in `providers/mokahr.mjs:7-8` and `templates/portals.example.yml:2062` (DeepSeek, Moonshot AI, Zhipu AI); the `freehire` rows are `hrmos.go` (cyberagent-group), `quickin_test.go` (botcity) and `pyjamahr_test.go` (dodo-payments); the kit has not re-probed them. MIT; the licence texts are in the repositories.
- `kit_ats_misc2.py` (OpenCATS, Odoo, recruiting-solutions.org, TeamEx, Wantapply, werecruit, Workstream, WordPress/Yoast career sites, careers-page.com, Jobboardly and JobAppNetwork boards; Python re-writes of the endpoints, parameters and field names in freehire `internal/ingest/sources/opencats.go`, `odoo.go`, `recruitingsolutions.go`, `teamex.go`, `wantapply.go`, `werecruit.go`, `workstream.go`, `wpyoast.go`, `careerspage.go`, `jobboardly.go` and `jobappnetwork.go`, MIT, commit bed5ec9, no code copied; the `freehire` rows in `companies.json` for these types are board addresses from the same project; public pages and APIs, link back to the original listing). Each fetcher cites file and line in its header comment. Licence text is in the freehire repository.
- `kit_asia.py` (South-East Asian job boards: Glints, Kalibrr, Tech in Asia, ITviec; Python re-writes of the endpoints, parameters, headers and field names in freehire's kalibrr.go, techinasia.go, itviec.go and career-ops' glints.mjs, kalibrr.mjs, techinasia.mjs, itviec.mjs, no code copied; unofficial site endpoints, link back to the original listing). Each fetcher cites file and line in its header comment. Both MIT; licence texts are in each repository.
- `kit_sites_b.py` (Airswift, Brief careers page, emagine, Functional Works, Golang Jobs, Joppy, Rapyd careers, StartupAndVC and TechTree; Python re-writes of the endpoints, parameters, headers and field names in freehire (https://github.com/strelov1/freehire, commit bed5ec9, MIT) `internal/ingest/sources/airswift.go`, `briefhq.go`, `emagine.go`, `functionalworks.go`, `golangjobs.go`, `joppy.go`, `rapyd.go`, `startupandvc.go`, `techtree.go` and `pacer.go`, no code copied; unofficial site endpoints, link back to the original listing). Each fetcher cites file and line in its header comment. MIT; the licence text is in the repository.
- `kit_markets3.py` (EchoJobs, Torre, Tecla and Japan Dev; Python re-writes of the endpoints, parameters and field names in freehire (https://github.com/strelov1/freehire, commit bed5ec9, MIT) `internal/ingest/sources/echojobs.go`, `torre.go`, `tecla.go` and `japandev.go`, and of career-ops (MIT, commit 8f48928) `providers/torre.mjs`, which records that Torre's old search API is retired, no code copied; unofficial site endpoints, the job link stays on the board's own host). Gradcracker (`gradcracker.go`) is not read: it is served only to a Chrome TLS fingerprint. Each fetcher cites file and line in its header comment. MIT; the licence text is in the repository.
- `kit_ats_us.py` (US mid-market ATS boards: Bullhorn, CareerPlug, Hireology, HiringThing, HRM Direct, iSolved Hire, Paycom, Paycor, JobScore and RecruiterFlow; Python re-writes of the endpoints, parameters and field names in freehire (https://github.com/strelov1/freehire, MIT, commit bed5ec9) `internal/ingest/sources/bullhorn.go`, `careerplug.go`, `hireology.go`, `hiringthing.go`, `hrmdirect.go`, `isolvedhire.go`, `paycom.go`, `paycor.go`, `jobscore.go`, `recruiterflow.go` and the sample data in their `_test.go` files, no code copied; public board pages and APIs, link back to the original posting). Each reader cites file and line in its header comment. The `companies.json` rows with `source` `freehire` for these boards come from freehire's board lists. MIT; the licence text is in the repository.
- `kit_markets4.py` (Yourator, CareerViet and OCC Mundial boards; Python re-writes of the endpoints, parameters and field names in career-ops (https://github.com/career-ops-hq/career-ops, MIT, commit 8f48928) `providers/yourator.mjs`, `providers/careerviet.mjs`, `providers/occ.mjs`; no code copied; each site's own public pages, link back to the original posting). Each fetcher cites file and line in its header comment. AIJobs (freehire `aijobs.go`, MIT, commit bed5ec9) is not read: its list is a CSRF-protected POST that needs a cookie jar the kit's `http` cannot hold. The `career-ops` Yourator and CareerViet rows of `companies.json` are the whole-board addresses those providers describe. Licence text is in the career-ops repository.
- SimplifyJobs New-Grad-Positions and Summer2027-Internships `listings.json` on GitHub (public lists, no licence file; read at run time, never stored; link back to the original listing). The idea of reading them follows freehire (MIT, `scripts/harvest_boards.py`).
- Keyed official APIs (`kit_keyed.py`): Reed, USAJOBS and WhatJobs. Endpoints, parameters, auth headers, paging rules and field names follow freehire (https://github.com/strelov1/freehire, MIT licence, licence text below), commit 615cf00, files `internal/ingest/sources/reed.go`, `usajobs.go`, `whatjobs.go` and the response shapes in `reed_test.go`, `whatjobs_test.go` and `testdata/reed_search.json`, `testdata/usajobs_search.json`. Python re-write; no code copied. Each needs your own free key, read only from the environment. Link back to the original listing.

Patterns studied from career-ops (MIT) and ai-job-search (MIT); no code copied.

## Niche boards (`kit_niche.py`)

Endpoints, query parameters, paging and field names follow the references below, re-written in Python
(no code copied). Public JSON and RSS only: no key, no login. Link back to the original listing.

- career-ops (https://github.com/career-ops-hq/career-ops, MIT, commit 1d5e39a): `providers/` files
  `4dayweek.mjs`, `landingjobs.mjs`, `getonbrd.mjs`, `manfred.mjs`, `thehub.mjs`, `remotli.mjs`,
  `a16z-speedrun-talent.mjs`, `jobspresso.mjs`, `nodesk.mjs`, `cryptocurrencyjobs.mjs`, `pythonorg.mjs`,
  `larajobs.mjs`, `startup-jobs.mjs`, `nofluffjobs.mjs`, `justjoin.mjs`, `solidjobs.mjs`, `higheredjobs.mjs`.
- freehire (https://github.com/strelov1/freehire, MIT, commit 615cf00): `internal/ingest/sources/`
  files `fourdayweek.go` (the /api/v2 feed that robots.txt allows), `powertofly.go`, `jobstash.go`.
- Test fixtures are shaped like those projects' own test data (`tests/providers/*.test.mjs`,
  `internal/ingest/sources/*_test.go`).

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
