<!-- doc: FROZEN | captured: 2026-10-16 with REA 6.5.0 (Reverse Engineer Anything) -->
# Competitor teardown with REA — 2026-10-16

What eight job-search products do, read from their public JavaScript and network calls with the REA MCP tools.
For each one: how they find jobs, how they rank or score a match, how alerts or schedules work, what their MCP or agent tools do, and how feedback or outcomes work.

**Scope.** Public pages only. No logins, and no accounts were used: we have none on these products. Nothing was submitted. No bot challenge was bypassed.
**Citations.** Every claim cites a bundle file with a byte offset (`file@offset`), a network event (`[seq N]`), or captured page text (`a11y [node]`).
Anything not from REA is labelled **web read**. Anything REA did not find is **NOT FOUND**, with the search terms tried.
"(inference)" marks our reading of a quote, not the quote itself.
**Read with care.** Scoring, ranking and alert schedules all run on the vendors' servers. The client only shows the inputs, labels, enums and endpoints, never the formula.

## How the evidence was collected

- **REA server identity.** `binary_session` reports package `rea-agents` 6.5.0 (`/opt/node22/bin/rea`), started 2026-10-16T15:54:44Z.
  - Client: claude-code 2.1.301, MCP protocol 2026-07-28.
  - Catalog: 207 tools, combined digest `405725e7…4a4f4`.
  - Browser provider available. Ghidra, Hopper, IDA and JADX are not configured, and none was needed.
- **Browser.** Headless Chromium 141.0.7390.37, launched with a loopback CDP endpoint through the container proxy.
  - REA's own launch mode failed TLS behind the proxy (`net::ERR_CERT_AUTHORITY_INVALID`), so every scenario used `capture_browser_scenario` in `connect` mode.
- **REA tools used:**
  - `analyze_web_bundle`: chunk graph and endpoint candidates.
  - `inspect_web_page`: script sources and page text (accessibility tree).
  - `export_web_scripts`: wrote the digest-verified script files cited below.
  - `capture_browser_scenario`: network calls with request and response bodies.
  - `export_evidence_bundle` and `discover_webmcp_tools`.
- **WebMCP.** `discover_webmcp_tools` returned `WebMCP.enable was unavailable` on Chromium 141 (ev_265c26d3…, ev_968fe5b7…, ev_053b155b…). Page-registered WebMCP tools are therefore **unknown, not absent**.
- **Reading the captures.** Six parallel readers searched the exported files.
  - Each reader re-checked its own quotes with an exact-string search: 283 quotes in total.
  - The lead then re-checked 45 key quotes independently against the files, and all 45 matched.
- **Artifacts.** About 60 MB of captures sit in the session scratchpad, not in the repo. The evidence ids below re-identify each capture.
- **Discovery (web read).** Which public pages to open was chosen from each site's `sitemap.xml`, fetched with curl.

| Site | Pages captured | Main REA evidence | Status |
|---|---|---|---|
| jobspipe.dev | `/`, `/sandbox`, `/mcp-server`, `/signals` | bundle ev_fe9b4db1…, scripts ev_19fa596e…, network ev_9c95b879… / ev_c8f8f8f9…, text ev_28d6fece… / ev_0914cfe9… | full |
| freehire.me | `/`, `/jobs`, `/agents`, `/features/notifications`, `/features/ghost-jobs` | bundle ev_21368c7d…, scripts ev_684abb20…, network ev_c6d7cdd2…, text ev_96918286… / ev_ea51d290… / ev_d246ed6e… | full |
| Jobright | `/ai-job-match` | scripts ev_3663d4f8…, network ev_53a7e7a8… | `/jobs` blocked: bot "Security check" at `/_jr/security/challenge` (ev_c2d3b161…), not bypassed |
| Simplify | `/jobs` | scripts ev_7de8ceba…, scenario partial observation (seq 557–558) | scenario ended `target_not_allowed`, but its partial observation kept the search call |
| 6figr JobGPT | `/jobgpt` (Angular app on cdn.6figr.com) | bundle ev_a3524b3a…, scripts ev_77832a92…, network ev_d8ff23fd… | logged-out only |
| Careerflow | `app.careerflow.ai/login` | scripts ev_b3163656…, network ev_2678f177… | login shell only; the API bodies were not returned |
| Rezi | `www.rezi.ai/resume-mcp`, `app.rezi.ai/login` | scripts ev_25936299… / ev_208cc6ee…, text ev_8846929b…, network ev_5ae74fe1… | `cli-auth` chunk not loaded, so not captured |
| Teal | `www.tealhq.com` | ev_1caca737… (page = "Attention Required! \| Cloudflare", 1 script, 375 bytes) | **blocked**: Cloudflare challenge. Also HTTP 403 to curl (web read). Not bypassed. |

