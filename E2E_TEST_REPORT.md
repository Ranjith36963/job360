# Job360 — End-to-End Test Report

**Date:** 2026-06-06
**Tested by:** Claude (automated browser test via Playwright + live backend/DB inspection)
**How:** Real Chrome browser driving the live app (frontend `localhost:3000`, backend `localhost:8000`) — clicking, typing, uploading, navigating like a real user. Then the search engine was exercised directly via the CLI against live sources, with the database and scores inspected at each step.
**Test account:** `alex.tester.job360@gmail.com` (created during the test)
**Sample CV:** `test-artifacts/sample_cv.pdf` — a made-up Senior ML Engineer (London) CV

> **UPDATE 2026-06-06 — Bugs #1 and #2 are FIXED ✅.** Per-user search now threads the
> logged-in `user.id` into `run_search` → `load_profile(user_id)` (`main.py`, `search.py`),
> and the relevance gate was relaxed from "require BOTH title and skill" to "suppress only
> when NEITHER matches" (`skill_matcher.py::_gate_suppressed_score`). Proven on live data:
> the same Reed query that returned **0 jobs** now stores **5** real ML roles (scores ≥30),
> and the authenticated `/api/search` logs *"Using dynamic keywords from user profile"*.
> Full suite green (1259 passed) + 3 new regression tests. A pre-existing **flaky** test
> (`test_cookie_tampering_rejected`, ~5% false-pass from base64 last-char malleability) was
> also fixed. Bug #3 (Jobs nav 404) and the 🟡 items remain open.

---

## Plain-English summary

The app looks great and most of it works: sign-up, login, CV upload, AI parsing, and every page renders cleanly. **But the core promise — "upload your CV and jobs come" — does not work yet, for TWO separate reasons:**

1. **🔴 The website never gives your profile to the search engine.** You upload a CV (saved in the database), click "New Search", but the engine looks for your profile in a *file* that doesn't exist for website users. So it runs with "no profile" and finds nothing.

2. **🔴 Even with a profile, the scoring rejects perfect jobs.** I fed the engine a complete ML-Engineer profile and pointed it at a UK job board (Reed). It found *ideal* matches — "Senior Machine Learning Engineer", "Lead ML Engineer", all in London — but **every one scored 10 out of 100 and got filtered out** by the 30-point minimum. The skill score (worth 40 pts) and location score were 0 even for perfect jobs. So zero jobs get saved.

Bottom line: **fixing #1 alone is not enough — #2 must be fixed too, or the app still shows zero jobs.** There's also one broken menu link (#3).

The good news: the *plumbing* all works — sources fetch live jobs, dedup works, the profile/AI parsing is excellent, and every screen is built and polished.

---

## What WORKS ✅

| Area | Result | Notes |
|---|---|---|
| Landing / Register / Login | ✅ | Register auto-logs-in & redirects; logged-out users correctly bounced to `/login?next=…`; `GET /api/auth/me` → 401 when not logged in |
| Profile page (`/profile`) | ✅ | Deep form (titles, skills, salary, locations, work mode, etc.) |
| **CV upload + AI parsing** | ✅ | PDF → parsed → profile auto-filled with skills/titles + "Skill Tiers". **Multi-provider fallback works** (Gemini key is out of quota → auto-fell back to Groq/Cerebras and succeeded) |
| Keyword generation from CV | ✅ | Produced strong keywords (aws, docker, fastapi, langchain, llms, ml, mlops, nlp…) and queries ("Senior ML Engineer UK") |
| Source fetching (live) | ✅ | Reed 170, RemoteOK 98, Arbeitnow 52 jobs fetched live |
| Deduplication | ✅ | e.g. Reed 170 → 80 unique; RemoteOK 98 → 73 |
| Dashboard / Pipeline / Channels / Account / Notifications pages | ✅ | All render with correct empty states + logged-in email |
| Channels | ✅ | Add Slack/Discord/Telegram/email/webhook; creds Fernet-encrypted |

---

## What's BROKEN ❌

### 🔴 #1 — The web search never passes the logged-in user's profile (CRITICAL)

**Symptom:** Logged in, uploaded CV, clicked "New Search" → `POST /api/search` fired (200 OK) but **0 jobs** stored, dashboard stayed empty. Backend logged `No user profile found` *after* the profile was saved.

**Root cause (confirmed in code):**
- `src/api/routes/search.py:72` → calls `run_search(source_filter=source, no_notify=True)` — **never passes `user.id`**.
- `src/main.py:345` → `run_search` always loads `load_profile(DEFAULT_TENANT_ID)` (the fixed system tenant), not the logged-in user.

So the website saves *your* profile under *your* user ID, but the engine only ever reads the **default system tenant's** profile (empty). Per-user search was never wired — the code comment at `main.py:344` even says *"Per-user profiles are the HTTP API's job."*

**Fix:** Thread `user.id` from `start_search` into `run_search(...)`; have `run_search` accept a `user_id`/`tenant_id` and call `load_profile(user_id)`.

