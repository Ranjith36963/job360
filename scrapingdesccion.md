# Scraping Decision — LinkedIn / Indeed / Glassdoor

> **Status: DECISION PENDING — measurement required before acting.**
> Owner: Ranjith. Written 2026-07-17. Everything below is either verified against
> code (with file:line) or explicitly labelled as opinion/advice.
>
> **Count correction, 2026-08-24 (doc truth check).** The roster shrank after this
> was written. Every "47 registry entries / 46 unique sources" below should read
> **41 registry entries / 40 unique source classes** (measured: `SOURCE_REGISTRY`
> at `backend/src/main.py:110-154`, `SOURCE_INSTANCE_COUNT = 40` at `main.py:168`).
> The *argument* is unaffected — the 3 at-risk sources are still `linkedin`,
> `indeed`, `glassdoor` — so the real question is **40 vs 39**, not 46 vs 45.
> The prose is left as written; this note is the correction.

---

## 1. TL;DR

- Job360 has **47 registry entries / 46 unique sources**. Only **3** carry legal risk:
  `linkedin`, `indeed`, `glassdoor`.
- **In production, only LinkedIn actually scrapes.** `indeed` + `glassdoor` are
  **already inert on Railway** because `python-jobspy` is not installed in the prod
  image. So the real prod question is **46 vs 45 sources, not 46 vs 43**.
- The LinkedIn scraper is **real, hand-written, live in prod, and ungated**.
- Advice (Fable + assistant): **remove LinkedIn from the free tier before public
  release.** The risk is not "today at 8 users" — it is **the launch itself**.
- **Not yet known:** how many jobs would actually be lost. Dedup may mean the answer
  is "almost none". **Measure first** (§8). Do not cut on vibes.

---

## 2. What `SOURCE_REGISTRY` actually is

Not just a list of names. It is a **dict mapping a source name → a real working
class** with a working `fetch_jobs()`. Verified in `backend/src/main.py:110-128`:

```python
"jobicy":    JobicySource,
"greenhouse":GreenhouseSource,
"linkedin":  LinkedInSource,     # <- real HTML scraper
"indeed":    JobSpySource,       # <- real scraper (via python-jobspy)
"glassdoor": JobSpySource,       # <- same class instance
...
```

`backend/src/main.py:157` notes: *"46 not 47 because 'indeed' and 'glassdoor' both
map to JobSpySource (one instance)."*

**Answer to "is it just a list / did I never write scraping code?"** — No. It maps to
classes containing genuine fetch+parse code. See §3.

---

## 3. Evidence: the LinkedIn scraper is real

`backend/src/sources/scrapers/linkedin.py`:

| Line | Fact |
|------|------|
| 11 | `_BASE_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"` — LinkedIn's **internal guest endpoint** (undocumented; not a public API) |
| 42 | `html = await self._get_text(_BASE_URL, params=params)` — pulls **raw HTML** |
| 21 | `_LINK_RE = re.compile(r'href="(https://[^"]*linkedin\.com/jobs/view/[^"]*)"')` — **regex-extracts** job links out of that HTML |
| 28 | `async def fetch_jobs(self)` — the standard source entrypoint |

That is textbook scraping: undocumented endpoint + HTML + regex.

**Indeed/Glassdoor:** the scraping is done by the third-party `python-jobspy`
package, not hand-written here. Legally that is a distinction without a difference —
**your server, your IP, your domain** makes the requests. But see §4: it doesn't run
in prod anyway.

---

## 4. What is ACTUALLY live in production (the key correction)

This is the fact that changed the recommendation. **Registry = intent. Dockerfile =
what ships.**

`backend/Dockerfile:18-20`:
```dockerfile
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && pip install --no-cache-dir ".[semantic]" arq
```

- It installs the **`[semantic]`** extra only. It does **NOT** install **`[indeed]`**
  (`pyproject.toml`: `indeed = ["python-jobspy"]`).
- Therefore **`python-jobspy` is absent in prod** → `indeed` + `glassdoor` fetch
  **nothing** (the code skips gracefully when the package is missing).
- **LinkedIn needs no extra package** (only `aiohttp`, a core dep) → **it runs on
  every prod pipeline run**, from Railway's IP, under job360.uk.
- **No env gate** exists on linkedin/indeed/glassdoor (grep found only an unrelated
  `source_filter` line at `src/main.py:304`).

**Locally** `python-jobspy 1.1.82` IS installed — so indeed/glassdoor DO scrape on the
dev laptop. That is the developer's machine, not the product.