**Bundle path keys.** Paths below are relative to each site's exported `files/origins/<hash>/` folder:

| Short form | Bundle URL |
|---|---|
| jobspipe chunk | `https://jobspipe.dev/_next/static/chunks/<file>` |
| freehire chunk | `https://freehire.me/_app/immutable/chunks/<file>` |
| Jobright `_app` | `https://static.jobright.ai/_next/static/chunks/pages/_app-4f28f5fa2e9a08b8.js` |
| Simplify chunk | `https://simplify.jobs/_dunder/_next/static/immutable/chunks/<file>` |
| 6figr `main` | `https://cdn.6figr.com/app/main.b88cd25b464da787.js` |
| 6figr `pages` | `https://cdn.6figr.com/app/src_app_pages_pages_module_ts.9d8d301a4c0d3b52.js` |
| Careerflow chunk | `https://app.careerflow.ai/assets/<file>` |
| Rezi app chunk | `https://app.rezi.ai/_astro/<file>` |

---

## Summary across the eight

| | Find jobs | Rank / score | Alerts | MCP / agent | Feedback / outcomes |
|---|---|---|---|---|---|
| **jobspipe.dev** | 30+ ATS/boards normalised and deduplicated; REST `/v1/…/jobs/search` | filter search; demo agent shows per-posting `relevance` 0–1 + reason | "Signals": re-run saved search every ~5 min; email/Slack/HMAC webhook, instant or daily | hosted MCP `mcp.jobspipe.dev/mcp`: `search_jobs`, `list_pricing_plans`, `give_feedback` (+ free `list_job_sources`) | MCP `give_feedback` (data quality); no seeker tracking |
| **freehire.me** | crawls company boards (Greenhouse, SmartRecruiters, …) + user-submitted; 2.2M jobs | skills coverage %, adjacent skills ×0.5, blockers cap the score | saved search → subscription (Telegram default, email, push, Discord, webhook); instant or daily digest; follow-up nudges at 3/21/18/15/12/5 days | `npx -y freehire-mcp`, Go CLI, Claude Code plugin, custom GPT; "an agent never submits an application for you" | vote, dismiss, report, ghost-report; application stages; Gmail sync |
| **Jobright** | `/swan/recommend/*` server feed; import-by-URL; Greenhouse easy-apply | sort RELEVANCE / FRESHNESS / MATCH_SCORE; sub-scores education/industry/seniority/skill | job-alert caps 1/2/5/unlimited per day; digest NONE/DAILY/WEEKLY | Orion copilot (web chat); MCP NOT FOUND | like/unlike, ignore, report, job-match feedback; APPLIED/INTERVIEWING/OFFER_RECEIVED/REJECTED/ARCHIVED |
| **Simplify** | client-side Typesense `multi_search` on collection `jobs` | `_text_match` + `shuffle_key`; up to 3 paid `promoted_rank` jobs pinned on page 1; server "match groups" | email frequency DAILY/WEEKLY; "still interested?" nudge | MCP NOT FOUND; Copilot browser extension | like/dislike/view events; tracker SAVED…GHOSTED/WITHDRAWN; Gmail scan sets status |
| **6figr JobGPT** | `POST /api/v1/jobs/search` and `/jobs/match` per "job hunt" | server `relevancyScore` (0–1) sorts the MATCHED column; AI fit badge at ≥0.75 | none; Auto Mode auto-applies daily (default limit 5, min match score) | MCP via API keys + OAuth at `mcp.6figr.com`; tool list NOT FOUND | kanban MATCHED → AUTO_APPLYING → APPLIED → INTERVIEWING → OFFER/REJECTED; thumbs on generated resumes |
| **Careerflow** | own index ("CTS") `/get-public-jobs`, `/get-jobs` (Qdrant-named), simple-apply matches | resume-vs-JD skill match: "Strong match" / "Partial Match", % | none; per-job reminder emails (interview, follow-up) | MCP NOT FOUND (SDK only in a dependency list); Auto Apply agent | kanban Saved/Applied/Interviewing/Offer/Rejected, editable columns; skill-level feedback |
| **Rezi** | NOT FOUND in code; page says "the agent picks the strongest match" | NOT FOUND (keyword targeting, "Rezi Score" named) | NOT FOUND | Resume MCP for Claude/Cursor/Codex/Gemini; tool names NOT FOUND; CLI login handoff | NOT FOUND |
| **Teal** | blocked | blocked | blocked | blocked | blocked |

