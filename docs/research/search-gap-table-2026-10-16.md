<!-- doc: FROZEN | written 2026-10-16 by the cloud REA session, at the owner's request relayed by the local "apply" session -->
# Search gap table: Job360 vs the 46 references (2026-10-16)

**Only one question:** how do other tools let an AI assistant *find* jobs, and what should our hunt kit and `hunt.md` borrow so the user's own AI searches the whole world?
Job360 still never searches itself (product rule 4). Everything below is for the **user's AI**: kit code it runs, connectors it adds, or instructions it follows.

**How this was checked.**
- Repos were cloned at the commits below and every quote was matched to its line with an exact-string search. The lead re-checked 26 of them.
- Live calls were made from the cloud container on 2026-10-16.
- "web read" means a search result or a fetched page, not code. UNVERIFIED means not confirmed.
- No logins, no keys, no bot-check bypass. Hard-coded keys found in other repos are named by file and line, never copied.

| Source | Commit / date |
|---|---|
| Job360 `main` | `ea764ab` (`hunt.md`, `hunt_kit/hunt_kit.py`, `kit_ats.py`) |
| career-ops | `1d5e39a` (MIT) |
| JobSpy | `1988484` (MIT) |
| jobspy-mcp-server | `5f93ffe` (MIT) |
| ai-job-search | `407dc34` (MIT) |
| ApplyPilot | `4a8d521` (**AGPL-3.0**: borrow ideas, not code) |
| apply-potato | `1a50e8c` (no licence file found) |
| jobsync | `2bd2284` (MIT) |
| freehire | `615cf00` (MIT) |
| trackly-cli | `fd734f6` (MIT) |

## First, two corrections to what we thought we had

1. **We already read far more than 3,677 boards.**
   - `--universe` is **on by default** (`hunt_kit.py:1984` `default=True`). It downloads the open `Feashliaa/job-board-aggregator` lists (`:1536`).
   - Counted today at our pinned commit: greenhouse 8,333, lever 4,368, ashby 3,161, bamboohr 11,316, **workday 12,884**.
   - A default run reads the starter list plus about 15.9K Greenhouse, Lever and Ashby boards.
   - The real gap: **Workday and BambooHR are skipped by default** (`:91` `DEFAULT_SKIP = ("bamboohr", "workday")`), even though the Workday code exists (`kit_ats.py:164` `fetch_workday`).
2. **Two claims in the 46-refs doc were wrong.**
   - apply-potato ships **no** list of GitHub job repos. The user types them in (`.env.example:102` `GITHUB_REPOS=owner/repo@branch,...`).
   - ApplyPilot's `--continuous` re-polls its own apply queue every 60 s (`config.py:168` `"poll_interval": 60,`). It is not a job re-search.

## The gap table

Rows are ordered by how much they widen the search, most first.
"Owner decides" marks a row that touches a rule we wrote down.