| Source | Local dev | **Production (Railway)** |
|---|---|---|
| `linkedin` | live | **LIVE — scraping** |
| `indeed` | live | **inert** (no jobspy in image) |
| `glassdoor` | live | **inert** (no jobspy in image) |

> ⚠️ **Caveat — not yet proven:** this proves what the prod **image installs**, not
> what the scheduler **actually ran**. The airtight proof is prod's `run_log` table
> (rows per source per run). See §8.

---

## 5. Invited vs uninvited — why 43 sources are fine and 3 are not

All 46 sources pull **live, real-time data**. The distinction is **not** live-vs-cached
or API-vs-HTML. It is **invited vs uninvited**.

**~43 sources — live data you are welcome to:**
- **ATS boards** (Greenhouse, Lever, Workable, Ashby, SmartRecruiters, Workday,
  Personio, Recruitee, Pinpoint, SuccessFactors, Rippling) — public job-board APIs.
  Companies *want* their jobs syndicated; that is what the endpoints exist for.
- **RSS/XML feeds** (NHS Jobs, jobs.ac.uk, WeWorkRemotely, BioSpace, University Jobs,
  WorkAnywhere, RealWorkFromAnywhere, Teaching Vacancies) — a feed is a **published
  invitation** to consume it.
- **Free JSON APIs** (RemoteOK, Arbeitnow, Remotive, Jobicy, Himalayas, DevITjobs,
  Landing.jobs, AIJobs.net, HN Jobs) — documented, intended for this.
- **Keyed APIs** (Reed, Adzuna, JSearch, Jooble, Careerjet, Findwork, Google
  Jobs/SerpApi, DfE apprenticeships) — you hold a **key**; that is permission + terms.

**3 sources — live data taken without permission:** LinkedIn, Indeed, Glassdoor.

> LinkedIn's `jobs-guest` endpoint exists to render **their** page in **your browser**.
> Using it as a data feed takes something never offered. Same freshness — completely
> different legal footing.

---

## 6. The legal analysis (Fable's advice — labelled as advice, not law)

Fable was explicitly asked to say if the assistant was **over-worrying** at 8 users,
pre-revenue, hobby scale. It said no. Verbatim substance:

**Verdict:** *"Real flaw — pull the 3 scrapers before public release. Keep the rest of
his sequence exactly as is."*

- **"At 8 users, nobody sues you; the risk isn't today, it's the launch."** Public
  release is precisely when a named, hosted UK SaaS redistributing scraped
  LinkedIn/Indeed/Glassdoor content becomes **visible and attributable**.
- **hiQ v LinkedIn nuance** (the strongest counter-argument, and it fails): hiQ won the
  **CFAA** point — public scraping ≠ "unauthorized access" — but then **lost on
  breach-of-contract on remand and folded**. **ToS claims survive.**
- **UK law is worse, not better:** adds **database right** + **Computer Misuse Act**
  exposure, with **no hiQ shield** (hiQ was US law).
- **What actually happens to small operators:** IP bans first → **cease & desist to the
  registered owner of job360.uk** → account termination. LinkedIn is *"the most
  litigious scraping plaintiff on earth"* and occasionally sues tiny outfits to make
  examples.
- **The kicker:** *"A C&D alone kills a pre-revenue product's credibility and forces the
  removal anyway, on their timeline not his."*
- **Bonus:** JobSpy/scrapers **break constantly**, so the value being protected is
  unreliable anyway.

**The owner's own Fable audit already flagged this** — `docs/harness/fable/00-EXECUTIVE-SUMMARY.md`
lists it as **blocker #3 of 4**: *"Scraping LinkedIn/Glassdoor/Indeed — existential
business + legal risk; fails any enterprise legal review."*

---

## 7. How this fits the release sequence (it does NOT break it)

**The owner's firm sequence:**
1. Fix + harden the **FREE tier** to enterprise grade — code, security, **and docs**
2. **Public release**
3. **Then** pricing + tier gating (PLAN-5)
4. **Paid job APIs** (Fantastic Jobs ~$1/1k hourly, TheirStack) enter at step 3 — as a
   **paid-tier feature**, not a free-tier cost

**This decision is a free-tier correctness item, not "business work early."**
The bar is *enterprise-grade before public release*. A source set that **fails legal
review is not enterprise-grade**, however clean the code is. It belongs in the
pre-release list already committed to.