---

## jobspipe.dev

A job-data API for developers, sales and recruiting teams, not a product for job seekers.

**1. Finding jobs**
- Sources: chunk `9381-ad7825097fccccd4.js@2491` `30+ sources, from Workday, Greenhouse, Lever, Ashby and SmartRecruiters to LinkedIn and Indeed - every posting normalized into one schema and deduplicated across boards.`
- Dedup: `9381@9640` `When the same role is posted to Greenhouse and Ashby, we collapse them into one record and surface every board under .sources[].`
- The /signals page lists `Watching 10 sources` (a11y_jobspipe-signals [75]): Indeed, Greenhouse, Workday, Lever, Ashby, Workable, SmartRecruiters, Paylocity, LinkedIn and Y Combinator ([77]–[103]).
- Endpoint: REA `analyze_web_bundle` (ev_fe9b4db1…) found `https://api.jobspipe.dev/v1/sandbox/jobs/search` (call:fetch) in `374-f0f4736da1efcca5.js` line 1, column 3491, and the route `jobspipe.search` in `2054-d2202e4500cb93ac.js` column 20822.
- The FAQ says the sandbox needs no key: `9381@11281` `Every /v1/sandbox route answers without an API key and without a quota`.
- Search shape: `9381@12611` `Title and location accept arrays (job_title_or, job_location_or), plus description keywords, country, remote flag, posted_at date ranges and per-source include or exclude.`
- Calls seen on page load (network ev_9c95b879…):
  - `[seq 144] GET api.jobspipe.dev/api/auth/get-session`
  - `[seq 148] GET /api/users/me` returns 401 with an agent-friendly error body: `The API key sent with this request is not valid`, plus `how_to_fix` and `get_key`.
  - `[seq 150] GET /api/status`
  - No search call fired without a user action.

**2. Ranking / scoring**
- The core API is filter search, with no scoring visible in the client.
- The homepage demo is an agent loop: `9381@1778` `"ranking":"Ranking by fit"`.
- The server-rendered `/api/hero-presets` response (scenario ev_c8f8f8f9…) gives each demo result a `relevance` and a `check` reason, e.g. `"relevance":0.83,"check":"Not a hospital"`. It also records how many `searches` and `rounds` the agent ran.
- (inference) An LLM plans several searches, then scores each posting against the stated intent on the server. The method itself is NOT FOUND.

**3. Alerts / schedules**
- "Signals" are saved searches. a11y_jobspipe-signals [123]: `Every few minutes we re-run your search against postings that entered the corpus since the last time we checked.` [148]: `5 min` between checks.
- [123]: `A posting counts as new the first time we ever see it`, so a reposted job does not fire again.
- Delivery, [128]: `Matches arrive as an email, a Slack message, or a signed webhook into your own system. Instant, or batched into one daily digest`.
- The webhook is `An HMAC-signed alert.matched payload` ([267]). Data webhooks fire `job.created`/`job.closed`, signed in `X-JobsPipe-Signature` (`9381@10608`).
- Three Signals are free ([31]).

**4. MCP / agent tools** (a11y_jobspipe-mcp, ev_28d6fece…)
- Hosted server: `claude mcp add --transport http jobspipe https://mcp.jobspipe.dev/mcp` [53]. OAuth sign-in or a Bearer key.
- `search_jobs` [33]: `Search live, normalized job postings by title, skill or tech, country, remote, seniority, employment type and recency. Returns the postings themselves.`
- `list_pricing_plans` [37].
- `give_feedback` [41]: `Tell us what was wrong or missing: a stale posting, a wrong field, a search that missed. Free; a person reads every submission.`
- Free server at `https://jobspipe.dev/mcp` [90]:
  - `list_job_sources` [75]: `List the ATS and job-board sources JobsPipe normalizes, with coverage and freshness notes.`
  - `search_jobs` capped at 3 rows; it returns the REST call for the full set [83].
