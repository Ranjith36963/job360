# The exponential product — web research + first-principles synthesis

**Date:** 2026-08-27
**Method:** four parallel web-research lenses (incumbents · auto-apply market + backlash ·
job-seeker pain · agent-native infrastructure), synthesised against `INTENTION.md` and
`OPERATING_MODES.md` on branch `worktree-feat+mcp-server-design`.
**Coverage bound:** Reddit was unreachable (403 on every fetch path; `agent-reach` CLI not
installed). Seeker voices come from Blind, Substack and press. Numbers are from surveys and
platform reports; MCP adoption figures vary 2–5× across sources — treat as magnitude.

---

## 1. What the web says (verified, 2026)

### Searching, writing, applying are commoditised — and already inside the assistants
- Indeed app in ChatGPT — shipped 10 Feb 2026. ZipRecruiter app in ChatGPT — 19 Mar 2026.
  Indeed is an official Claude connector. **All three search only**; the user is bounced to
  the platform to apply.
  https://www.indeed.com/news/releases/indeed-launches-app-in-chatgpt ·
  https://www.nasdaq.com/press-release/ziprecruiter-launches-chatgpt-app-ai-powered-job-discovery-2026-03-19 ·
  https://claude.com/connectors/indeed
- LinkedIn's only agent, Hiring Assistant, is for **recruiters** (GA Sept 2025).
  https://news.linkedin.com/2025/hiring-assistant-globally-available
- Greenhouse, Workday, Ashby shipped "agentic ATS" in 2026; identity-verification vendors
  now plug in before interview stage. Applications reportedly +412% since 2023 vs flat
  openings. https://www.herohunt.ai/blog/surviving-the-ai-application-flood-2026-playbook/
- OpenAI's announced Jobs Platform (Sept 2025, "mid-2026") **has not shipped**.
  https://campustechnology.com/articles/2025/09/10/openai-to-launch-ai-powered-jobs-platform-by-mid-2026.aspx

**Read:** the employer side is arming with agents. The candidate side has nothing.

### Auto-apply is a dead end
- Tailored vs generic: **5.75% vs 2.68%** interview conversion on 1.39M tracked applications
  (Huntr, Q2 2025). Kickresume field study (204 real apps, Apr–Jun 2026): tailored 18% vs
  generic 10% positive response.
  https://tailorforge.com/blog/state-of-resume-tailoring-2026/ ·
  https://studyfinds.com/this-resume-format-most-interviews/
- 819 auto-applied jobs → 5 interviews. https://jobsearchwithai.substack.com/p/i-let-ai-apply-to-819-jobs-for-me
- Indeed **paused** auto-submit on its "Apply For Me" test, 4 Aug 2026.
  https://www.indeed.com/news/releases/indeed-tests-apply-for-me-job-search
- OpenAI **removed** ChatGPT Agent mode, Aug 2026. https://www.usecarly.com/blog/chatgpt-agent-mode/
- LinkedIn cease-and-desist killed HeyReach for 30,000 users overnight, Mar 2026; LinkedIn
  detects "human-impossible application velocity".
  https://northlight.ai/blog/what-happens-when-linkedin-bans-your-tool
- LazyApply (largest pure auto-applier): Trustpilot 2.1–2.4/5, 56% one-star.
  https://blog.loopcv.pro/lazyapply-review/
- AIHawk open-source applier: 30.3k GitHub stars — demand is real, outcomes are not.
  https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk

### The pain is AFTER the click, and nobody owns it
- 62.6 applications → 4.76 interviews per seeker (2026, n=1,000 US).
  https://blog.hiringthing.com/2025-job-application-statistics-updated-data-you-need-to-know
- UK: **75% of applications get zero response** (2025 Ghosting Index).
  https://blog.theinterviewguys.com/the-2025-ghosting-index/
- 63% ghosted after an interview (Greenhouse); Gen Z 78%.
  https://www.unleash.ai/artificial-intelligence/news/greenhouse-61-of-job-seekers-have-been-ghosted-during-the-recruitment-process
- 36% applied to a job that was never filled; 18–22% of Greenhouse listings are ghost jobs
  per quarter. https://www.greenhouse.com/blog/greenhouse-2025-workforce-hiring-report
- 86% of ghosted candidates feel "down or depressed"; 17% "severely".
- **8%** of seekers call AI hiring fair vs **70%** of hiring managers who trust it.
  https://www.greenhouse.com/newsroom/an-ai-trust-crisis-70-of-hiring-managers-trust-ai-to-make-faster-and-better-hiring-decisions-only-8-of-job-seekers-call-it-fair
- After the application is sent, none of LinkedIn / Indeed / ZipRecruiter / Google /
  ChatGPT / Perplexity record: which CV was sent, outreach, interview prep, rejection
  reason, or cross-board history. Checked item by item.