**Verified the display side is fine:** `GET /api/jobs` (jobs.py:380) reads the shared `jobs` table and `run_search` (main.py:582) writes to it — so once the right profile is loaded *and scoring is fixed (#2)*, jobs will render. No third cut wire on the display path.

---

### 🔴 #2 — Scoring rejects even perfect-match jobs (CRITICAL)

**This was found by giving the engine a complete profile and a source full of ideal jobs.** I seeded the default tenant with the parsed ML-Engineer profile and ran live sources via the CLI:

| Source | Fetched | After dedup | Passed score ≥30 | Stored |
|---|---|---|---|---|
| Arbeitnow | 52 | 42 | **0** | 0 |
| RemoteOK | 98 | 73 | **0** | 0 |
| **Reed (UK, keyed)** | 170 | 80 | **0** | 0 |

Reed returned *perfect* matches — "Senior Machine Learning Engineer", "Senior ML Platform Engineer", "Lead ML Engineer", "Senior AI Engineer", all in **London**. Their scores:

```
inst=10  title=13  skill=0  loc=0   | Senior ML Platform Engineer (London)
inst=10  title=20  skill=0  loc=0   | Lead ML Engineer - 12 Month FTC (London)
inst=10  title=8   skill=0  loc=0   | Senior Machine Learning Engineer (London)
```

Every ideal job scored **match_score = 10**, well under the **30** minimum (`MIN_MATCH_SCORE`), so all were filtered out and **nothing was stored**.

**Why (diagnosed):**
- **`skill_score = 0` (40-pt component dead):** Reed's list endpoint returns only a ~450-char *snippet* (mostly "Rate: £650/day, Outside IR35, London"), not the full job description. The skill matcher scans description text for the user's skills and finds almost none in a short rate-focused snippet. The single biggest scoring component contributes ~0 on list-based sources.
- **`location_score = 0` for London jobs:** the CV populated skills/titles but the profile's explicit `preferences.locations` list is empty, so the location dimension has nothing to match — even though the candidate is London-based.
- Both the new instance scorer **and** the legacy `score_job()` under-score these jobs, so it isn't isolated to one path.

**Impact:** Out of the box, the pipeline stores **zero** jobs even with a perfect profile and a board full of perfect matches. This blocks the product just as hard as #1.

**Fix directions (for investigation — `src/services/skill_matcher.py`):**
- Make skill scoring not depend on a full JD that list endpoints don't provide (e.g. score skills/title together, fetch full descriptions, or weight title higher).
- Credit location from the CV/summary when `preferences.locations` is empty, and ensure "London"/"North London" match a UK/London profile.
- Re-calibrate `MIN_MATCH_SCORE` (30) against the actual multi-dim score distribution — a "Senior Machine Learning Engineer in London" for an ML-Engineer profile must clear the bar.

---

### 🟠 #3 — "Jobs" nav link 404s (MEDIUM)

The header **Jobs** link → `/jobs` shows **"404 Page not found."** `frontend/src/app/` has `jobs/[id]/page.tsx` (detail) but **no `jobs/page.tsx`** (list). The list actually lives on the Dashboard.
**Fix:** create `app/jobs/page.tsx`, or repoint the nav link to `/dashboard`.

---

## Lower-priority notes 🟡

| # | Observation | Why it matters |
|---|---|---|
| A | Source-level "relevant" filter is loose — RemoteOK returned "Data Entry Specialist", "Test Account" as "relevant" jobs | Wastes scoring effort; weak first-pass filter |
| B | Account page says "You will remain logged in after changing [password]"; hard rule #26 says password/email/delete must **invalidate the session** | UI copy vs documented security policy mismatch — verify backend clears the cookie |
| C | `GET /api/profile` 404 for new users is logged as a red console error | Expected case ("no profile yet") shown as an error — cosmetic noise |
| D | Dark-mode hydration mismatch warning on every page (the Next.js "1 Issue" badge) | Harmless dev-only SSR warning; worth fixing for a clean console |
| E | Header "Search Latest Jobs" button only links to `/dashboard`, doesn't start a search | Label implies it searches |
| F | Editable install resolves `import src` to a **git worktree** (`.claude/worktrees/reviewer/backend`), not the main checkout | A diagnostic script imported the wrong copy; could confuse local tooling |
| G | One CLI run logged `no such table: run_log` for metrics export, though the table exists in the main DB | Likely a path/connection quirk in the metrics exporter; metrics-only, non-blocking |

---

## Recommended fix order

1. **#2 — Scoring** (real matches must clear the threshold). Without this, nothing else about job discovery matters.
2. **#1 — Per-user search wiring** (`user.id` → `run_search` → `load_profile(user_id)`).
3. **Re-run this end-to-end test** to confirm jobs appear with scores and render on the dashboard, then test job detail / bookmark / pipeline.
4. **#3 — Jobs nav 404** (quick win).
5. Address 🟡 items (verify session invalidation on password change; quiet expected-404 + hydration console noise; tighten source relevance filter).

---

## Verified vs still-unverified

**Verified working end-to-end:** auth, CV upload + AI parse + fallback, keyword generation, live source fetch, dedup, DB write path, every page render.
**Verified broken:** per-user search wiring (#1), scoring rejects real matches (#2), Jobs nav (#3).
**Still unverified (blocked by #1+#2 — no jobs reach the UI):** job detail page, bookmark/apply → pipeline flow, notification delivery. Re-test after #1 + #2 are fixed.

---

## Test artifacts

- Sample CV: `test-artifacts/sample_cv.pdf`
- Screenshots: `test-artifacts/e2e-02…e2e-08*.png` — profile, dashboard, jobs-404, pipeline, channels, account, notifications
- Test account `alex.tester.job360@gmail.com` and a copy of its profile seeded onto the default tenant remain in the local `data/jobs.db` (test-only; safe to delete).