- Also `npx skills add jobspipe/skills`, OpenAPI and `llms.txt` [110]–[117].

**5. Feedback / outcomes**
- Seeker outcome tracking: NOT FOUND (searched thumbs, dislike, not interested, applied, interview, offer, status). Expected, since this is a data API.
- Only feedback on the data itself exists: the `give_feedback` MCP tool and a support-widget link "Request a feature" (`[seq 387]`).

## freehire.me

An open-source search engine for tech jobs (SvelteKit).

**1. Finding jobs**
- Size: `net-freehire [seq 693] GET /api/v1/jobs/facets?` returns `"total":2200131`. The facets also count `auto_apply_available` 202637 and `ai_archetype` buckets.
- Endpoints in `hwK1lzry.js`:
  - `/api/v1/jobs/search?${i}` @3105
  - `/api/v1/search/interpret` @3288 (inference: a natural-language query is turned into filters)
  - `/api/v1/feed/recent` @2961
  - `/api/v1/sources` @4227
  - `/api/v1/jobs/find?url=` @18617 and `/api/v1/jobs/resolve` @18697
- Source labels, `B_z-JSI_2.js@4969`: `greenhouse:`Greenhouse`` (in `ke={telegram:…,greenhouse:…,smartrecruiters:…,bamboohr:…}`). Also `remoteok`, `workatastartup`, `usajobs`, `weworkremotely` and `hackernews` (@5083–@5300).
- Crawling plus user submissions:
  - `DMpAkYdt.js@943` `Added, and this company is new to us — we'll start crawling its board.`
  - `@1037` `Its careers site isn't one we can crawl yet, so we'll check by hand…`
- Crawler code is NOT FOUND in the client; it runs on the server.

**2. Ranking / scoring**
- Sort, `DAY7CcOb2.js@780`: `d={newest:`posted_at`,views:`view_count`,match:`match`}`. The labels include `match:`Best match`` (@957). The default is relevance with a text query and newest without one (@657).
- Match = skills coverage, computed on the server by `/api/v1/jobs/${e}/match`, `/match-analysis` and `/match-analysis/stream` (hwK1lzry.js@2698).
- Client recompute, `CG3MWyc0.js@2162`: `coverage_percent:Math.round((n+.5*r.length)/e.total*100)`. Adjacent skills count half.
- Blockers cap the score: `CG3MWyc0.js@415` sorts unmet blockers by `score_cap`. Each blocker has `met`, `score_cap`, `severity` and `reason`.
- Logged-out visitors get a blurred fake score of 60–90% (`CG3MWyc0.js@1526` `var u=60,d=90`).

**3. Alerts / schedules**
- A saved search (`POST /api/v1/me/searches`, hwK1lzry.js@13720) gets a subscription: `{saved_search_id:e,channel:t}` (@17861). The default channel is `telegram` (@17781).
- Channel setup: Telegram link, Discord, email, `POST /api/v1/me/webhook`, `PUT /api/v1/me/notification-settings` and `PATCH /api/v1/me/timezone` (@12431–@18146).
- Notification kinds, `CNEZVaVR.js@4133`: `nudge_follow_up`, `nudge_interview_prep`, `nudge_job_closed` and `nudge_auto_apply_submitted`, plus `subscription_digest` (@3157).
- Schedule, from a11y_freehire-notifications [158]–[172]:
  - `Choose Instant and it goes out as soon as the next match run finds a job against your saved search, or a daily digest at a time you set`
  - `A saved job nudges you after 3 days if you haven't applied, a stalled application follows up at 21, 18, 15, 12 and 5 days of silence depending on the stage`
  - Quiet hours, delivered by email, Telegram and push.

**4. MCP / agent tools** (a11y_freehire-agents, ev_96918286…)
- MCP config `"command": "npx", "args": ["-y", "freehire-mcp"]` [112]. Searching needs no key ([49] `Searching needs no key at all.`).
- `CF2cGl5-2.js@8393`: `A small Go CLI and an MCP server over the freehire job API, so an AI agent or a script can search, open and track jobs without a browser. One API key drives both.`
- Five agent skills, a Claude Code plugin [80], and a custom GPT [121]. Rate limits are 600 requests a minute, or 300 for agent search [340].
- [197]: `An agent never submits an application for you`.
- Individual MCP tool names: NOT FOUND (the MCP package source was not captured).