### Distribution is open now
- Claude connectors: ~414 (May 2026), open submission. ChatGPT Apps SDK + App Directory:
  open, App-Store-style review (early 2026). Gemini "Connected Apps". Grok Connectors
  (May 2026) + Bring-Your-Own-MCP. Copilot Studio MCP GA.
- MCP: ~97M monthly SDK downloads, 10k+ servers (magnitude). Spec 2026-07-28: Tasks
  extension, MCP Apps, OAuth/OIDC; SEP-2577 deprecates sampling.
  https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/
- Google A2A: 150+ orgs in production, v1.2 signed agent cards, now under Linux Foundation.
  https://www.axios.com/2026/08/17/a2a-agentic-ai-foundation-open-ai-standards
- Agent payments live: Stripe/OpenAI ACP, Mastercard Agent Pay, Visa Intelligent Commerce.
- Generic memory layers exist (Mem0 $24.5M, Zep, Letta, Supermemory). **No domain-specific
  job-search memory layer exists.** Closest: Clera, $3M pre-seed, Apr 2026.
  https://clera.substack.com/p/announcing-our-3m-pre-seed-building

### Job-board dominance per country (for Premium supply, later)
UK Indeed 54–77% of seeker traffic, Reed, Totaljobs, CV-Library · US Indeed ~50%, LinkedIn,
ZipRecruiter · Germany Indeed 34%, LinkedIn 24%, StepStone, XING (20M DACH) · India Naukri
(70M+ applications/yr), LinkedIn, Instahyre · Australia Seek · Canada Indeed, Job Bank.

---

## 2. First-principles synthesis

Every assistant can now *do* the job search. None can *remember* it. Memory does not
appear retroactively (INTENTION.md §3.2). Whoever holds the record of what a person
actually sent, to whom, and what came back, owns the improvement loop — and the loop is
worth ~2× interviews (the one number backed by real applicant data).

Job boards can **never** build this: their customer is the employer. They will not publish
"this company ghosts 80% of applicants" or "this posting is fake". Job360's customer is
the candidate. That is a structural moat, not a feature.

**The exponential product: the candidate's side of the agentic hiring war — a ledger every
agent writes to, a loop that learns from outcomes, and a truth layer no board is allowed to
build.**

---

## 3. What to build, in order

### Tier 0 — the atomic unit: the application receipt
- One immutable row per **attempt**: exact CV bytes, exact cover letter, profile version,
  model/agent that wrote it, timestamp, channel. Today `tailored_documents` has
  `UNIQUE(user_id, job_id, doc_kind)` — regenerate and the sent version is **overwritten**.
  This is the #1 defect against the vision. Fix first; everything stacks on it.
- `what_did_i_send(company)` answers in one call from any client.

### Tier 1 — close the loop (where the 2× lives)
- Gmail as the eyes: auto-capture reply / interview invite / rejection / **silence** (no
  reply in N days is an event).
- Outcome + reason on every application; rejection feedback captured even when "none given".
- Outreach log (who, what, when) — biggest hole, no table exists.
- Interview record per round: what was prepared, what was asked.
- "What to change next time" — the only thing that reads history back. Nothing does today.

### Tier 2 — agent-native surface (reframe the MCP work)
- Search tools are table stakes — Indeed's connector already does that. **Memory tools are
  the product**: `record_application`, `what_did_i_send`, `log_outreach`, `log_interview`,
  `whats_stale`, `next_action`, `why_rejected`.
- List in all four directories. Sit next to Indeed: they find it, we remember it.
- A2A agent card for the candidate — for 2027–28 when employer agents ask; consent-gated
  (INTENTION.md §11).

### Tier 3 — the cross-user truth layer (the actual exponential)
- Ghost-job score backed by outcomes: "applied by N Job360 users, 0 replies in 60 days".
  Listing-level ghost detection already exists; make it outcome-backed.
- Company response-rate and ghosting score. Boards structurally cannot publish this.
- "What works" patterns across users, opt-in, anonymised. Every user improves the next
  user's tailoring — a network effect a job board cannot have.

### Tier 4 — global (cheaper than the country-parameter plan implies)
- **The record is already country-agnostic.** A Naukri / Seek / StepStone job pasted or
  fetched by the user's assistant gets the receipt, loop and truth layer on day 1.
  **Light tier is global immediately with zero country work.**
