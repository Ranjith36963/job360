# Job360 — what we are building
<!-- doc: LIVING | decided 2026-09-03 in an 18-question interview with the owner -->

> **Read this before any feature work.** It is the single source of truth for
> what Job360 is, what it is not, and the order things get built. If code or an
> older doc disagrees with this file, this file wins and the other one is stale.
> Change it only with the owner, and change it *here* first — the reason this
> file exists is that without it every new session re-derives the product and
> we go round in a loop.

## One line

**Job360 is the memory and context layer for whatever AI agent a job seeker
already uses.** The agent (Claude Code, Claude, ChatGPT, Grok, Gemini, a
browser agent) finds the job, reads it, judges fit, writes the CV, finds the
recruiter, reads the inbox, fills the form. Job360 gives that agent the
candidate's structured context, keeps every version of what it made, records
every event that happened, and answers "what did I send, what happened, what
changed, what worked".

Job boards find jobs. Agents think and act. Job360 remembers.

## The house (the owner's analogy, 2026-10-20)

The lifecycle is a house. Each part has one name, and one owner of the work:

| Part | Name | What it is | Who does the work |
|---|---|---|---|
| Soul | **The assistant** | Whichever AI the seeker already uses — Claude, ChatGPT, Gemini — with its connectors. It thinks, finds, writes, reads the inbox, fills the form. | The seeker's AI, never Job360 |
| Heart | **Job360** | The memory and context: profile, every version, every event, every receipt. | Job360 stores and remembers |
| Source 1 | **Jobs** | Where an opening comes from: the assistant finds it, or the seeker brings a link or pasted text. | `bring_job` |
| Source 2 | **People (outreach)** | Where a connection comes from: recruiters, hiring managers, referrals, found by the assistant through its own connectors (Apollo, Vibe Prospecting, Sprouts, company pages and others). Most jobs come through people, so this source matters as much as Jobs. | `add_contact` |
| Process | **Application** | One object per job. Both sources feed it, and a person can start one. Everything after the click lives here. | the spine (`services/applications/`) |
| Wiring | **Gmail, calendar, events** | What joins the parts: replies read from the inbox, interview dates, the event log. | the assistant's connector reads, `record_event` stores |

The assistant is the soul that runs the house; Job360 is the heart it runs with.
The better the context in the heart, the more the soul can do alone. We never
source, rank or recommend (rule 4): a new source is a list of places the
assistant tries and a field that records where it came from, never a Job360 finder.

## The harness (the owner, 2026-10-24)

**Job360 is the job seeker's harness for their own AI assistant.** The assistant
is the engine (it thinks, finds, writes, fills); Job360 controls when it runs,
what it does, within which limits, and keeps the record. Connect Job360 to any
assistant (Claude today; ChatGPT and Gemini next) and the seeker gets the same
full job-seeking help, because every part that matters lives in Job360:

| Harness part | What it is |
|---|---|
| Control panel | The seeker's settings: limits, apply mode, hunt and Gmail timing, score line |
| Instructions | The recipes every assistant follows (`get_recipe`) |
| Safety gates | `check_submit`, `check_outreach`, the practice run: the assistant cannot act past the seeker's rules |
| Memory | Profile, answers, every document version, events, receipts |
| Duty board | `whats_due` / `mark_done`: what is due now, what is done |
| Ignition | Job360 starts the assistant the moment the seeker acts or a step finishes (a vendor start link where one exists; the vendor's own timer everywhere) |
| Engines on a clock | Hunt, people hunt, apply, Gmail check: scheduled runs with the seeker's computer off |
| Dashboards | Two cockpits: **Needs you** for attention (one card per job, one click per action) and **Home** for permissions (pause, apply mode, limits, every yes the assistant waits for). See "Every page has one purpose". |

The harness never becomes the brain: no LLM of its own (decision 28), never
sources, ranks or recommends (rule 4). It steers, limits and records. What
differs per assistant is only the setup (how its timer is made, whether a start
link exists); the parts above stay the same.

## Every page has one purpose (the owner, 2026-10-27)

> "Everything that needs my attention comes to Needs you. Everything that needs my
> permission goes to Home. Connect your assistant is maintenance. Each and every
> page has its own vision, its own persona."

Job360 is the job seeker's cockpit. There are two cockpits, for two different
purposes, plus one workshop. A control lives on exactly one page, chosen by the
question it answers:

| Question the control answers | Kind | Its page |
|---|---|---|
| "What is waiting for **me** right now, on one job, person or email?" | **Attention** | **Needs you** |
| "What may my assistant do, and does it have my yes?" | **Permission** | **Home** |
| "How is it set up and connected?" | **Maintenance** | **Connect your assistant** |
| "What happened, and what is on record?" | **Record** | the record pages below |

### The three main pages

| Page | Persona | It is for | It never holds |
|---|---|---|---|
| **Needs you** | **The attention cockpit.** The page the seeker lives on. | Every item that waits on the user, one card per job: questions to answer, guesses to OK, a CAPTCHA or sign-in on an open form, a CV to approve, a note to send to one person, an outcome to confirm, an email to match. Cards are folded to one dense row (company, role, where it came from, when, score, next step) and unfold with **+**. It updates live while the assistant works. | Standing rules, limits or on/off switches. Setup. |
| **Home** | **The permissions cockpit.** The major controls. | Everything that decides **what the assistant may do**, and every yes it is waiting for: Pause; how it applies (ask first or apply on its own, the score line, the "auto for a while" grant); limits (per day, per week, per company); sending in the user's name (employer emails, outreach, the Gmail mode); the assistant's requests to change a setting; bulk yeses (send these N, run my own-browser tasks now, the batch queue). Plus one linked line: "N things need you". | Per-job work (that is Needs you). Setup and fine-tuning (that is Connect). |
| **Connect your assistant** | **Maintenance.** The workshop. | Connecting an assistant, tokens and connected apps, start links and schedules, job-site sign-ins for Job360's browser, the setup steps, run problems, and the fine-tuning of the rules (gap between applications, apply hours, how often it hunts, people hunt, PC-on hours, standing notes). | Anything that waits on the user today. |

A yes or no about **one** job or **one** person (Send this application, Skip this
job, send this person's note) is attention, not a standing permission: it stays
in that job's card on Needs you and on the application page. Only standing rules
and **bulk** yeses go to Home. A count can appear on another page; the control
lives on its own page.

### The record pages

| Page | Persona: what it is for |
|---|---|
| **Jobs in** | The front door: bring a job (link or pasted text). |
| **Applications** (and one application's page) | The record of each job, one object per job: every version, event, decision and receipt. The list uses the same folded row as Needs you. |
| **Outreach** | The record of people: who, linked to which job, every note and reply. |
| **Receipts** | Proof of what was sent. |
| **Stats** | How the hunt is going. Read only. |
| **Profile** | Who the seeker is: the CV and the facts the assistant writes from. |
| **Memory** | What the assistant knows and has saved about the seeker, and what the seeker has confirmed. |
| **Account** | Sign-in, privacy, export and delete. |

### What sits on the wrong page today (2026-10-27 inventory, to move)

- **Needs you to Home:** the assistant's setting-change requests (`SettingRequestCard`, now in the Other list and its count); "Send these N" (`SendAllCard`); "Run my own-browser tasks now" (`RunNowPanel`). Their counts may stay on Needs you.
- **Connect to Home:** the major standing controls `paused_until`, `submit_mode`, `above_line`, `below_line`, `apply_min_score`, `auto_grant_count` / `auto_grant_until`, `daily_cap`, `weekly_cap`, `company_cap`, `outreach_send`, `auto_employer_emails`, `preferences.daily_check`. On Home today they are read-only with "Change in Connect" links (`MorningCheck`, `BatchRulesCard`).
- **Home to Needs you:** "Started, not confirmed" (`StartedNotConfirmed`), due follow-ups, and queue preflight warnings. Home keeps one linked "N things need you" line.
- **Home to Connect:** the setup card, the welcome connect cards, run problems and start-link problems (`RunProblems`, `StartLinkProblems`).
- **Stays where it is, with a count elsewhere:** Pause stays global in the top bar *and* is the first control on Home. The assistant-saved Memory and Profile marks (Keep / Take back) stay on their record pages.

## How a job gets in (decided 2026-10-16, the owner)

Every way in lands in the same place: `bring_job` births one Application, and
the same apply process runs from there. The closed list of ways is the `origin`
field of `api/routes/bring.BringJobRequest`. Whatever is on it, Job360 never
finds jobs itself (rule 4): each origin is somewhere the assistant or the seeker
looked, never a Job360 finder.

## Why this and not the old Job360

Job360 spent a year sourcing and ranking jobs. Every board already does that
better, and every seeker now has an AI subscription that can search, reason
and write. What nobody keeps is the **state**: which CV version went to which
company, who replied, when the interview was, why it was a no. Chat history
loses it; the seeker forgets it; the next application starts from zero.
That persistent, agent-accessible record is the product.

## What we build / what we never build

| We build (the plate) | We never build (the ingredients) — the agent or the ecosystem does it |
|---|---|
| Structured candidate profile (CV + LinkedIn + GitHub + prefs), agent can edit it | Job search, job feeds, ranking, "recommended for you" |
| One **Application** object per job, born the moment the job is brought | Fit judging, CV / cover-letter / answer writing (we store, the agent writes) |
| **Every version** of every artifact, forever, stamped with who/when/which profile | Gmail OAuth, inbox polling, email classification (the agent's connector reads; we store the event) |
| A **typed event log**, one door (`record_event`), every event says who wrote it | People databases (Apollo, LinkedIn lookups) — the agent finds, we store the contact |
| A rich **receipt** on "I applied" — fields, answers, confirmation, exact CV version | Auto-submit only when the user turns it on (never Indeed/LinkedIn; practice run first; pause switch). *Our own browser agent: allowed since decision 29 — see `BROWSER_AGENT.md`.* |
| `whats_new` — pull, not push | Push notifications, WhatsApp, mobile apps (later, on evidence) |
| OAuth 2.1 so ChatGPT / Grok / any client can connect | Our own LLM, our own general agent |
| Cheap counts (reply rate per CV version / role) + full `export_history` | Charts-heavy analytics; the agent analyses the export |
| Bring a job by **link or pasted text**, both, on the web | Chrome extensions, iOS/Android |
| A web page that is the **record** (home = your applications) | A web page that is the product |

Recruiter side: later, consent-first only, never selling candidate access.
Pricing: free for seekers and recruiters until value is proven. No credits,
no per-application charge, nothing that rewards volume.

## The object model

```
Candidate (one profile per user, versioned; multiple named profiles = later)
 └── Application            born at bring_job; status starts "considering"
      ├── Job snapshot      title, company, location, URL, ad text as it read that day
      ├── Fit verdict       written by the agent: fit, gaps, reasoning (stored, not computed by us)
      ├── Artifacts[]       cv | cover_letter | answers | outreach — every version kept
      │                     each: version_no, text, made_by (agent/model/human), profile_version, created_at
      ├── Contacts[]        name, role, email, linkedin — found by the agent
      ├── Receipt           frozen on "I applied": artifact versions sent, fields filled, answers,
      │                     confirmation text/number, channel, sent_at — append-only
      └── Events[]          the history; the status is replayed from it by
                            services/applications/status.replay_status
```

**Event types are a fixed list plus free-text detail.** The list is
`core.settings.APPLICATION_STATUS_EVENT_TYPES` (these move the status) and
`APPLICATION_NOTE_EVENT_TYPES` (these never do). Who may write each one, and
the payload it must carry, is the `record_event` route in
`api/routes/applications.py`, which dispatches one validator per event family.
Pinned by `tests/test_application_kit.py::test_autofill_events_assistants_may_only_deny`,
`tests/test_application_kit.py::test_duplicate_clear_is_web_only` and
`tests/test_assistant_settings.py::test_per_job_override_an_assistant_may_only_send_confirm`.
Nothing is ever deleted.

Today's `applications` (stage) + `application_receipts` (snapshot) +
`application_stage_history` + `tailored_documents` (unversioned, DELETE+INSERT)
fold into this. Receipts stay append-only (bring-a-job constraint 4).

## The agent surface (MCP + same REST)

The tools are the `@mcp.tool()` functions in `api/mcp_server.py`; each carries its
own docstring, which is what the agent actually reads. The exact set is pinned by
`tests/test_mcp_server.py::test_tools_list_is_exactly_the_expected_tools`. Two that
are decisions, not mechanics: we write no CV or letter ourselves (decision 28 — the
agent writes, we version and render), and nothing sources, ranks or recommends a
job (rule 4).

Auth: OAuth 2.1 (`api/routes/oauth.py` + the discovery documents in
`api/routes/well_known.py`), with personal `j360_…` tokens as the CLI fallback.

## Build order (decided 2026-09-03)

1. **OAuth 2.1 authorization server** — unblocks ChatGPT + Grok.
2. **The spine** — one Application object, event log, versioned artifacts, the
   tools above; home page = your applications; old search UI hidden behind a
   flag (off), batch scorer / judge / enrichment switched off with it.
3. **URL fetch** on the web — paste a link OR the text; paste is the fallback
   when a site blocks us. SSRF guard mandatory.
4. Contacts + outreach artifacts; `stats`; `update_profile`.
5. Delete the hidden search code, the 41 sources and the scorer once step 2
   has been live for a release. *(1–5 shipped by 2026-09-05.)*
6. **Email evidence on events** — the agent's Gmail connector reads, we store
   `source` (message id, from, subject) idempotently, plus a real interview
   datetime. Added 2026-09-07 from the competitor read.
7. Visa / sponsorship signal supplied by the agent, shown on the card —
   country-agnostic: we store the agent's reading of the ad and the
   candidate's own list of countries where they need no sponsorship, and
   compare the two; no country rule of ours anywhere (shipped 2026-09-11,
   pinned by `backend/tests/test_visa_signal.py`).
8. CV diff (original vs tailored), web only, read-only — the receipt names
   the applied version; no Keep button (decision 26, 2026-09-11).
9. "Flag for next time" on the web + lessons back through `get_profile`
   (shipped 2026-09-11, pinned by `backend/tests/test_lessons.py`).
10. Later, on evidence only: WhatsApp ("text your agent" + pushes, needs worker
   + Redis back), multiple named profiles, recruiters.

## The one measure

**The owner uses it daily for his own job hunt through Claude Code / ChatGPT.**
Every application he makes goes through Job360. If he skips it for a week the
product is wrong — fix that before adding anything. Proof is his own
applications and events in the database, not a doc.

## Decision log (the interview, 2026-09-03)

| # | Question | Decision |
|---|---|---|
| 1 | "AI finds the job" vs never-source rule | **Not ours.** Agents find jobs with their own tools and hand them over. We never search or rank. |
| 2 | Old search UI on the dashboard | **Hide now** (flag off), delete later. Home = your applications. |
| 3 | When is an Application born | **At `bring_job`**, status `considering`. Merge the two half-objects. |
| 4 | Who writes the tailored CV | **The agent writes, we store.** Ours stays as a web fallback. |
| 5 | Who judges fit | **The agent.** We give context and store the verdict. Our scorer off. |
| 6 | Artifact versions | **Every version, forever**, stamped who/when/profile version. |
| 7 | Who writes events | **Anyone with a token**, typed events + free text, author recorded. |
| 8 | Gmail | **Agent reads via its own connector**, calls `record_event`. No Gmail code from us now. |
| 9 | Outreach / people | **We store contacts + messages.** Agent finds and writes. No provider integration. |
| 10 | Execution readiness | **One rich `record_application`** receipt that takes what the executing agent saw. |
| 11 | Notifications | **Pull, not push** — `whats_new` + web home. Push/WhatsApp later. |
| 12 | Agent auth | **OAuth 2.1 now** (owner overrode "tokens first"). Tokens stay as fallback. |
| 13 | Improvement loop | **Both** — cheap counts on our side + full export for the agent. |
| 14 | Profile | **Keep our extraction, add `update_profile`.** One profile; multiple = later. |
| 15 | Build order | **OAuth → spine → URL fetch → contacts/stats/profile edits.** |
| 16 | URL fetch | **Both link and text must work on the web.** Paste is the fallback. |
| 17 | Our LLM code | **Tailor stays as web fallback; scorer/judge/enrichment off.** *(The tailor half is superseded by decision 28, 2026-09-21: no LLM of ours at all — the agent writes, we render.)* |
| 18 | Success measure | **Owner uses it daily for his own hunt.** |

Taken from the 2026-09-02 pivot without re-asking: recruiters later and
consent-first; everything free; auto-submit only when the user switches it on (owner decision 2026-10-08, no cap by default); global from day one.

### Additions, 2026-09-07 (the Tsenta competitor read — decisions kept, the read itself deleted 2026-09-12)

| # | Question | Decision |
|---|---|---|
| 19 | Gmail again — the competitor table said "copy" | **Decision 8 stands.** The agent's connector reads; we add email evidence to the event (`source`, idempotent by message id) and a real interview datetime. No Gmail OAuth, no polling. |
| 20 | Visa signal — the table said "have", the column is dead | **Agent supplies it** on `bring_job` / `save_fit`; we store and show it next to the CV's right-to-work fact. We never extract it. |
| 21 | "Review before submit" | **Side-by-side diff + one Keep button**, web only. Agents already hold both texts. |
| 22 | What first | **Email evidence (#513)**, then visa (#514), CV diff (#515), flag-for-next-time (#516). |
| 23 | WhatsApp | **Wait for a paying signal.** If ever: inbound-only Twilio webhook first, no worker. |
| 24 | Claude.ai / ChatGPT / Grok / Cursor connectors | **Proved live in prod by a session in a real browser.** Never assumed from the OAuth code. *Not yet done (measured 2026-09-11): 0 OAuth grants and 0 personal tokens ever minted in prod — no MCP client of any kind has connected; the MCP surface is exercised only by `backend/tests/test_mcp_server.py`. Allowlist set on Railway 2026-09-16 — one `POST /api/oauth/register` on prod with all five callbacks (Claude.ai, ChatGPT, Grok, Cursor hosted, Cursor loopback) answered 201. **Proved for Claude.ai on 2026-09-19:** the owner added `https://job360.uk/api/mcp` as a custom connector (Sign in now + Register automatically), Claude registered itself by DCR at 10:25:49 UTC, the grant landed at 10:26:34, and the tool list was fetched five seconds later. Then, in one Claude.ai chat, all ten lifecycle tools ran on one real job (application 52, AI Engineer at Change Digital): `get_profile`, `bring_job`, `save_fit`, `save_artifact` ×2, `add_contact`, `record_application` with an answer and salary fields, `record_event` interview_requested with a real datetime, `record_event` lesson, `whats_new`, `get_profile` returning the lesson — every row verified in the production database, all stamped `agent:Claude`. Two findings: Claude refused to invent an ad when the prompt still held a placeholder (correct, rule 4); and the candidate's country list was unset, so the visa badge stays silent until `work_authorization_countries` is filled. ChatGPT, Grok and Cursor: allowlisted, not yet proved.* |
| 25 | The eight pre-pivot `wiring/*` draft PRs | **Closed** (#447–#454). They targeted the deleted notification stack. |

### Addition, 2026-09-11 (the owner's question on slice 8)

| # | Question | Decision |
|---|---|---|
| 26 | "Do we really need the Keep button? Do we always go back and forth between the web and the chat?" | **No Keep button.** The version that counts is the one the receipt names (`record_application`'s `cv_artifact_id` / `cover_letter_artifact_id`) — that fact already exists, and a Keep on the web would be a "do" door (rule 5) forcing a chat ↔ web round trip. The web stays the record: it shows original vs any version and marks the applied one. Version numbers stay internal; the human sees "Original", "Applied". Decision 21's Keep is withdrawn. |
| 27 | "This is global, not only the UK. Every country has its own visa rules. Only flag it when you see it in the job description." | **Job360 knows nothing about visas.** No country rule, no keyword scan, no guess when the ad is silent. Two stored facts — the agent's reading of the ad (`sponsors` / `no_sponsorship` / `unknown` + the ad sentence + the job's ISO country) and the candidate's own list of countries where they need no sponsorship — and one comparison: list membership. `unknown` or an empty list shows nothing (rule #29). |

### Addition, 2026-09-21 (the owner, on our own LLM)

| # | Question | Decision |
|---|---|---|
| 28 | "Do we need any model of our own?" | **Job360 has no brain of its own.** The user's agent (Claude, ChatGPT, Perplexity, Grok, Gemini — connected over MCP) reads, judges and writes; Job360 stores, versions, renders and remembers. This **replaces the "tailor stays as a web fallback" half of decision 17** — the tailor's LLM is gone; what stays is the part a browser cannot do for itself: version history, per-line provenance, keep/download, DOCX and PDF rendering of text the agent saved with `save_artifact`. Four slices: **A** removes the tailor LLM (this PR), **B** removes the profile LLM passes, **C** teaches the agents, **D** fixes the copy. Market check 2026-09-20 — custom MCP connectors are available on Claude (all plans, including Free), ChatGPT Plus/Pro, Perplexity Pro/Max, Grok paid and Gemini business: a seeker who can use Job360 at all already has an agent, so a second-rate model of ours is cost, not value. |

### Addition, 2026-10-19 (the owner, on a browser of our own)

| # | Question | Decision |
|---|---|---|
| 29 | "Claude in Chrome needs my PC on, and only Claude can use it. Can we build one browser that works for every assistant, even with my laptop off?" | **Yes: the Job360 browser agent** (`docs/product/BROWSER_AGENT.md`, which says what is built and what is still off). It replaces "browser automation of our own" on the never-build list. A cloud Chrome that Job360 runs, driven by the user's own agent over MCP, so Claude, ChatGPT, Gemini and others all use the same browser, laptop on or off. The agent still thinks; Job360's browser only does what the agent asks, through the same gates (`check_submit`, never-apply list, limits). The owner also said yes to keeping the user's signed-in session (cookie only, encrypted, never a password) and to Job360 submitting from the cloud. Hard lines stay: never type a password, never solve a CAPTCHA, never disguise the browser to get past a bot check. What the cloud browser cannot do goes to Needs you, and the user's own assistant's browser (Claude in Chrome, Gemini in Chrome, ChatGPT agent, Comet) finishes it when their computer is on. |
| 30 | "Everything that needs my attention comes to Needs you. Everything that needs my permission goes to Home. Connect your assistant is maintenance. Each page has its own vision, its own persona." (2026-10-27) | **Two cockpits and a workshop.** Needs you = attention, Home = permissions and the major controls, Connect your assistant = maintenance. A control lives on one page, picked by the question it answers. See "Every page has one purpose" above for the page personas and the list of moves. |

## Older docs this supersedes

The sourcing-era product docs this superseded (`docs/product/PRD.md`, the
`docs/product/pillars/` manuals, every `docs/product/plans/PRICING_*` file)
described a product this codebase no longer builds. They were deleted whole
2026-09-05 (harness+docs cleanup) rather than archived — git history is the
record, not a guide for what to build next. The per-slice plan and spec files
(`docs/plans/`) and the ADR folder (`docs/decisions/`) went the same way
2026-09-12 — the shipped code, the hard rules and this file are what bind; git
log holds the reasoning that produced them.