**5. Feedback / outcomes**
- Job feedback in `hwK1lzry.js`:
  - `POST /api/v1/jobs/${id}/vote` @10663 (the vote values are NOT FOUND)
  - dismiss/undismiss @10554
  - `/reports` @21093
  - `/ghost-report` @21203
- Tracking:
  - `PATCH /api/v1/me/applications/${id}` @9839 and `/jobs/${e}/track` @9773
  - `/me/tracking/pipeline|saved|viewed|dismissed`
  - follow-up @23527
  - Gmail sync `/api/v1/me/gmail/sync` @22089
- Ghost jobs (a11y_freehire-ghostjobs):
  - Posting criteria: `Posting behaves as evergreen` and `Not on the company's own careers board`.
  - Applicant criteria: `People applied through freehire and got no answer in the time their stage allows. Only counted when a mailbox is connected`.
  - Verdict scale: `Possibly inactive` / `Likely inactive`.
- The stage enum is NOT FOUND in the captured chunks.

## Jobright

Captured: `/ai-job-match` only. `/jobs` is behind a bot challenge (ev_c2d3b161…, target `https://jobright.ai/_jr/security/challenge?return=%2Fjobs`), which we did not bypass. All API paths are under `/swan/` in `_app`.

**1. Finding jobs**
- The feed is served by the server:
  - `_app@150378` `/swan/recommend/list/jobs`, with params `refresh, sortCondition, position, deeplink, reqJobIds, count, syncRerank`
  - search `@152005`
  - similar `@154353`
  - visitor list and visitor search
  - `net-jobright [seq 309] GET /swan/recommend/landing/jobs` (body unreadable)
- Bring your own job: `/swan/import/job-by-url` (@157707). Import states are `NOT_FOUND / INIT / ENQUEUED / IMPORTED / FAILED` (@556380); (inference) imported URLs are crawled on the server.
- Apply: `/swan/greenhouse/submit` (@210493) and `/swan/recommend/auto-apply/jobs` (@158595). The A/B flag `"greenhouse_easy_apply":"on"` is set at `[seq 338]`.
- Job sources: NOT FOUND (searched greenhouse as a source, lever, ashby, workday, linkedin, indeed, crawl, scrape).

**2. Ranking / scoring**
- `_app@122678`: `e.RECOMMENDED="RELEVANCE",e.MOST_RECENT="FRESHNESS",e.TOP_MATCHED="MATCH_SCORE"`.
- Sub-scores, `_app@299771`: `education_match_score:"Education Match"`, `q_industry_match:"Industry Exp."`, `q_seniority_match:"Experience Level"` and `q_job_skill_match:"Skill"`.
- FAQ chunk `85987-70a34ec345f3f2ed.js@10781`: `Match Score estimates how closely your background fits a job based on factors such as relevant experience, skills, seniority, industry, and years of experience.`
- `syncRerank` is a list parameter; (inference) the server re-ranks. Weights and the model are NOT FOUND.

**3. Alerts / schedules**
- Endpoints: `/swan/filter/job-alert/get|save` (@331140, @331474), `/swan/resume/job-alert`, unsubscribe endpoints, and `/swan/filter/digest/get|save`.
- Caps, `_app@199630`: `1_ALERT_A_DAY`, `2_ALERTS_A_DAY`, `5_ALERTS_A_DAY` and `UNLIMITED`.
- Digest, `_app@200052`: `e[e.NONE=0]="NONE",e[e.DAILY=1]="DAILY",e[e.WEEKLY=2]="WEEKLY"`. The default is `digestJobAlertFrequency:O.DAILY`.

**4. MCP / agent tools**
- "AI Copilot Orion" lives at the route `/orion-copilot`.
- Message types (`_app@122740`): `AGENT_JOB_RECOMMENDATION`, `AGENT_START_RESUME_TAILOR`, `AGENT_START_COVER_LETTER` and `AGENT_UPDATE_FILTER`.
- Agent routes: `swan/agent/job/list`, `swan/agent/resume/tailor` and `swan/orion/get-cover-letter`.
- The auth response has `"enableAgent":false` (`[seq 313]`).
- MCP, public API or CLI: NOT FOUND (searched mcp, model context, modelcontext).

