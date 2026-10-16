<!-- doc: FROZEN | written 2026-10-16 by the local "apply" session; web reads by 5 Sonnet agents + 1 code-read agent -->
# "The AI assistant finds the job": Job360 vs 46 references (2026-10-16)

Owner's question: of our competitor and open-source references, which let an **AI assistant find jobs**?
How do they search, and how do we search? Products that don't find jobs are listed, then left out.

**Evidence.** Every claim comes from a page fetched on 2026-10-16. The fetch tool summarises pages with a small model, so wording is paraphrased, not quoted.
REA (reverse-engineering) corrections come from `docs/research/rea-competitors-2026-10-16.md` on branch `research/rea-competitors`.
Read the "thin" rows with care.

## 1. What this session did (in order)

1. Agreed the owner's model of how a job gets in, saved it to memory, and added it to VISION.md.
   It has two ways: (1) the assistant finds it; (2) the user brings it, as (a) a link or (b) the pasted full description.
   - Memory: `memory/project_job_entry_paths_2026-10-16.md`
   - Doc: `docs/product/VISION.md`, section "How a job gets in", branch `docs/vision-job-entry-paths`, **PR #869** (open, not merged)
2. Built the reference list from `memory/reference_competitors_2026-10-13.md` and `.claude/skills/session-lessons/SKILL.md:99-112`. That gave **46** names (the owner said 45).
3. Launched 6 Sonnet agents in parallel:
   - 1 read our hunt code.
   - 5 read the competitors live, about 9 each.
4. Merged the results into the tables below. Saved them to memory: `memory/reference_competitors_2026-10-16_search.md`.
5. REA was not installed locally. Sent the 9 thin products to the cloud session **"Cloud 360"**.
   It replied with `docs/research/rea-competitors-2026-10-16.md` on branch `research/rea-competitors`, which I read and folded in (section 6).
6. Owner said to route reverse-engineering to the cloud session **"Reverse engineering anything"**.
   Sent it the last 4 unknowns plus a "how do the big indexes collect jobs" question (message copy in section 7). Results are pending.
   - Memory: `memory/reference_rea_session.md`

## 2. How Job360 searches (code read, file:line)

All paths are under `backend/src/`.

- **Who searches:** the user's own AI. Job360 never searches (`recipes/hunt.md:7`). The server only serves static kit files and fetches nothing (`api/routes/hunt_kit.py:5-13, 33-43`).
- **Sources:**
  - `hunt_kit/companies.json` has **3,677** boards: Ashby 1,875, Lever 1,166, Greenhouse 631, Workable 5.
  - The code also reads SmartRecruiters, Recruitee, Personio, Teamtailor, BambooHR and Workday, but the starter list has no boards for those (`hunt_kit/README.md:65-67`).
  - Optional `--universe` adds about 24k boards (`hunt_kit.py:90-91, 1536`).
  - 10 open feeds: freehire, Himalayas, Remotive, Arbeitnow, RemoteOK, Jobicy, Working Nomads, WWR, The Muse, HN (`README.md:69-71`).
  - Adzuna and Jooble need the user's own free keys.
- **Never:** we never scrape LinkedIn, Indeed or Glassdoor, and never log in (`hunt.md:38-41`). Their jobs come in through Gmail alerts or a pasted link.
- **Where:** comes only from `search_brief.rows` (`hunt.md:17-19`), which are built from the right-to-work answers (`services/profile/search_brief.py:1-14, 51-62`). Missing answers become `gaps` that the assistant asks about once.
- **Titles:** the user's titles plus real-world variants (`hunt.md:19-21`).
- **Judge and save:** the assistant reads the full ad and checks scope, visa and years. Only fits are saved, with `bring_job` and then `save_fit` (`hunt.md:44-61`). Duplicates are a hard stop (`hunt.md:12, 52-53`).
- **Schedule:** the daily recipe, step 7 (`recipes/daily.md:51-53`), at `preferences.check_every`.
- **Clients:** Claude Code or Codex run the Python kit. Without Python (Claude.ai), the assistant calls the board APIs by hand (`hunt.md:30-37`).
- **Weak spots:**
  - 15-minute run budget (`--resume`).
  - No ranking.
  - English-only phrase filters.
  - Dead board tokens in the starter list.
  - Nothing enforces the rules; they are instructions only.

## 3. The user's own AI finds jobs (same model as Job360): 14