- The country parameter (docs/plans/2026-08-27-country-is-a-parameter.md) gates
  **Premium supply only**. Phase 1 still ships (rule #30 violation), but it is one tier's
  supply chain, not "the pivot".

## 4. What NOT to build
- Auto-apply — killed by the data and by the platforms.
- More job sources — aggregation is paid-for table stakes (INTENTION.md §3.1).
- A chat UI — the user already has one; we are the tool it calls.
- Interview-answer copilots — trust product; do not sell cheating.

## 5. The honest challenge
- ~0 users. "Exponential" needs users to compound. Box 2 in the directories is the fastest
  path: free intelligence, free distribution.
- The first number to watch is **applications recorded per week**, not jobs in catalog.
- Reddit voices unverified this session (see coverage bound).

---

## 6. The plate — gaps neither side can close (added same day)

Owner's analogy: rice = every job board, curry = every LLM, **Job360 = the plate** the
seeker eats from. Neither ingredient can be the plate. Each line: gap -> why they
structurally cannot close it -> why the plate can. Numbers from §1; incentives are
reasoning, not survey.

### 6.1 Job boards, providers and AI-apply tools
1. Paid by the employer, not the seeker -> every design serves the poster -> causes 2–6.
2. Will never flag a ghost job (18–22% of Greenhouse listings) -> it accuses the payer ->
   plate makes ghost detection outcome-backed across users.
3. Will never score the employer's response rate -> rating the payer is suicide -> plate
   rates the application experience, which nobody rates.
4. Memory dies at the board's edge -> no board stores a rival's applications -> plate
   stores the whole search regardless of source.
5. Nothing after the click -> they are paid at "submitted" -> plate starts there.
6. Their agents are for recruiters (Hiring Assistant, agentic ATS) -> that is the revenue ->
   the candidate has no agent with memory; be it.
7. No feedback loop -> the outcome lives in the seeker's inbox, not on the board -> plate
   sees it via Gmail + record.
8. Auto-apply halves conversion (5.75% vs 2.68%) and gets vendors banned; Indeed paused
   its own -> volume is their pitch -> plate never submits; it remembers and improves.
9. Trackers (Teal, Huntr) are spreadsheets with a UI -> manual, no agent door, no
   outcomes -> plate is API-first, agent-native.
10. Cannot be vendor-neutral on intelligence -> Indeed's AI is Indeed's -> "LLM is
    electricity", per-user provider.
11. Cannot see silence -> they do not watch the inbox -> silence timer.
12. Hold data hostage -> lock-in is retention -> full export/delete; trust is the product.
Acquisition test: a board owning this must publish ghost rates against its own customers
and store rivals' applications. It survives only as a neutral.

### 6.2 LLM assistants (Claude, ChatGPT, Gemini, Grok, Perplexity, Copilot)
1. Memory is a scratchpad, not a ledger -> unstructured, unversioned, no receipts -> typed
   immutable application record.
2. Memory is locked to one vendor -> no vendor builds memory that works in a rival ->
   client-neutral record from any assistant.
3. No outcomes -> writes the application, never learns the reply -> loop closes where the
   record lives.
4. No durable state across sessions -> no pipeline, no reminders that fire later -> server
   job (MCP).
5. Cannot submit reliably and are retreating (OpenAI removed Agent mode Aug 2026; Indeed
   paused auto-submit) -> hostile surfaces -> be what is written to after the human clicks.
6. No cross-user data -> one user per conversation -> aggregate outcomes = network effect.
7. No domain schema (stages, staleness, visa precedence, deadlines) -> plate has them.
8. No consent/portability layer for sharing with recruiter agents -> plate owns consent.
9. Not proactive -> answers only when asked -> server + eyes + record.
10. Will not run an employer-ghosting index (liability) -> neutral domain product will.
11. Their job features are search-only and delegated (Indeed/ZipRecruiter apps in ChatGPT;
    Indeed in Claude) -> they mount job data rather than own it -> the door to walk through.
12. Incentive is the conversation, not the hire -> plate is measured on outcomes.
Acquisition test: value is neutrality; it evaporates the day one vendor owns it (Zapier,
Plaid, Stripe logic).

**Honest weakness:** an assistant could ship a structured job-search memory for its own
users tomorrow. It cannot make it work in a rival, watch Gmail outside a chat, aggregate
across users, or publish employer scores. The defence is neutral + durable +
outcome-backed + cross-user, together — not "they can't build memory".

---

## 7. Tsenta — the closest competitor (read 2026-09-01)

**Verified:** YC S26, two student founders (Rose-Hulman), launched June 2026, SF.
Self-reported 45k–70k users, "70% of paid users landed interviews"; no independent
reviews, GitHub org empty (MCP server is a landing-page claim, unverified).
https://tsenta.com/ · https://www.ycombinator.com/companies/tsenta ·
https://www.loopcv.pro/directory/tsenta/

**What they do:** watch 50,000+ career pages across 19 ATSes directly (bypass boards);
auto-apply a tailored CV the moment a role posts ("applicant #4 of 312"); review queue +
diff view; **receipt per application** (fields, answers, CV, cover letter, confirmation,
"flag for next time"); **Gmail connected** — reads replies/invites/rejections, updates
tracker, drafts recruiter replies to Drafts (never sends), enters emailed verification
codes; surfaces: web, iOS, Android, Chrome, iMessage, WhatsApp, MCP (Claude + Codex), CLI.
**Priced per application:** $19/600, $39/1,500, $99/4,500 per month.

**Read:**
- Closest thing to §3's roadmap that exists: Tier 0, half of Tier 1, Tier 2 — in one
  summer. That is the bar for speed.
- Core bet is the opposite of ours: an apply machine priced by volume. Revenue scales with
  submissions; data says volume halves conversion; platforms are fighting the surface.
  Their FAQ: "does not promise that automated assistance is undetectable."
- Their receipt is a spoon, not the plate: it records only what Tsenta submitted. Not
  neutral; boards and employers see a flood. Vendor-locked memory at startup scale.
- The danger is their data, not their product: 70k users × volume × Gmail accumulates
  outcome data faster than 0 users. If they pivot from apply-machine to record, they are
  the plate with a head start. Cannot be out-waited.
- Our thesis survives only in the parts they cannot do: neutral record across every source
  (including Tsenta), outcome-backed cross-user truth layer (they cannot indict the
  applications they are paid to send), no submission (nothing to ban), MCP both ways, no
  per-application pricing incentive.

**Take from them now:** the receipt shape (copy exactly; ours overwrites); Gmail
drafts-not-sends; diff view before approval; queue position as a freshness metric; one
agent on every surface.

**The move:** be the plate *under* Tsenta. In Box 2 their MCP and ours sit in the same
client: they apply, we remember — their applications, LinkedIn's, the recruiter's email.
A position they structurally cannot take; a competitor becomes a source.

---

## 8. THE PIVOT — owner's decision, 2026-09-02. This section supersedes where they conflict.

The 2026-09-01 park is lifted. The project restarts as a **career-ops ecosystem that
fills only the gaps** — it never rebuilds what already works elsewhere.

**The five rules:**
1. **Never source, never recommend jobs.** Matching runs ONLY on a job the user brings —
   a pasted link or description, from any board, any country, or their own AI assistant.
   A fit verdict + skill gaps on *that* job is in scope; "here are jobs for you" is not.
   (Also kills the weakest part of Tsenta — its 3.3–3.6-star store reviews complain about
   the recommendation engine, not the ops.)
2. **Everything after the click is the product.** Receipt, tracker, outreach log,
   interview record, outcome capture, silence-as-signal, improvement loop — the seven
   stages nobody fills (see the map below).
3. **No volume auto-submit.** Single, user-initiated, assisted applications only.
4. **Free for seekers AND recruiters until value is proven.** "The experience becomes the
   product." Monetisation is decided later, on evidence. Recruiter-side revenue only ever
   consent-first per INTENTION.md §11 — verified 2026-09-02: Hired.com (once $500M) shut
   down May 2024 and Triplebyte died 2024 selling candidate access; the surviving version
   is the candidate approving each introduction.
5. **The catalog stays parked.** 40 sources, scrapers, scheduler, country/gazetteer work —
   all of it. Bring-your-own-job makes the product global on day 1 with zero country work.

**The gaps-only map** (build only the right column):

| Stage | Solved elsewhere — leave it | The gap — ours |
|---|---|---|
| Find jobs | boards + assistants | nothing |
| Decide | nobody | fit verdict + gaps + red flags on the job the user brings |
| Prepare | assistants write CVs | versioned tailoring with a diff, tied to job + profile version |
| Apply | the human / their agent | **the receipt** — exact CV, letter, answers, moment; immutable |
| Outreach | Apollo, Gmail | the log: who, what, when (no table exists today) |
| Wait | nobody | silence-as-signal from the watched inbox |
| Interview | prep content everywhere | the per-round record: prepared, asked |
| Outcome | nobody | offer/rejection/reason captured |
| Learn | nobody | the loop: what worked, what to change |
| Next search | zero everywhere | the exportable career file |

**Surfaces: two.** Web app + MCP server (any agent: Claude, Codex, ChatGPT, Gemini). Every
capability in both or it is unfinished (INTENTION.md §3.3).

**Slice one:** bring-a-job + the receipt. Paste URL/description → pipeline + fit verdict →
tailor with diff → approve → mark applied → receipt frozen (fixes the
`UNIQUE(user_id, job_id, doc_kind)` overwrite in `tailored_documents`) →
"what did I send X?" answered in the UI and as MCP tool #1. First test: the owner uses it
for his own next real application.

INTENTION.md (branch `worktree-feat+mcp-server-design`) remains the constitution; its §5
lifecycle table is this same roadmap. This pivot narrows the build order, it does not
change the convictions.
