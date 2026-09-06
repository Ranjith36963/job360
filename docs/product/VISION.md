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
| A rich **receipt** on "I applied" — fields, answers, confirmation, exact CV version | Form filling, browser automation, auto-submit at volume |
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
      └── Events[]          the history; the current status is just the last status event
```

**Event types (fixed list, plus free-text detail):**
`brought`, `fit_judged`, `artifact_saved`, `contact_added`, `outreach_sent`,
`applied`, `replied`, `interview_requested`, `interview_scheduled`,
`interview_done`, `offer`, `rejected`, `withdrawn`, `ghosted`, `note`,
`lesson` ("flag for next time"). Every event: `type`, `detail`, `occurred_at`,
`recorded_by` (which token/agent/web), `recorded_at`. Nothing is deleted.

Today's `applications` (stage) + `application_receipts` (snapshot) +
`application_stage_history` + `tailored_documents` (unversioned, DELETE+INSERT)
fold into this. Receipts stay append-only (bring-a-job constraint 4).

## The agent surface (MCP + same REST)

| Tool | Does |
|---|---|
| `get_profile` / `update_profile` | structured candidate context; agent may fix or add fields |
| `bring_job` | link or text → Application (status `considering`) |
| `get_application` / `list_applications` | full object with events and artifact versions |
| `save_artifact` | new version of cv / cover_letter / answers / outreach |
| `save_fit` | agent's verdict + gaps on this application |
| `add_contact` | recruiter / hiring manager on this application |
| `record_event` | typed event, free-text detail |
| `record_application` | the receipt — what was actually sent |
| `whats_new` | everything since a timestamp (replaces push for now) |
| `export_history` | applications + events + versions + outcomes as clean JSON |
| `stats` | cheap counts: reply / interview rate per CV version, per role |
| `tailor_documents` | **web fallback only** — our own tailoring for users with no agent |

Auth: personal tokens (`j360_…`) today; **OAuth 2.1 with short-lived tokens
is the next slice** so ChatGPT and Grok connectors can be added. Tokens stay
as a fallback for CLI clients.

## Build order (decided 2026-09-03)

1. **OAuth 2.1 authorization server** — unblocks ChatGPT + Grok.
2. **The spine** — one Application object, event log, versioned artifacts, the
   tools above; home page = your applications; old search UI hidden behind a
   flag (off), batch scorer / judge / enrichment switched off with it.
3. **URL fetch** on the web — paste a link OR the text; paste is the fallback
   when a site blocks us. SSRF guard mandatory.
4. Contacts + outreach artifacts; `stats`; `update_profile`.
5. Delete the hidden search code, the 41 sources and the scorer once step 2
   has been live for a release.
6. Later, on evidence only: WhatsApp ("text your agent" + pushes, needs worker
   + Redis back), multiple named profiles, our own Gmail watcher, recruiters.

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
| 17 | Our LLM code | **Tailor stays as web fallback; scorer/judge/enrichment off.** |
| 18 | Success measure | **Owner uses it daily for his own hunt.** |

Taken from the 2026-09-02 pivot without re-asking: recruiters later and
consent-first; everything free; no auto-submit at volume; global from day one.

## Older docs this supersedes

The sourcing-era product docs this superseded (`docs/product/PRD.md`, the
`docs/product/pillars/` manuals, every `docs/product/plans/PRICING_*` file)
described a product this codebase no longer builds. They were deleted whole
2026-09-05 (harness+docs cleanup) rather than archived — git history is the
record, not a guide for what to build next.
[`docs/plans/2026-09-02-bring-a-job/spec.md`](../plans/2026-09-02-bring-a-job/spec.md)
is slice one of this vision and still holds.