| # | Who | Where it searches | How it picks | Auto-runs? | Source |
|---|---|---|---|---|---|
| 1 | career-ops (OSS) | 100+ companies, 55+ ATS modules (Greenhouse, Lever, Ashby, Workday, iCIMS, Radancy), web search, RSS | keyword filter (`scan.mjs`, no AI tokens), then the agent scores 1-5 | no (external cron) | github.com/career-ops-hq/career-ops |
| 2 | MadsLorentzen/ai-job-search (OSS) | Danish boards, public LinkedIn pages, freehire API, `/add-portal` | deal-breakers, then the LLM scores 5 dimensions | no | github.com/MadsLorentzen/ai-job-search |
| 3 | neonwatty/job-apply-plugin (OSS) | LinkedIn, Hacker News, X | saved prefs; hiring manager or 1st-degree contact first | no | github.com/neonwatty/job-apply-plugin |
| 4 | borgius/jobspy-mcp-server (OSS) | scrapes Indeed, LinkedIn, ZipRecruiter, Glassdoor, Google, Bayt, Naukri | filters only | no | github.com/borgius/jobspy-mcp-server |
| 5 | trackly-cli (OSS) | own DB of 250K jobs, 5.4K companies, 40+ ATS | filters plus `trackly ask` (20 a day) | no | github.com/kevinastuhuaman/trackly-cli |
| 6 | freehire.me | own index, **2.2M** jobs per REA (site says 4.8M); crawls company boards | facets; skills coverage % | alerts, daily digest | freehire.me |
| 7 | jobspipe.dev | 10 watched sources incl. LinkedIn and Indeed, deduplicated (site says 11.3M) | filters only | "Signals" re-run about every 5 min, webhooks; paid | jobspipe.dev |
| 8 | Himalayas MCP | own remote-job board (41 tools, 13 public) | filters, sort | alerts | himalayas.app/mcp |
| 9 | Indeed MCP | Indeed only; Claude only; beta | not stated | no | docs.indeed.com/mcp |
| 10 | Rezi MCP | "Job Search" tool; source NOT FOUND (REA) | "agent picks the strongest match" | no | rezi.ai/mcp |
| 11 | 6figr JobGPT MCP | `/jobs/search`, `/jobs/match` per saved "job hunt"; source list NOT FOUND | server `relevancyScore` 0-1 | Auto Mode applies 5 a day | mcp.6figr.com |
| 12 | LoopCV with Claude | 50K+ listings (Workday, Greenhouse, Lever, Ashby) | its own matching, then auto-applies | not stated | loopcv.pro/apply-with-claude |
| 13 | FoundRole | employer ATS imports, hourly | ghost-job, pay and visa checks, skill-gap match | digests, alerts | foundrole.com |
| 14 | Tsenta | 50K careers pages, 30+ ATS types | match % plus a reason | continuous, instant alerts | tsenta.com |

## 4. Their own built-in AI finds jobs (not our model): 18

| # | Who | Where it searches | How it picks | Auto-runs? | Source |
|---|---|---|---|---|---|
| 1 | ApplyPilot (OSS) | scrapes Indeed, LinkedIn, Glassdoor, ZipRecruiter, Google Jobs; 48 Workday sites; 30 career sites | LLM scores 1-10, `--min-score` | `--continuous` | github.com/Pickle-Pixel/ApplyPilot |
| 2 | jobsync (OSS) | Greenhouse, Lever (1,160+), Ashby (1,860+) | local score, then AI | scheduled "Automations" | github.com/Gsync/jobsync |
| 3 | JobCtrl (OSS) | Indeed, LinkedIn, ZipRecruiter, company sources | LLM scores 1-10 against evidence | Temporal; $25 a day LLM budget | github.com/ebarti/JobCtrl |
| 4 | apply-potato (OSS) | GitHub job-list repos | hard filters, then a 0-100 score | every 30 min | github.com/coolbrother/apply-potato |
| 5 | get_jobs (OSS) | Boss Zhipin, 51job, Liepin, Zhilian (logged in) | rules; AI only on Boss | auto-applies | github.com/loks666/get_jobs |
| 6 | Auto_job_applier_linkedIn (OSS) | LinkedIn, using your own account | config filters, no score | manual | github.com/GodsScion/Auto_job_applier_linkedIn |
| 7 | JobSpy (OSS) | LinkedIn, Indeed, Glassdoor, ZipRecruiter, Bayt, Naukri and others; no AI | query params only | none | github.com/speedyapply/JobSpy |
| 8 | Jobright | server feed "8M+ jobs"; import by URL | RELEVANCE / FRESHNESS / MATCH_SCORE | alerts 1/2/5/unlimited a day | jobright.ai |
| 9 | Simplify | own Typesense index; **up to 3 paid jobs pinned on page 1** (REA) | text match plus a shuffle | daily or weekly email | simplify.jobs |
| 10 | JobCopilot | 500K company career pages | filters, learns from edits | daily, 50 applications a day | jobcopilot.com |
| 11 | Massive | "hand-vetted companies" | own matching | daily auto-apply | usemassive.com |
| 12 | AIApply | "20M+ jobs" | match score | daily auto-apply | aiapply.co |
| 13 | Wobo | 50+ sources, 3M jobs | match score, swipe | "while you sleep" | wobo.ai |
| 14 | Jack & Jill | "millions of jobs a day" | matches, then introduces you | overnight | jackandjill.ai |
| 15 | Kickresume | job board plus "AI Job Matching" (thin) | not stated | unknown | kickresume.com |
| 16 | Sonara | unclear: relaunched by BOLD, or discontinued? | own matching | unknown | resumly.ai/answers/what-happened-to-sonara-ai |
| 17 | LazyApply | browser filter search on boards (partial, third-party) | no ranking | no | lazyapply.com |
| 18 | Teal | unconfirmed (403; REA blocked) | resume vs job-description score | unknown | tealhq.com |