**5. Feedback / outcomes**
- Job actions: `/swan/job/like|unlike` (@152583), `/swan/job/ignore` (@156317, hides the job) and `/swan/job/report`.
- Match-quality feedback: `/swan/feedback/job-match/show|submit` (@157093). Skill corrections go to `/swan/filter/user-skill/exclude|add`.
- Tracking: `/swan/job/apply`, `/unapply`, `/apply-status/save` and `/swan/job/applied/jobs-v3`.
- Status enum, `_app@556567`: `e[e.APPLIED=0]="APPLIED",e[e.INTERVIEWING=1]="INTERVIEWING"`, then `OFFER_RECEIVED`, `REJECTED` and `ARCHIVED`.
- Apply source enum: `DEFAULT / AGENT / EASY_APPLY`.

## Simplify

**1. Finding jobs**
- Search runs in the browser against Typesense. REA scenario partial observation, seq 557–558: `POST https://js1.simplify.jobs/multi_search?use_cache=true&cache_ttl=120`, with header name `x-typesense-api-key`.
- Hosts: `20s20tax4mrce.js@7073` `["js-ha.simplify.jobs","js1.simplify.jobs","js2.simplify.jobs","js3.simplify.jobs"]`. Collection `jobs` (`312l3dauoiapn.js@6791`).
  - The page ships a scoped, search-only key. It is deliberately not reproduced here.
- Account API: `https://api.simplify.jobs` `/v2/…` (`11p6v-m2qd_e3.js@452`).
- Ingestion sources: NOT FOUND (searched greenhouse, lever, ashby, workday, scrape, crawl).

**2. Ranking / scoring**
- Sort map, `20s20tax4mrce.js@35649`: `relevance:"_text_match:desc,shuffle_key:asc,posting_id:desc"`. The other options are `recent`, `updated` and `salary`. `query_by` is `title,company_name,functions,locations`.
- Captured request (seq 558, body sha256 a2ab8553…): `"sort_by":"_text_match:desc,shuffle_key:asc,posting_id:desc","page":1,"per_page":21`.
- Paid placement: on page 1 with the default sort, a second query runs: `@37751` `sort_by:"promoted_rank:desc,_text_match:desc,shuffle_key:asc",group_by:"company_id",group_limit:1,…per_page:3`, filtered by `promoted_rank>0`.
  - `mergePromoted` (@38856) puts up to 3 promoted jobs, one per company, first.
- Personal recommendations come from the server: `0moi59m3zmn9_.js@19013` `/v2/candidate/me/recommendation/?refresh=…&match_group=…`. Groups include `bestMatch`, `semanticMatch` and `takeAnotherLook` (@435).
- A job-match percentage: NOT FOUND.
  - "Great match!" (`3qsbud2t-uc4v.js@339588`) is a resume-keyword score (`/v2/keywords/resume-score`).
  - "Strong match" (`09x36_838nklo.js@171373`) is a referral-contact label.

**3. Alerts / schedules**
- `1celhz-gqz4pr.js@479596` `prefers_email_frequency` with `DAILY:1, WEEKLY:2`, and `prefers_still_interested_in_applying_email_frequency` (@480793).
- Followed job lists (`/v2/candidate/me/following/job-list/…`), a weekly application goal, and streaks.
- Saved-search alerts, push and cron times: NOT FOUND.

**4. MCP / agent tools**
- NOT FOUND (searched mcp, agent, tool, copilot across 102 scripts).
- The agent-like part is the Simplify Copilot browser extension (`02dgfz9iec7bp.js@114258`), whose telemetry source is "Sabre".

**5. Feedback / outcomes**
- Reactions: `POST /v2/candidate/me/recommendation/` with `{job_posting_id,type,source:2,match_group}` (`0moi59m3zmn9_.js@19999`).
- Reaction types (`1celhz-gqz4pr.js@511609`): `LIKED_RECOMMENDATION:9,DISLIKED_RECOMMENDATION:10`, plus VIEWED, CLICKED, SAVED, IMPRESSION and SENT_STILL_INTERESTED.
- Reaction sources: SABRE (extension), DUNDER (web), EMAIL and VANCE.
- Free-text feedback: `/v2/candidate/me/recommendation/feedback/record` (@20382).
- Tracker (`1celhz-gqz4pr.js@198751`): `SAVED:1,APPLIED:2,SCREENING:11,INTERVIEWING:12,OFFER:13,WITHDRAWN:21,GHOSTED:22,REJECTED:23,ACCEPTED:24`. The outcome funnel is `/v2/candidate/me/tracker/sankey`.
- Gmail scan sets status: `/v2/candidate/me/gmail/categorize/stage3`. The email categories are `APPLIED / SCREENING / INTERVIEWING / OFFER / REJECTED` (@456673).