**It also improves the product story:**
- **Free tier** = 43+ legitimate, invited, live sources
- **Paid tier** = LinkedIn/Indeed coverage **restored legitimately** via licensed APIs

That is exactly the Fantastic Jobs / TheirStack plan already researched — unchanged.

---

## 8. What we DO NOT know — measure before cutting

**The open question:** what is the real difference, in prod, for users and jobs
retrieved, between having these sources and not?

**Why the naive answer is wrong:** the **deduplicator** collapses the same job across
sources (4-layer: exact key → RapidFuzz → TF-IDF → embedding). Job boards syndicate
heavily — a role on LinkedIn is very often **also** on the company's Greenhouse/Lever
board, which is already pulled legitimately.

> **So the cost of dropping LinkedIn is NOT "all its jobs" — only the jobs that
> NO OTHER source carries.** That could be ~30% of the feed, or ~2%. Guessing is not
> acceptable here.

### Queries to run against **PROD** (Railway Postgres) — read-only

**Q1 — volume per source (last 30 days):**
```sql
SELECT source,
       COUNT(*)              AS runs,
       SUM(jobs_found)       AS jobs_found,
       SUM(jobs_new)         AS jobs_new
FROM run_log
WHERE run_at > NOW() - INTERVAL '30 days'
GROUP BY source
ORDER BY jobs_new DESC;
```
*Answers: do linkedin/indeed/glassdoor contribute anything in prod at all?
(Expectation from §4: indeed/glassdoor = 0 rows or 0 jobs.)*

**Q2 — the number that matters: jobs ONLY LinkedIn has:**
```sql
-- jobs whose normalized_key appears ONLY from linkedin (no other source carries it)
SELECT COUNT(*) AS linkedin_only_jobs
FROM jobs j
WHERE j.source = 'linkedin'
  AND NOT EXISTS (
        SELECT 1 FROM jobs o
        WHERE o.normalized_key = j.normalized_key
          AND o.source <> 'linkedin'
  );

-- for scale, total unique jobs in the same window
SELECT COUNT(DISTINCT normalized_key) AS total_unique FROM jobs;
```

**Q3 — user impact: did those unique jobs ever reach a feed / score well?**
```sql
SELECT COUNT(*) AS linkedin_only_in_feeds,
       ROUND(AVG(uf.score), 1) AS avg_score
FROM user_feed uf
JOIN jobs j ON j.id = uf.job_id
WHERE j.source = 'linkedin'
  AND NOT EXISTS (
        SELECT 1 FROM jobs o
        WHERE o.normalized_key = j.normalized_key AND o.source <> 'linkedin'
  );
```

**How to run (safely):**
- `railway connect postgres` (or `railway run psql`) — owner runs, pastes numbers back.
- ❌ **Never paste the prod `DATABASE_URL` into a chat** — it is a live credential.

### Decision rule once numbers exist
- **LinkedIn-only jobs are a trivial share (say <5% of unique, low avg score)** →
  remove it; the free tier barely notices. **Clear call.**
- **LinkedIn-only jobs are a large share (say >20%, good scores)** → still remove
  before public release (the legal logic in §6 does not change), but **prioritise
  bringing Fantastic Jobs forward** so coverage is restored at launch, not after.

---

## 9. If/when we remove — the five surfaces (rules #8 / #13)

Removing a source touches **five** load-bearing places, not four:

1. `backend/src/main.py` — `SOURCE_REGISTRY` dict
2. `backend/src/main.py` — `_build_sources()` list
3. `backend/src/main.py` — `RATE_LIMITS` dict
4. `backend/tests/test_cli.py` — hardcoded `len == N` + expected source set
5. `backend/tests/test_api.py` — hardcoded `== N` in `test_sources_returns_*`,
   `test_status_returns_counts`, `test_full_api_workflow`

Also: `pyproject.toml` `indeed = ["python-jobspy"]` extra can go if indeed/glassdoor
are dropped. Count moves **47 → 44** registry entries (46 → 43 unique) if all 3 are
removed; **47 → 46** (46 → 45) if only LinkedIn goes.

Estimated effort (Fable): **one commit, ~1 hour.**

---

## 10. Options on the table

