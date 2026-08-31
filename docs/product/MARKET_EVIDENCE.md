# Market Evidence — what the world actually shows, 2026-08-31

**This file is evidence, not conviction.** `INTENTION.md` §3 holds the owner's convictions
and only the owner edits those. This file exists so a conviction can be checked against
something measured. Where evidence contradicts a line in our own docs, this file says so
plainly and points at the line. It does not change it.

Gathered 2026-08-31 by four parallel research passes plus one codebase audit. **Every
number carries its source and its confidence.** Vendor marketing dressed as research is
labelled as such.

---

## 1. The strongest single dataset we found

**Huntr Q1 2026** — real product telemetry, not a survey: 139,927 applications from
25,635 active users, 39,184 tailored resumes from 11,943 authors.
<https://huntr.co/research/job-search-trends-q1-2026>

| Applications sent | Interview rate |
|---|---|
| 11–20 | **9.25%** |
| 21–50 | 6.96% |
| 51–100 | 4.65% |
| 100+ | **2.58%** |

From the same dataset:

- Tailored resume converts **4.23%** vs **2.07%** untailored — 2.04x.
- Applying repeatedly to the *same* company degrades: 1 app → 6.07%; 8+ apps → 1.91%.
- Two-thirds of successful searches closed **within 50 applications**.
- **Median time from search-start to offer: 108 days** (up from 83 in Q4 2025).