| # | Competitor(s) | What they do | What Job360 lacks | What to borrow | How | Evidence | Verified |
|---|---|---|---|---|---|---|---|
| 1 | freehire | A **keyless hosted MCP** over 3.3M postings from 294K companies. Any AI adds one URL and can search | Claude.ai / ChatGPT users with no Python get only "call board APIs by hand" | Tell the assistant to add the freehire connector | `hunt.md` step 3: "No Python? Add the connector `https://freehire.me/api/v1/mcp` (no key). Tools: `search_jobs`, `get_job`, `check_jobs_open`, `search_companies`." | live `tools/list` HTTP 200, 7 tools; `freehire internal/api/handler/mcpapp.go:75` `api.All("/mcp"` | yes, live and code |
| 2 | freehire | Its REST search allows `offset+limit` up to 10,000 | The kit asks for 4 pages × 50 = **200 ads per title** | Page deeper, and use the facet filters (`countries`, `regions`, `work_mode`, `visa_sponsorship`, `posted_within_days`) | `hunt_kit.py:1474` `for page in range(4)`. Raise it, and pass `search_brief` rows as `countries` / `regions` | `hunt_kit.py:1474`, `:1486`; live: "machine learning engineer" total 41,713 | yes, live and code |
| 3 | Himalayas | A **keyless hosted MCP**, 41 tools, **125,203** remote jobs | The kit reads its feed, but no-Python users don't get it | Same as row 1 | `hunt.md`: "Add `https://mcp.himalayas.app/mcp` (no key) for remote jobs. Credit Himalayas with a link." | live `tools/list` HTTP 200, 41 tools; `himalayas.app/jobs/api` `totalCount` 125203 | yes, live |
| 4 | Indeed (official) | An official Indeed MCP, Claude connector only, beta. Tools: job search and job detail | `hunt.md:36` only says "e.g. Indeed's in Claude" | Make it a named step for Claude users | `hunt.md` step 3c: "If you have the Indeed connector, run `search_jobs` for every row's place × title, then `get_job_details`" | tool names seen in this session's tool list; docs.indeed.com returned 403 | partly: tool names yes, endpoint and regions UNVERIFIED |
| 5 | career-ops, Feashliaa, ApplyPilot | Workday is read: career-ops `providers/workday.mjs`, ApplyPilot 48 curated tenants | Workday is **off by default** | Turn Workday on, starting with a short curated list so a run fits the time budget | Drop `workday` from `DEFAULT_SKIP`, or add `--sources +workday`. Add a curated Workday list of our own; ApplyPilot is AGPL, so don't copy its list, write ours | `hunt_kit.py:91`; career-ops `providers/workday.mjs:354` `${origin}/wday/cxs/${tenant}/${site}/jobs`; ApplyPilot `discovery/workday.py:158`, 48 `tenant:` lines | yes, code |
| 6 | career-ops | **110** no-login provider modules. We have 10 board types | No iCIMS, Taleo, SuccessFactors, Oracle Cloud, Jobvite, Breezy, JazzHR, Eightfold, Phenom, Pinpoint, Rippling, Comeet, Softgarden, Zoho, UltiPro, PageUp, Gem, Getro, … | Port the big enterprise ATSs first: iCIMS, Oracle Cloud, SuccessFactors, Taleo, Jobvite, Eightfold | New fetchers in `kit_ats.py`, mirroring career-ops (MIT). iCIMS is HTML (`careers-<tenant>.icims.com/jobs/search?ss=1`). SmartRecruiters is already ours | `ls providers/*.mjs` = 110; `providers/icims.mjs:4`; `smartrecruiters.mjs:103`; `oraclecloud.mjs:23`; `taleo.mjs:3`; `jobvite.mjs:20` | yes, code |
| 7 | freehire | Grows its board list from **public** inputs: GitHub listing JSONs, `site:` web searches and LinkedIn public listings, then checks each board live | `companies.json` is a static copy | Run the same harvest to grow our list. freehire has 6,863 Greenhouse, 3,994 Ashby, 2,205 Lever, 1,480 Workable and 4,202 Workday companies | A `build_companies.py`: read the GitHub listing JSONs (row 8), regex the ATS URLs, then validate each with the board API we already call | `freehire scripts/harvest_boards.py:2`, `:43-49`; `discover_boards.py:4`; `docs/sources.md:26-39` | yes, code |
| 8 | freehire, apply-potato | Reads **GitHub job lists** (SimplifyJobs New-Grad / Internships, vanshb03, crypto-jobs-fyi) | Nothing reads them | A kit feed for new-grad and intern users, and a seed source for row 7 | Feed: `raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json` (+ Summer2027-Internships). apply-potato reads repo READMEs instead | `freehire scripts/harvest_boards.py:43-49`; `apply-potato src/github_parser.py:81`, `:3` | yes, code (feed shape not yet fetched) |
| 9 | career-ops | **Web-search queries with `site:` filters**, written per title. These find employers on no list | `hunt.md:8` says "web search" with no query shapes | Give the assistant ready query templates | `hunt.md` step 3: for each title × place, search `site:boards.greenhouse.io OR site:job-boards.greenhouse.io "<title>" <place>`, then the same for `jobs.lever.co`, `jobs.ashbyhq.com`, `myworkdayjobs.com`, `icims.com`, `apply.workable.com`. Add each new board to `--companies-extra` | career-ops `modes/scan.md:166` "Level 3 — WebSearch Queries"; `templates/portals.example.yml:892` | yes, code |
| 10 | career-ops, ai-job-search | **National public job services** with open APIs: Germany Arbeitsagentur, Sweden Platsbanken/JobTech, Canada Job Bank, Singapore MyCareersFuture, Belgium VDAB, plus regional boards | `hunt.md:35` says "that country's own job sites", with nothing concrete | A kit feed per country, picked from the user's `search_brief` rows | Arbeitsagentur `rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs` (public client key, `X-API-Key` header); JobTech `jobsearch.api.jobtechdev.se/search`; Job Bank RSS; MyCareersFuture `api.mycareersfuture.gov.sg/v2/search` | career-ops `providers/arbeitsagentur.mjs:39-40`, `platsbanken.mjs:71`, `jobbankca.mjs:52`, `mycareersfuture.mjs:141` | code yes; live calls blocked by the container proxy, so UNVERIFIED live |
| 11 | ai-job-search | `/add-portal`: the assistant builds a search skill for a new local job site, and **stops if the site needs a login** | No step for "a country we don't cover" | Same idea as a recipe step | `hunt.md`: "A row's country has no source? Find its main public job site. If listings open without login and robots.txt allows it, search it; if not, tell the user." | `ai-job-search .claude/commands/add-portal.md:1`, `:41` | yes, code |
| 12 | career-ops | Seeds companies from **VC portfolio lists** (YC, a16z) and probes their ATS | We have a YC mention only | A seed source for startup users | Port `seeds/vc-portfolios.mjs`'s idea into the row 7 builder | career-ops `seeds/README.md:3` | yes, code (sources not opened) |
| 13 | jobspipe | Keyless MCP **capped at 3 rows a search**; full API needs a key (1,000 free credits, once) | Nothing | Optional only: a free key gives a check across 30+ sources, LinkedIn and Indeed included | `hunt.md` optional line; the kit could read `JOBSPIPE_KEY` like Adzuna | live call: `"Preview: 3 of 5,904 ... caps every search at 3 rows"` | yes, live |
| 14 | trackly, Tsenta | Hosted MCPs behind a Google sign-in. Tsenta is an auto-apply product | Nothing | Mention only: the user's own choice, needs their account | none (account needed) | trackly `README.md:106`; tsenta.com/mcp (web read) | trackly yes; Tsenta web read |
| 15 | jobspipe, freehire, apply-potato, jobsync, career-ops | **Schedules.** jobspipe Signals about every 5 min; freehire instant or daily alerts; apply-potato every 30 min; jobsync hourly; career-ops cron, every 3 days in its example | Daily recipe only (`daily.md:1`, `preferences.check_every`); nothing runs in the background | (a) The assistant sets its own scheduled task. (b) The user turns on **free alerts that land in Gmail** (freehire saved search, Himalayas, LinkedIn, Indeed), which the daily check already reads | `hunt.md`: "Offer to schedule `run 360 daily` in your own scheduler (Claude Code scheduled task, cron). Use the kit's `--history` so each run shows only new jobs." | `apply-potato src/config.py:542`; `jobsync src/lib/constants.ts:274`; `career-ops docs/AUTOMATION.md:55`; jobspipe Signals (first-pass doc) | yes, code |
| 16 | JobSpy, ai-job-search, ApplyPilot, JobCtrl | **LinkedIn without login**: `GET /jobs-guest/jobs/api/seeMoreJobPostings/search?start=N` (10 a page), then `/jobs/view/{id}` or `/jobs-guest/jobs/api/jobPosting/{id}` | We forbid it (`hunt.md:38`) | **Owner decides** (facts below) | If allowed: a kit source with very low volume (ai-job-search pages slowly, backs off on 429) | JobSpy `linkedin/__init__.py:71`; ai-job-search `linkedin-search/cli/src/helpers.ts:7`, `cli.ts:7` "against LinkedIn's Terms of Service, so keep volume low" | yes, code |
| 17 | JobSpy, ApplyPilot, jobspy-mcp | **Indeed without login**: `POST apis.indeed.com/graphql` while **pretending to be Indeed's iPhone app**, with a hard-coded app key | We forbid it | **Owner decides.** Safer path: the official Indeed MCP (row 4) | — | JobSpy `indeed/__init__.py:40`; key at `indeed/constant.py:97` (not copied); app headers `:100-101` | yes, code |
| 18 | JobSpy | **Glassdoor**: `POST /graph` GraphQL with a hard-coded CSRF token. **Google Jobs**: `google.com/search?udm=8` | We forbid it | **Owner decides.** JobSpy's own README says Google Jobs "is currently unavailable" | — | `glassdoor/__init__.py:127`, `constant.py:7`; `google/__init__.py:110`; `README.md:226` | yes, code |
| 19 | JobSpy | ZipRecruiter (HTML), Naukri (a signed token per request; answers 406 recaptcha when blocked), Bayt (HTML) | — | Do not borrow: bot checks are part of the design | — | `naukri/__init__.py:64`, `:70`; `ziprecruiter/__init__.py:55` | yes, code |
| 20 | freehire | `check_jobs_open` checks up to 50 jobs per call | We have `--check-live` in the kit | Nothing new for kit users. No-Python users get it through row 1 | — | live tool list | yes |