| Option | What it means | Risk |
|---|---|---|
| **A. Remove all 3 now** | Cleanest. Kills the #1 legal blocker + removes dead weight (indeed/glassdoor are inert in prod but live on dev laptop). | Loses LinkedIn-only jobs (unknown size — §8) |
| **B. Remove LinkedIn only** | Minimum urgent action; indeed/glassdoor already inert in prod so they're not the fire. | Leaves jobspy scraping on the dev machine + a re-enable footgun (someone installs the extra → prod scrapes again) |
| **C. Keep all, remove at pricing stage (original plan)** | No work now. | **Ships the legal risk INTO public release** — the one item where waiting *increases* risk rather than delaying a benefit |
| **D. Measure first, then A or B** | ← **Recommended.** Run §8 queries, decide on numbers. | Costs one query session |

---

## 11. Corrections log — what the assistant got wrong along the way

Kept deliberately, because the pattern matters more than the conclusion.

1. **"The LLM judge is off in prod"** — **WRONG.** `MATCHER_ENABLED=true` in Railway
   *and* local `.env`. The assistant read `.env.example`'s **default** (`false`) and
   CLAUDE.md, never the running config. An entire recommendation (and a Fable
   consult) was built on a false premise.
2. **"Remove 3 scrapers"** — **⅔ wrong for prod.** Reasoned from `SOURCE_REGISTRY`
   (intent) without checking `Dockerfile` (what ships). indeed/glassdoor were already
   inert in prod. The real exposure is **one** scraper.
3. **"Scraping is existential"** — quoted from the Fable audit **as fact all day**
   without calibration. Fable, asked directly whether this was over-worrying,
   confirmed the substance — but the assistant should have flagged it as *the audit's
   rhetoric* until independently checked.

**The rule these produce:** before asserting anything, ask *which artifact would prove
it* — deployed env? the Dockerfile? prod's `run_log`? — and read **that**. Registry ≠
image ≠ runtime. "I checked" is worthless if the wrong instance was checked.

---

## 11b. Related finding while writing this doc — the PR #44 clobber

Discovered 2026-07-17 while trying to commit this file. Recorded here because it
affects **what main actually contains** — i.e. whether the free-tier bar can even be
assessed. Verified against a fresh `git fetch`.

**What happened:** commit `3f532b2` ("fix(security): XML billion-laughs guard +
timing-safe login (P2)") was built on a **stale base**. Landing it reverted newer work.
It reached main via the **PR #44 merge the assistant performed** from a branch another
session owned. Its own diffstat shows it deleted:

- `.github/workflows/security.yml` — **95 lines** (bandit / gitleaks / pip-audit CI)
- `PLAN-1-fix-red-ci.md` (218) + `PLAN-2-unblock-real-signups.md` (252)
- `backend/tests/test_xxe_hardening.py`, `test_security_hardening.py`, `test_pg_translate.py`
- `frontend/src/lib/security-headers.ts` (the M15 fix)
- plus edits to `CLAUDE.md`, `.env.example`, `.gitignore`, `commit-gate.sh`

**Status:** `b465fca` ("restore 4,348 lines clobbered by stale-base commit 3f532b2
(PR #44)") exists on another session's branch but is **NOT on main** as of `729bf0c`.

**⚠️ Assistant's WRONG alarm (kept deliberately):** it first claimed *"main has the XXE
vulnerability back"* because `grep -c defusedxml` returned **0** on main. **That was
wrong.** Main defends via a *different* mechanism: `_sanitize_xml()` in
`backend/src/sources/base.py` strips `<!DOCTYPE ...>` (incl. the internal `[...]`
subset) and `<!ENTITY ...>` declarations **before** parsing — and billion-laughs/XXE
requires an entity declaration. It is applied centrally to every XML source.
**Main is NOT exposed.**

> **The error shape:** absence of *my fix* was read as absence of *protection*.
> `grep -c defusedxml → 0` proved the code was gone; it proved nothing about the
> property that mattered. Same failure as §11.1 (read the default, not the runtime):
> measuring the artifact expected instead of the property cared about.

**Net:** the clobber is **real and worth restoring** (lost security CI, tests, M15, docs)
but is **not a live security hole**. Not an emergency. The other session's restore
handles it. **Do not run a "verification pass" of audit findings until `b465fca` lands
— main does not currently contain what it is assumed to contain.**

---

## 12. Next action

1. **Owner runs the §8 queries against prod** (`railway connect postgres`) and pastes
   the numbers.
2. Decide **A** or **B** using the §8 decision rule.
3. Execute the five-surface removal (§9) as one gated commit.
4. Fantastic Jobs / TheirStack proceed **unchanged**, at the pricing stage (§7).

**Nothing has been removed yet. No code changed by this document.**