## 6figr JobGPT

**1. Finding jobs**
- `main@41516` `searchJobs(h){…return this.http.post(s+"/jobs/search",h,i)` (with `s="/api/v1"`).
- Live call: `net-6figr [seq 158] POST /api/v1/jobs/search` with body `{"filters":{"locations":[],"titles":["Software Engineer"],"countries":["US"]},"description":false}`. The response body was not readable.
- Logged in: `main@38713` `/jobs/match` for each saved "job hunt" (`/job-hunts`). Filters are titles, locations, skills, remote, expLevels, companySize and workAuthorization (`pages@187301`).
- Import: `pages@196503` `Indeed & Dice URLs are not supported. Please use direct job posting URLs.`
- `GET /job-boards` exists; its response was not captured, so the source list is NOT FOUND.

**2. Ranking / scoring**
- `pages@206956`: `return P.relevancyScore-o.relevancyScore` sorts MATCHED cards by the server's `relevancyScore`.
- The AI fit badge shows `aiRelevancyScore>=.75` as "% Profile Match" (`pages@98743`). Fit is computed by `POST /jobs/{id}/ai-analysis`.
- Re-score after editing the resume: `POST /job-applications/{id}/match-score`.
- The formula is NOT FOUND.

**3. Alerts / schedules**
- Job alerts or digests: NOT FOUND (searched alert, digest, frequency, cron, schedule, notification).
- Recurring behaviour is Auto Mode:
  - `pages@187388` `dailyLimit:5,minMatchScore:null`
  - `Enable Auto Mode to auto-apply to relevant jobs everyday` (`pages@73467`)
  - `Job queued for auto apply. This usually takes a few minutes`

**4. MCP / agent tools**
- `pages@395664` `Connect JobGPT with Claude Code, Claude Desktop, or other MCP-compatible AI tools.`
- API keys: `/me/api-keys` (`generateMcpKey` and `revokeMcpKey`, `main@26929`).
- OAuth callback `mcp.6figr.com/oauth/callback` (`main@182164`).
- Agent chat: `/api/v1/ai-chat/conversations`, with the agent type "jobgpt".
- MCP tool names: NOT FOUND (they live on the server).

**5. Feedback / outcomes**
- Kanban: MATCHED → AUTO_APPLYING → APPLIED → INTERVIEWING → OFFER, plus REJECTED (`pages@256378`). Moving a card calls `PUT /job-applications/{id}`.
- Thumbs up/down apply to generated resumes only: `main@45780` `resume-feedback/upvote`.
- Report a job: `/job-applications/{id}/report` with `{note}`.
- "Not interested" or dismissing a job: NOT FOUND.

## Careerflow

API bodies were not returned on the login page (`[seq 519]`/`[seq 521]` with status None), so these findings come from the JavaScript.

**1. Finding jobs**
- Own job index ("CTS"):
  - `apiPaths-Sy7umbJm.js@417` `ctsBaseUrl:`https://api-prod.careerflow.ai/job-api/interface``
  - `@569` `ctsInstituteId:`public-jobs``
  - `ServiceFactory-DE4gbi99.js@25586` `/get-public-jobs` (query, page, location, lat/long, post-time window)
  - `@26031` `getJobsQdrant` → `/get-jobs` (inference: semantic retrieval from a vector store)
- Recommendations: `/simpleapply/matches` (@4461). The UI says recommendations use your resume's experience and skills.
- Users add jobs themselves: `/job/addInBulk` and the Chrome extension.
- Upstream sources: NOT FOUND.

**2. Ranking / scoring**
- Resume vs JD: `/skillmatch/parseJobDescription`, `/skillmatch/{id}` and `/skillmatch/skillGapAnalysis` (`ServiceFactory@11102`).
- Labels: `Skill Score`, `Strong match` (`InitService-BKprAshH.js@41354`), `Partial Match`, `Missing Keywords` and `"{{percentage}}% match"`.
- Tracker search sorts by `postingDate desc` only (`JobTrackerServices-T5kDwIsQ.js@740`). Weights: NOT FOUND.