## LinkedIn and Indeed: the facts for the owner's decision

`hunt.md:38` says: "Never scrape or browser-search LinkedIn, Indeed, Glassdoor or other big job sites, signed in or not: their terms forbid automated access."

**How competitors do it** (rows 16–19): plain HTTP calls with no login.
- **LinkedIn:** the public "jobs-guest" pages that logged-out visitors see. 10 jobs a page.
  - JobSpy waits 3–7 s between pages and wipes cookies.
  - JobSpy `README.md:194`: "LinkedIn rate limits bursts of requests with a 429".
  - JobSpy `README.md:231`: "All of the job board sites are aggressive with blocking." JobSpy advertises proxies "to bypass blocking" (`README.md:18`).
- **Indeed:** JobSpy calls Indeed's **mobile-app API**, sending an app key copied from the app and an iPhone app identity. That is not a public page; it is pretending to be Indeed's own app.

**Rules and risk:**
- **LinkedIn**, web read ([Wikipedia](https://en.wikipedia.org/wiki/HiQ_Labs_v._LinkedIn), [Morgan Lewis](https://www.morganlewis.com/blogs/sourcingatmorganlewis/2022/12/linkedin-v-hiq-landmark-data-scraping-suit-provides-guidance-to-data-scrapers-and-web-operators)):
  - hiQ v. LinkedIn: scraping *public* pages was held not to be "hacking" under US law.
  - But in Nov 2022 the court found hiQ **broke LinkedIn's User Agreement**. hiQ settled with a $500,000 judgment and an injunction against further scraping.
  - The risk is contract and account bans, not crime. ai-job-search itself warns "against LinkedIn's Terms of Service".
  - linkedin.com/robots.txt did not load from this container: unread.
- **Indeed robots.txt**, read live today (sha256 prefix `1189922fa6724d51`):
  - It lists `Claude-User`, `ChatGPT-User`, `Claude-SearchBot`, `OAI-SearchBot` and `Perplexity-User` in a group with `Allow: /`.
  - Search pages up to `&start=90` are allowed.
  - `/jobs/<country>/`, `/*?rss`, `/alert` and `/applystart` are disallowed.
  - So an AI assistant **opening an Indeed search or job page for its user** is allowed by robots.txt.
- **Indeed terms**, web read ([conductatlas](https://conductatlas.com/platform/indeed/indeed-terms-of-service/provision/CA-P-028619/prohibition-on-scraping-automated-access-and-bot-activity/)): the terms still bar crawling and scraping. robots.txt is not a licence.
  - The mobile-app GraphQL trick (row 17) is clearly outside both.
  - The official Indeed MCP (row 4) is the sanctioned path.
- **Glassdoor**, web read: its terms bar scraping. JobSpy uses a copied CSRF token.

**Options for the owner** (inference, not a recommendation of law):
- **A. Keep the rule.** Use the official Indeed MCP (row 4), the job-alert emails, and "Apply on company site" links.
- **B. Allow the assistant to *open* an Indeed search page itself.** Low volume, its own user agent, no app spoofing. robots.txt allows `Claude-User` today; the terms do not.
- **C. Allow the LinkedIn guest search at very low volume**, as ai-job-search does. Against LinkedIn's terms; the risk is to the user's IP and access, not their account (no login).
- Never: the Indeed app-API spoof (row 17), the Glassdoor CSRF token (row 18), proxies, or captcha workarounds (row 19).

## Suggested order (inference)

1. Recipe-only changes, no code: rows 1, 3, 4, 9, 11, 15. They work for every AI, Python or not.
2. Kit switches: row 5 (Workday on, with a curated list) and row 2 (page freehire deeper and use its filters).
3. Kit growth: rows 7 and 8 (harvest builder plus GitHub list feeds), then row 6 (iCIMS, Oracle Cloud, SuccessFactors, Taleo, Jobvite, Eightfold adapters) and row 10 (national job services).
4. Owner decision: the LinkedIn and Indeed options above.

Raw clones and live outputs are in the cloud session's scratchpad and die with the container. Before building any row, re-clone at the commit above and re-check the quote (session-lessons).