## 5. Do not find jobs (left out): 14

| Who | What it does instead |
|---|---|
| reactive-resume | resume builder plus a tracker |
| Resume-Matcher | tailors a resume to a pasted job description |
| Awesome-CV | LaTeX template |
| open-resume | resume builder plus a parser |
| AIHawk | the repo is now `invisible_dots`, not a job tool |
| jobctl-claude | tracker; "your agent is the AI layer" (closest to the Job360 model) |
| jobwyre | tracker MCP, no job source |
| REA | reverse-engineering tool |
| Huntr | tracker plus a job clipper |
| Careerflow | tracker; has its own job index, but no MCP found (REA) |
| Final Round AI | interview prep |
| Scale.jobs | humans apply to jobs you pick |
| Applendium | Gmail tracker plus an apply-or-skip check |
| JobShinobi | LaTeX resume plus a tracker |

## 6. Job360 vs the field

| We're ahead | We're behind |
|---|---|
| We never scrape LinkedIn or Indeed (others risk bans and break those sites' terms) | **No hosted index.** Claude.ai users without Python get a weak hunt. freehire, jobspipe and trackly let any AI search one big index |
| Visa and place rows decide "where" | Fewer ATS types than career-ops (no Workday or iCIMS boards) |
| Every fit verdict and job is kept as history | No instant new-job alerts (Tsenta, jobspipe) |
| Free, and works with any AI | No ghost-job or visa-data checks (FoundRole) |

- **Closest to us:**
  - career-ops and ai-job-search use the same own-AI model, but keep local files only and no history.
  - freehire's public line, "an agent never submits an application for you", matches our hard line 1.
- **The REA doc's own verdict** (rules 4-6) says do not build match scores, promoted ranking, auto-apply limits, alert caps or crawling. Whether a hosted index fits rule 4 is the owner's call.

## 7. Messages sent from this session (copies)

**To "Cloud 360" (cloud).** It replied with branch `research/rea-competitors`. The message asked it to:

> Reverse-engineer how 9 competitors find jobs, where the web read was thin: Teal, Sonara, Rezi MCP, 6figr JobGPT, Kickresume, LazyApply, Jobright, Simplify, Careerflow.
> For each: does it find jobs, whose AI does it, real sources, ranking method, schedule, MCP. Cite the endpoint, file or JS bundle.
> Also: how freehire, jobspipe and trackly collect jobs, and which ATS lists we could add to `companies.json`.
> This is read-only research. Push the result to a branch, with no PR and no merge.

**To "Reverse engineering anything" (cloud).** Results pending, on branch `research/rea-competitors`. The message asked:

> **Part A, still unknown:** Teal (retry another public page), Sonara (alive in 2026?), Kickresume, LazyApply. For each: finds jobs? Whose AI? Sources and size, ranking, alerts, MCP.
> **Part B, our gap:** how freehire.me and jobspipe.dev collect jobs (ATS lists or token datasets, crawl frequency), and what trackly-cli's 250K-job DB is built from.
> Rules: public pages only, no logins, no bot bypass, cite `file@offset`, mark NOT FOUND. Append to the same doc and push, with no PR and no merge.

## 8. Paths

| What | Where |
|---|---|
| This report | `docs/research/ai-finds-jobs-46-refs-2026-10-16.md` (branch `research/ai-finds-jobs-46-refs`) |
| REA teardown (8 products) | `docs/research/rea-competitors-2026-10-16.md` (branch `research/rea-competitors`) |
| VISION entry paths | `docs/product/VISION.md`, PR #869 |
| Our hunt | `backend/src/recipes/hunt.md`, `backend/src/hunt_kit/`, `backend/src/api/routes/hunt_kit.py`, `backend/src/services/profile/search_brief.py` |
| Memory | `memory/project_job_entry_paths_2026-10-16.md`, `memory/reference_competitors_2026-10-16_search.md`, `memory/reference_rea_session.md`, `memory/reference_competitors_2026-10-13.md` |

Raw REA captures die with their cloud container. A session that borrows a technique must re-capture the site first (`session-lessons`).