**Why this matters more than anything else here:** it is behavioural, large, and it says
volume actively *hurts*. Every "apply to more jobs faster" product sells the thing that
lowers the user's odds. Corroborated directionally by the NBER audit study
(<https://www.nber.org/papers/w21689>), whose overall callback rate is ~15%.

> ⚠️ A widely-quoted "BLS: 30.89% vs 20.36% offer rate by application volume" figure could
> **not** be traced to any BLS publication. It appears only in SEO content mills citing
> each other. Do not use it, even though it agrees with the above.

---

## 2. Job seekers want AI for the grind, not for the decision

- **80%** use AI in their job search; **86%** say it raised their weekly volume; but only
  **20%** let AI submit applications automatically. (Clutch, n=590)
  <https://clutch.co/resources/state-ai-hiring>
- Only **26%** trust AI to evaluate them fairly; **81%** want a human in the loop; **82%**
  want to be told what is being checked. (Gartner n=3,000)
  <https://www.gartner.com/en/newsroom/press-releases/2025-07-31-gartner-survey-shows-just-26-percent-of-job-applicants-trust-ai-will-fairly-evaluate-them>
- **63%** have faced an AI interview and most describe it negatively.
  <https://www.prnewswire.com/news-releases/63-of-job-seekers-have-faced-an-ai-interview-most-havent-had-a-good-one-yet-302760120.html>

**The asymmetry is the product insight:** people use AI themselves but distrust it being
used *on* them, and they keep the send button. A design where the agent applies on the
user's behalf fights 80% of the market.

---

## 3. Employers are taxing volume and rewarding scarcity + identity

- **Greenhouse "My Dream Job"** — one priority application per month, per candidate,
  platform-wide. ~500,000 submissions, hired at **~5x the base rate**. Greenhouse also
  acquired Ezra AI Labs for mandatory voice interviews, explicitly to make mass-apply
  expensive. *(Vendor CEO commentary — flag as such.)*
  <https://fortune.com/2026/07/27/greenhouse-ceo-daniel-chait-ai-doom-loop-job-seekers-spam-interview-applications-unemployment/>
- **Ashby** shipped ATS-integrated fraud detection, 2025-09-16.
  <https://www.ashbyhq.com/blog/all/ashby-launches-the-first-ats-integrated-fraud-detection-system>
- **Greenhouse × CLEAR** — government-ID-to-selfie verification inside the apply flow.
  <https://www.greenhouse.com/newsroom/greenhouse-and-clear-announce-partnership-to-enable-candidate-verification>
- **LinkedIn "Verified Applicant Spotlight"** — recruiters can filter to verified-only.
  Verification is becoming a visibility gate. *(Secondary-sourced; no LinkedIn-official
  post located.)*
- **Gartner: 72.4% of recruiting leaders now interview in person** to fight fraud. Google
  banned AI tools in virtual interviews; Cisco and McKinsey pulled rounds back in person.
  <https://www.computerworld.com/article/4044734/to-counter-ai-cheating-companies-bring-back-in-person-job-interviews.html>
- Scale: ~254 applications per posting; application volume per recruiter **up 412%**.
- **67%** of HR leaders say AI-generated applications have slowed hiring; 20% by 2+ weeks.
  (Robert Half, n=2,000, fielded Nov 2025 — well-sourced.)
  <https://press.roberthalf.com/2026-03-10-Robert-Half-survey-67-of-HR-leaders-report-AI-generated-applications-are-slowing-hiring>

**Direction of travel: the funnel is being re-priced away from volume toward scarcity,
identity and provable history.** Favourable to a record-and-operations product; terminal
for a submit-faster product.

---

## 4. The pool itself is polluted

- **85.7%** of IT candidates and **87.5%** in marketing report hitting ghost jobs. Enhancv
  cross-referenced 1,000 professionals against Jan 2026 BLS JOLTS: IT showed 192,000
  openings vs 102,000 hires — a 47% gap.
  <https://enhancv.com/blog/ghost-jobs-survey-2026-bls-data-comparison/>
- Huntr's survey layer (n=593): **93%** applied to a suspected ghost job, **72%** hit a
  scam, **66%** report being AI-rejected, **46%** have under 3 months of runway.
- Eligibility waste is measurable: UK teacher-training rejected **4,849 applications** in
  one cycle solely because the provider could not sponsor a visa. *(Primary — UK gov.)*
  <https://becoming-a-teacher.design-history.education.gov.uk/find-teacher-training/reordering-the-filter-list>

**Filtering the pool down to real, eligible jobs is a measurable, largely unclaimed
feature.** We already have `ghost_detection.py`, `visa_signal.py` and `uk_gate.py`.

---

## 5. Ghosting and the memory hole

- **53%** ghosted by an employer in the past year, up from 38% in 2024 — a three-year
  high. (Criteria Corp 2026 Candidate Experience Report, via Fortune)
  <https://www.criteriacorp.com/blog/83-of-employers-have-been-ghosted-by-a-candidate>
- **61%** report post-interview ghosting; **40%** ghosted after a 2nd/3rd round.
  *(Indeed's primary page 404s — secondary citation only.)*
- **92%** never finish an online application (Appcast); SHRM puts mid-application
  abandonment at 60%.
  <https://www.shrm.org/topics-tools/news/talent-acquisition/people-92-never-finish-online-job-applications>
- Effort: **62.6 applications** averaging **44 min each** ≈ **46.2 hours** before landing.
  <https://novoresume.com/career-blog/job-search-statistics>

> ⚠️ **Honest gap, and it hits our core claim.** There is **no primary survey quantifying
> "I lost track of which CV I sent where."** It is inferred from the size of the
> job-tracker-template market and anecdote. `INTENTION.md` §2 states this pain as the
> reason we exist. The pain is plausible and widely served, but it is **not measured**.
> Treat it as a hypothesis to validate with our own users, not an established fact.

---

## 6. We are not first, and MCP is not a moat

- **6figr's JobGPT ships a public MCP server with 34 tools** — job search, saved hunts,
  application tracking, resume upload + AI tailoring + match scoring, recruiter/referrer
  outreach with email sending, and auto-apply. Works in Claude Desktop, Cursor, Windsurf.
  <https://github.com/6figr-com/jobgpt-mcp-server>
- **LoopCV** has a Claude integration that searches, matches and submits on the user's
  behalf. <https://www.loopcv.pro/apply-with-claude/>
- **JobPilot** markets full MCP connectivity to Claude Desktop.
- No MCP server found for **Teal, Huntr, Careerflow, Simplify, or Jobscan** — the
  incumbent trackers are MCP-absent. That is the actual opening, and it is narrow.

> ⚠️ **Contradicts `docs/product/OPERATING_MODES.md`** — "Nobody else in that diagram keeps
> history." JobGPT keeps history *and* sits in that diagram. The defensible claim is
> narrower: nobody keeps history *well enough to learn from*, and the ones who keep it
> optimise for submit-count. Owner's call whether to reword.

---

## 7. Distribution: a connector directory is not a channel

- OpenAI's directory holds **1,400+** connectable apps; Claude's Connectors Directory
  **418+** verified integrations. **Neither publishes install or usage numbers for any
  connector.** Searched for directly; this is a genuine absence.
- A live MCP marketplace operator, 2026-08-13: *"A catalog can be perfectly discoverable
  and still sell nothing… no external buyer has yet paid for a third-party listing here."*
  <https://fiatdock.com/mcp-marketplace.html>
- An audit of **1,847 public MCP servers found 52% abandoned**; registries publish no
  liveness signal, so dead and maintained servers look identical.
  <https://rapidclaw.dev/blog/mcp-servers-dead-what-it-means-2026>

> ⚠️ **Contradicts `docs/product/OPERATING_MODES.md`** — "Distribution is built in — the
> connector directory is a discovery channel." No evidence supports this. Directory
> listing is hygiene, not growth. Box 2 may still be right for the *cost* reason
> (intelligence is free there), which is independently sound. The **distribution** reason
> should be dropped or downgraded.

---

## 8. The money: cheap consumer AI subscriptions do not retain

ChartMogul, ~3,500 companies, Dec 2025 pull:
<https://chartmogul.com/reports/saas-retention-the-ai-churn-wave/>

| Price point | Gross revenue retention |
|---|---|
| Under $50/mo | **23%** |
| $50–249/mo | 45% |
| $250+/mo | **70%** (normal B2B SaaS) |

- Only **3–5%** of AI users convert to any paid tier; **44% of all subscription
  cancellations happen in the first 90 days**.
  <https://www.arcade.dev/blog/user-retention-in-ai-platforms-metrics/>
- Every competitor sits in the death band: Teal $9–29, Careerflow ~$14–24, Huntr $40,
  JobGPT credits to $34.99.
- **Job search is episodic — median 108 days.** Guidance from the pricing literature:
  *"bounded value — where the problem gets solved once — works better with one-time
  purchases."* A flat monthly sub is a charge the user resents in months 4–12.
  <https://www.airbridge.io/en/blog/subscription-vs-one-time-purchase-app>
- Pure outcome-based pricing is under 10% adoption; hybrid (low floor + usage) is heading
  to 61% by end of 2026.
  <https://www.getmonetizely.com/blogs/the-2026-guide-to-saas-ai-and-agentic-pricing-models>

---

## 9. What kills agentic products

- **~40% of the 2024 AI-startup cohort has already closed.** Named causes: commoditisation
  by the foundation models, inference burn, no data moat. Inference cost per million
  tokens fell ~80% from 2023→2025 — fatal to margin-arbitrage wrappers.
  <https://ideaproof.io/failures/ai-startups>
- Retention splits on **authenticated actions** (the agent does the task) vs passive chat,
  and on sitting **inside a workflow the user already runs** vs being a new destination.
- MIT NANDA on failed pilots: generic tools that don't adapt to a specific workflow stall
  after the demo. *(The headline "95%" figure has active methodological pushback — cite
  the mechanism, not the number.)*
- The diagnostic to keep (a16z): **"What does this app own when the model gets better,
  cheaper, and more vertically integrated? If the answer is only UI, the business is
  exposed."**

---

## 10. Our own codebase, measured against all of the above

Audited at commit `9b6cfba`, read-only, 2026-08-31.

**The weight is on the old identity.** `backend/src` is 47,239 lines.

| Area | Lines | Share |
|---|---|---|
| Job-finding (sources, scoring, dedup, enrichment, orchestrator) + its API surface | 19,716 | **~42%** |
| Profile (shared, but built to feed scoring first) | 11,350 | ~24% |
| **Application lifecycle — what `INTENTION.md` says the product is** | 5,699 | **~12%** |
| Remaining infra | ~9,811 | ~21% |

Tests tell the same story, measured from real pytest collection (3,549 collected, twice,
identical): the 19 sources/scoring files carry **595** tests; the 7 lifecycle files carry
**31**. Cross-cutting by test id it is **656 vs 27**. So **under 1% of the entire suite
touches the application lifecycle at all** — a 19–24x imbalance. Zero test files match
outreach, interview, or insight, because none of that code exists.

**The structural blocker for "Light" mode.** A user **cannot track a pasted job today**:

1. There is **no `POST /jobs`** anywhere in `src/api/routes/` — jobs enter only via the
   source pipeline.
2. `jobs` is a *shared, deduplicated catalog*, not per-user:
   `UNIQUE(normalized_company, normalized_title)` — `src/repositories/database.py:131`.
3. Both doors into the lifecycle 404 if the catalog row is missing —
   `src/api/routes/pipeline.py:96-99` and `src/api/routes/actions.py:27-29`.

So `INTENTION.md` §10's Light tier ("the user brings the job") is not buildable without
either letting `applications` exist independent of `jobs`, or minting synthetic catalog
rows — which pollutes the dedup/scoring/scheduler assumptions.

**The five things `INTENTION.md` calls "the product" have zero code and zero schema.** A
full inventory of all 28 tables across 34 migrations found **no outreach/message table**
at all. There is no outcome or rejection-reason field (`rejected` is a *stage*, not a
reason). `grep -rln "def.*insight\|def.*learn_from\|def.*analyze_history"` over
`backend/src` returns **zero matches** — nothing reads history back to produce insight.
`tailored_documents` has `UNIQUE(user_id, job_id, doc_kind)`, so regenerating a CV
**overwrites** the previous attempt — directly against the "nothing is overwritten" rule.

**What is genuinely reusable and already good:** `application_stage_history` (real
per-transition audit trail), `notes_history`, `user_profile_versions` (append-only
snapshots), `tailored_documents.ai_draft` vs `polished` (the draft→final diff is the
learning signal, already stored), `services/tailoring/provenance.py` (line-level
"grounded in your CV vs AI-added"), `audit_log`, the ARQ worker queue, and the
multi-channel dispatcher. The primitives exist; nothing joins them into a loop.

---

## 11. What we could not verify — read before quoting anything above

1. **No primary survey exists for "I lost track of which CV I sent where."** Our founding
   pain statement is unmeasured. §5.
2. **No per-connector install or usage data exists anywhere** — for us or our competitors.
   Nobody knows whether job-search MCP servers get used. §7.
3. **Channel-conversion numbers are all vendor content.** Referral vs cold-apply ("28.5%
   vs 2.7%"), outreach reply rates ("15–25% vs 2–5%") — directionally consistent across
   sources, but not one discloses methodology or sample size. Do not build a strategy on
   these.
4. **No free→paid conversion percentage** for Teal, Huntr, Careerflow or Simplify.
5. **Reddit was not directly reachable** in any pass. Real user voice came from TeamBlind
   and secondary summaries — a genuine substitution, not the primary source.
6. Gartner's "1 in 4 candidate profiles fake by 2028" — only secondary coverage found.
7. UK ONS job-search-method percentages came from an aggregator, not the ONS table.
8. The codebase audit could not **run** the test suite (Docker/Postgres was down) — only
   collect it (3,549 tests). Per-file counts are static greps, directionally reliable.

---

## 12. The lines in our own docs this evidence challenges

| Our line | What the evidence says | Status |
|---|---|---|
| `OPERATING_MODES.md` — "Nobody else in that diagram keeps history." | JobGPT's MCP server keeps history and is in that diagram. | Owner's call — narrow the claim |
| `OPERATING_MODES.md` — "Distribution is built in — the connector directory is a discovery channel." | No evidence. Directories publish no usage; an operator says a catalog can sell nothing. | Owner's call — drop or downgrade |
| `INTENTION.md` §2 — the lost-CV pain as our reason to exist | Real and widely served, but **not measured** anywhere. | Validate with our own users |
| `INTENTION.md` §10 — Light tier, "the user brings the job" | Not buildable today: no `POST /jobs`, and both lifecycle doors 404 without a catalog row. | Schema change required first |
| `INTENTION.md` §3.2 — "nothing is overwritten" | `tailored_documents UNIQUE(user_id, job_id, doc_kind)` overwrites on regenerate. | Violated in code today |

Box 2's *cost* argument (the host client's model pays for thinking) survives all of this
untouched. It is the *distribution* argument that has nothing behind it.


---

## 13. Nobody on Earth monetises job seekers at scale — checked, not assumed

The question "why has nobody won the seeker side?" has a factual answer, and it is worse
than a hard-market story. **No company anywhere runs primarily on job seekers paying.**
Checked across US, China, India, Germany, Japan and Korea.

| Company | Money raised | Status 2026-08-31 |
|---|---|---|
| **CareerBuilder + Monster** | Randstad paid $429M for Monster (2016) | **Chapter 11, 2025-06-24.** $50–100M assets vs $100–500M debt; sold off piecemeal |
| **ZipRecruiter** (NASDAQ: ZIP) | Public | Revenue **$905M (2022) → $449M (2025)**, halved. Seekers pay $0 — 100% employer-funded |
| **Indeed / Glassdoor** (Recruit) | — | Employer revenue-per-job **+35% YoY on falling posting volume** — squeezing the paying side harder. 1,300 laid off Jul 2025 |
| **LinkedIn** (Microsoft) | — | Growing, but on **ads**; Talent Solutions is the weak line. ~875 cut May 2026 despite record revenue |
| **Sonara** | undisclosed | **Shut down 2024-02-01**, users locked out without notice |
| **Teal** | $19M | Operating, ~2.8M visits/mo *(Similarweb est.)* |
| **Simplify** | $4.35M (YC, Craft) | Operating, 124 staff, traffic **+19% MoM** — best growth signal of the set |
| **Careerflow** | $800K (Techstars) | Traffic **−11.7% MoM** despite a self-reported $5.6M ARR |
| **Huntr** | ~$620K *(contested — founder says bootstrapped)* | Operating, **claims profitable** |
| **Jobscan** | **$0 — self-funded since 2013** | Operating, profitable, ~$2M rev *(third-party est.)* |
| **LazyApply** | $0, bootstrapped, <10 staff | Operating; Trustpilot 2.1–2.4/5 |

**The two "counter-examples" collapse when you read the filings:**

- **BOSS Zhipin (China)** — the model people point to as proof seekers will pay. Its own
  SEC 20-F shows the job-seeker VIP tier is **~1.15% of revenue**. ~99% is enterprise.
  <https://www.sec.gov/Archives/edgar/data/1842827/000141057825000682/bz-20241231x20f.htm>
- **Naukri (India)** — the best-documented working seeker-pay product on Earth:
  FastForward + Resume Display, **₹176cr FY26, 57% operating margin, +19% YoY**. Real,
  profitable, growing. And still **1/13th** the size of Naukri's employer business
  (₹2,256cr). A good upsell on an employer-pay business, not a seeker-pay business.

**Category funding collapsed**: job boards fell from the #1 VC investment category
(2017–19) to **#11 by 2024 — $2.4B down to $220M**.
<https://1worktech.com/the-job-board-isnt-dead-the-business-model-is/>

### The pattern that actually matters to a solo founder

Sort the table by who raised money. **Every venture-scaled seeker-side attempt died or
shrank. The ones still standing and profitable — Jobscan ($0 raised, profitable since
2013), Huntr (bootstrapped, claims profitable) — are small.**

So the honest conclusion is not "this cannot work." It is:

> **A large seeker-pay business has never existed. A small profitable one demonstrably
> has.** Naukri's 57% operating margin on a seeker product proves the unit economics can
> be excellent. It just never gets big.

That is a statement about the *ceiling*, not the *floor* — and a solo founder needs the
floor. It also means `INTENTION.md` §11 (the B2B direction) is pointed exactly where all
the money on Earth actually is, in every market checked.

### Could not verify

Sonara's status today (sources conflict); Liepin's revenue split; the job-seeker-only
slice of LinkedIn's $2B Premium line; whether Indeed Premium ($9.99/mo) earns anything at
all — never mentioned in any Recruit investor material. Several Crunchbase/SEC fetches
returned 403, so Huntr's and Careerflow's funding figures come via secondary coverage.