**3. Alerts / schedules**
- Job alerts or digests: NOT FOUND.
- Per-job reminder emails exist: `"Email me a reminder before the interview"` (1 hour / 1 day / 2 days / 1 week) and `"We'll email you a reminder to follow up on this application on the date you choose."` (`InitService@50446`, @50658).
- The reminder endpoint is NOT FOUND.

**4. MCP / agent tools**
- MCP: NOT FOUND. The one hit, `index-CpGc5atM.js@136084` `"@modelcontextprotocol/sdk":`^1.29.0``, sits in an embedded dependency list and is not evidence of a server.
- Agents run inside the web app:
  - resume agent `/api/resume-agent/chat` (@29406)
  - Auto Apply `/auto-apply/eligibility|apply|recording` (`ServiceFactory@3756`)

**5. Feedback / outcomes**
- Default columns: `Saved`, `Applied`, `Offer`, `Interviewing` and `Rejected` (`InitService@25804`). Users can add columns (`/board/add-column`) and move cards (`/board/moveJob`). Each job has notes, activities and tasks.
- AI-output feedback: `/feedback/${e}`.
- Skill feedback: `Not relevant to the job` / `No skill` / `Too generic` (@41816).
- Thumbs or "not interested" on jobs: NOT FOUND.

## Rezi

**1. Finding jobs**
- Page text only (a11y_rezi-mcp, ev_8846929b…):
  - [242] "Discover new opportunities" (nav item "Job Search", marked New)
  - [939] `Skip the job board. Ask for product manager roles in Austin and the agent picks the strongest match, then tailors your resume to it.`
- Endpoints and sources: NOT FOUND (the captured app bundle is login and analytics only).

**2. Ranking / scoring**
- Only "the agent picks the strongest match" [939] and "AI Keyword Targeting" [1164]. The "Rezi Score" is named on the page; its formula is NOT FOUND.

**3. Alerts / schedules**
- NOT FOUND (searched alert, notif, digest, schedule, frequency).

**4. MCP / agent tools**
- [554] `Connect Rezi to Claude, Cursor, Codex, or Gemini once. Then just paste a job description and say "tailor my resume to this." The agent does the rest.`
- Use cases: tailor [923], create a resume from a description [955], `"Add my new role at Stripe to my resume."` [975], and review [1003].
- Tool names and the server URL: NOT FOUND.
- CLI login handoff: `AuthLayout.astro_astro_type_script_index_0_lang.C0eubTPP.js@16837` `handleCliAuth`, loaded from `./cli-auth.DZ1Liwod.js` (not captured). Auth runs on Firebase.

**5. Feedback / outcomes**
- NOT FOUND. Only Amplitude analytics: `[seq 74] POST https://t.rezi.ai/2/httpapi`.

## Teal

Blocked. REA `analyze_web_bundle` on `https://www.tealhq.com/` (ev_1caca737…) saw only the Cloudflare "Attention Required!" page: one 375-byte inline script, no endpoints. curl got HTTP 403 (web read). We did not try to get past the challenge. All five questions: **NOT FOUND (blocked)**.

---

## What this means for Job360 (inference, for the owner to judge)

Product rules 4–6: we store; the seeker's agent finds, judges and applies. Read the patterns below with that in mind. Several are "do" features we must not build.

- **Outcome enums are richer than "applied / rejected".**
  - Simplify has `WITHDRAWN` and `GHOSTED` next to `REJECTED`.
  - Jobright has `ARCHIVED`.
  - freehire defines ghosting from stage-aware silence windows and "only counted when a mailbox is connected".
  - These are store-side event types, and fit our typed `application_events`.
- **Email-derived status** (Simplify Gmail `categorize/stage3`, freehire `gmail/sync` + `mail-recall/link`) matches our proof model: a confirmation email is proof, not a new status.
- **Feedback on bad data as a tool.** jobspipe's `give_feedback` MCP tool says "a person reads every submission". It is close to our `report_missing_tool`.
- **Agent-friendly errors.** jobspipe's 401 body carries `how_to_fix` / `get_key`.
- **Do not build.** Everything else is do-side and outside our mission: match scores, promoted ranking, auto-apply daily limits, alert caps, crawling. freehire's public line `An agent never submits an application for you` lines up with our hard line 1.
