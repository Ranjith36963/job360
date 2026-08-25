<!-- doc: LIVING | last-verified: 2026-08-24 by /sync -->
# Pillar 1 — The User Side

> **Audience.** Read this if you want to understand everything Job360 does *for a human end-user* — sign up, upload a CV, see matched jobs, track applications, get notified. This document covers no source-fetching internals and no scoring math; those are Pillars 2 and 3.
>
> **Scope.** Covers the code that exists on `main` as of 2026-08-14 (HEAD `09727e5`). Where a surface is partially wired or carries a known gap, it is called out in the **Status** column.

---

## 1. TL;DR — what the User pillar does

In one sentence:

> *Job360 lets a user create an account, describe themselves (CV + LinkedIn + GitHub + preferences), automatically receive a personalised stream of UK jobs ranked 0–100, take action on them (like, apply, dismiss), track their applications through a pipeline, and get notified via email/Slack/Discord/Telegram/webhook with per-channel quiet-hours and digest schedules.*

The user pillar is implemented as **four concentric rings**, each one assuming the previous:

```
       ┌──────────────────────────────────────────────────────┐
       │  Ring 4 — UI (Next.js 16 dashboard, profile, pipeline)│
       │ ┌────────────────────────────────────────────────────┐│
       │ │ Ring 3 — Delivery (feed, channels, notifications)  ││
       │ │ ┌────────────────────────────────────────────────┐ ││
       │ │ │ Ring 2 — Profile (CV + LinkedIn + GitHub)      │ ││
       │ │ │ ┌────────────────────────────────────────────┐ │ ││
       │ │ │ │ Ring 1 — Identity (users, sessions, auth)   │ │ ││
       │ │ │ └────────────────────────────────────────────┘ │ ││
       │ │ └────────────────────────────────────────────────┘ ││
       │ └────────────────────────────────────────────────────┘│
       └──────────────────────────────────────────────────────┘
```

The "shared catalog + per-user overlay" rule (CLAUDE.md rules #1, #10) is the architectural foundation: the `jobs` table is **shared across every user** — there is *no* `user_id` column on it. Every per-user fact (what you've seen, what you've liked, what stage your application is at, whether you've been notified) lives in a *separate* user-scoped table that joins to `jobs` by `job_id`.

---

## Walkthrough — A day in the life of a user (worked example)

> One concrete trace through all four rings, following one user (call her Alice) from signup to her first notification. Every step names the route, the service, and the table touched. If you've never opened a code file in this repo, read this first.

### T+0 — Register

Alice visits `/register`. Frontend `POST /api/auth/register {email, password}`. The route (`routes/auth.py`):

1. Validates email + password (min 8 chars).
2. `passwords.hash_password(plaintext)` → argon2id hash.
3. `INSERT INTO users(id=uuid4(), email, password_hash, created_at)`. `deleted_at` defaults NULL.
4. `sessions.create_session(user_id, SESSION_SECRET, user_agent, ip_hash)` → cookie string `<sid>.<hmac>`.
5. Response sets `Set-Cookie: job360_session=<sid>.<hmac>; HttpOnly; SameSite=lax`.

Frontend redirects to `/profile`.

### T+5 — Upload profile

Alice uploads her CV PDF. Frontend `POST /api/profile` with multipart `cv=<file>` + JSON body of `UserPreferences`. Route (`routes/profile.py`):

1. `require_user` resolves the cookie → `CurrentUser(id=<uuid>, email=...)`.
2. Save uploaded file to a temp dir.
3. `cv_parser.parse_cv_async(temp_path)`:
   - `extract_text_from_pdf()` via `pdfplumber`.
   - `llm_extract_validated(prompt, CVSchema, max_retries=2)` — **OpenAI (`gpt-4o-mini`) first**, then Gemini; on Pydantic `ValidationError`, errors are appended to the prompt and the call retries; falls through to Groq, then Cerebras.
   - Returns `CVData` (skills, titles, companies, education, …).
4. `preferences.merge_cv_and_preferences(cv_data, prefs)` → composite skills/titles list with user prefs taking priority.
5. `UserProfile(cv_data, preferences)` saved via `storage.save_profile(profile, user_id, source_action="upload")` — UPSERTs `user_profiles` (tip) **and** INSERTs `user_profile_versions` (immutable snapshot, retention 10).

Response: `ProfileResponse` with `skill_tiers`, completeness %, `current_version_id`.

Optional: LinkedIn upload (`POST /api/profile/linkedin`) and GitHub (`POST /api/profile/github {username}`). LinkedIn detection uses the 2-of-3 heuristic; GitHub fetches up to 30 repos with 3× weighting on the last 365 days.

### T+10 — Engine builds Alice's SearchConfig

When the worker tick runs (or CLI `python -m src.cli run` is invoked) — Pillar 2's `run_search()`:

1. Loads Alice's `UserProfile` via `storage.load_profile(alice.id)`.
2. Generates `SearchConfig` from it (Pillar 2 §3.1) — the bridge from Pillar 1 to Pillar 2.
3. Instantiates `JobScorer(search_config, user_preferences=alice.prefs, enrichment_lookup=lookup)`. `user_preferences` is what turns the Batch-2.9 dims on (rule #20); the lookup is optional and only decides whether they read real data or their neutral halves.
4. Domain-filters sources via `classify_user_domain(alice.profile)` → say `{"tech"}` → keeps tech + general sources, drops healthcare/academia/education/climate-only sources.
5. Fetches → prefilters → scores → dedups → stores.

For each job that survives the prefilter (Pillar 2 §2 stage 2) and scores ≥ `MIN_MATCH_SCORE` for Alice, the worker `score_and_ingest(job, users=[alice])` calls `FeedService.upsert_feed_row(alice.id, job.id, score, bucket)`.

### T+10 — Dashboard shows Alice's ranked feed

Alice opens `/dashboard`. Frontend `GET /api/jobs?bucket=24h&min_score=60` (TanStack Query caches by `queryKeys.jobList(filters)`). Backend (`routes/jobs.py`) scopes by `user.id`, JOINs `user_feed` + `jobs` + LEFT JOIN `job_enrichment`, pre-fetches `action_map` to avoid N+1, returns `JobListResponse` with the 9-dim `ScoreBreakdown`. Frontend renders `<JobList>` of `<JobCard>` with `<ScoreRadar>`.

### T+12 — Alice likes Job#42

Heart icon clicked. Frontend `POST /api/jobs/42/action {"action": "liked"}`. Route (`routes/actions.py`) UPSERTs `user_actions(user_id=alice.id, job_id=42, action='liked')`. UI optimistically marks the card liked.

### T+15 — Alice applies

Click "Apply" → browser opens external `apply_url`. Returning, Alice clicks "Mark Applied". Frontend `POST /api/pipeline/applications {"job_id": 42}`. Route (`routes/pipeline.py`):

1. **410 Gone** if `jobs.staleness_state='confirmed_expired'` (ghost-detection guard).
2. INSERT `applications(user_id, job_id=42, stage='applied')`.
3. INSERT `application_stage_history(user_id, job_id=42, from_stage=NULL, to_stage='applied')` — the audit trail starts.
4. UPDATE `user_feed SET status='applied' WHERE user_id=alice.id AND job_id=42`.

### T+30 — Alice sets up email notifications

On the channels page:

- `POST /api/settings/channels {channel_type:"email", display_name:"primary", credential:"alice@example.com"}` — for `email` the `credential` is a **plain address**, not an Apprise URL. Anything that is not a valid address is refused 422 (`backend/src/api/routes/channels.py:160-166`).
- The backend builds the Apprise URL itself from the PLATFORM's mail credentials — `build_email_apprise_url(dest)` (`services/channels/email_url.py:97`) — then `channels.crypto.encrypt(...)` → Fernet ciphertext → INSERT `user_channels`. With no mail transport configured the route returns 503 "email delivery is not configured" (`channels.py:179-183`).
- `slack`, `discord` and `telegram` cannot be created here at all: they are Connect-flow only and this route returns 400 for them (`channels.py:36,128-132`).
- Alice clicks "Test" → `POST /api/settings/channels/{id}/test` → dispatcher decrypts, calls Apprise, returns `{ok: true}`.

Then a rule:

- `PUT /api/settings/notification-rule {score_threshold:80, notify_mode:"instant", quiet_hours_start:"22:00", quiet_hours_end:"07:00"}`
- UPSERT by `UNIQUE(user_id)`. Stored in `notification_rules`. **One rulebook per user, not one per channel** (CLAUDE.md rule #23) — the rule governs every channel Alice has connected, so there is no `channel` field in the body.

### T+2h — A fresh job posts, Alice gets notified

Worker fetches a new job, scores it 87 for Alice. `score_and_ingest`:

1. `FeedService.upsert_feed_row(alice.id, job.id, 87, "24h")`.
2. 87 ≥ 80 (Alice's email rule threshold) → enqueue `send_notification(alice.id, job.id, urgency="instant")`.

`send_notification` worker task:

1. Loads Alice's enabled channels → finds the email channel.
2. Consults Alice's single `notification_rules` row (looked up by `user_id` alone): enabled, threshold ≤ 87 ✓.
3. Current time in Alice's `users.timezone` → outside quiet hours (22:00–07:00) ✓.
4. Idempotency check on `notification_ledger UNIQUE(user_id, job_id, channel)` — no row → proceed.
5. `crypto.decrypt()` the channel credential.
6. `dispatcher.dispatch()` lazy-imports Apprise (rule #11), calls `notify()`.
7. Success → INSERT `notification_ledger(status='sent', sent_at=now())`. Failure → INSERT with `status='failed'`, `retry_count=1`, `error_message`.

Alice's inbox: one email with job title, score 87, deep link to `/jobs/<id>`.

### T+5 days — Alice advances the application

After an interview: `POST /api/pipeline/42/advance {"to_stage": "interview", "notes": "1st screen w/ recruiter"}`. Route updates `applications.stage='interview'`, sets `last_advanced_at=now()`, INSERTs an `application_stage_history` row with `from_stage='applied'`, appends to `notes_history` JSON.

### Tables touched, in order

| Step | Table | Pillar |
| --- | --- | --- |
| Register | `users`, `sessions` | 1 |
| Upload CV | `user_profiles`, `user_profile_versions` | 1 |
| (Engine runs) | `jobs`, `job_enrichment` (shared catalog — no `user_id`) | 2+3 |
| Engine scores for Alice | `user_feed` (write) | 2 → 1 seam |
| Dashboard view | `user_feed`, `jobs`, `job_enrichment` (read) | 1 |
| Like | `user_actions` | 1 |
| Apply | `applications`, `application_stage_history`, `user_feed` (status flip) | 1 |
| Channel setup | `user_channels` (Fernet) | 1 |
| Rule setup | `notification_rules` | 1 |
| New job notification | `notification_ledger`, optionally `user_notification_digests` | 1 |
| Stage advance | `applications`, `application_stage_history` | 1 |

The **shared `jobs` catalog never gets a `user_id`** (rule #10). Every per-user fact lives in an overlay table joined by `job_id`. That's what makes Job360 multi-tenant without duplicating job rows.

---

## 2. Ring 1 — Identity & Authentication

### 2.1 What the user experiences

| Action | URL | What happens |
| --- | --- | --- |
| Register | `POST /api/auth/register` (UI: `/register`) | Email + password → user row created, signed cookie set, redirect to `/profile` |
| Log in | `POST /api/auth/login` (UI: `/login`) | Email + password verified → signed cookie set, redirect to `?next=` or `/dashboard` |
| Stay logged in | (browser auto-sends `job360_session` cookie) | 30-day TTL, HMAC-signed |
| Log out | `POST /api/auth/logout` | Session row deleted, cookie cleared |
| See "who am I" | `GET /api/auth/me` | Returns `{id, email}` |
| Change password | `PATCH /api/auth/users/me/password` | Requires current password |
| Change email | `PATCH /api/auth/users/me/email` | Requires current password; clears session (must re-login) |
| Delete account | `DELETE /api/auth/users/me` | Soft-delete (`deleted_at` is set; row preserved for audit) |

### 2.2 How it works under the hood

- **Password hashing — `backend/src/services/auth/passwords.py`.** Argon2id with OWASP-recommended params (`time_cost=3`, `memory_cost=64 MiB`, `parallelism=4`). Salt is unique per password; verification is constant-time.
- **Sessions — `backend/src/services/auth/sessions.py`.** Cookie format `<session_id>.<hmac>` signed with `itsdangerous.TimestampSigner` using `SESSION_SECRET`. Signature is verified *before* any DB lookup, so a tampered cookie is rejected for zero cost. 30-day absolute TTL.
- **FastAPI guards — `backend/src/api/auth_deps.py`.** Two dependencies: `require_user` (401 on missing/invalid/expired cookie) and `optional_user` (returns `None`). Both populate a frozen `CurrentUser` dataclass with `{id, email}`.
- **Routes — `backend/src/api/routes/auth.py`.** All seven endpoints in the table above. There is **no** rate-limit or lockout on login today. Email-verification and password-reset endpoints **do** exist (verify-email is not enforced at login; password-reset is fully wired) — see Status table at the end.
- **DB schema — `backend/migrations/0001_auth.up.sql`.** Two tables: `users(id, email UNIQUE, password_hash, created_at, deleted_at)` and `sessions(id, user_id FK, created_at, expires_at, last_seen, user_agent, ip_hash)`.
- **Multi-tenant placeholder — `backend/src/core/tenancy.py`.** Constant `DEFAULT_TENANT_ID = "00000000-0000-0000-0000-000000000001"`. Used as the owner for any row created by the *CLI* (pre-auth) or migrated from the pre-Batch-2 single-tenant world. Its `password_hash` is literally `"!"` so it can never be logged into. This is how migration `0002_multi_tenant` backfilled legacy data without throwing it away.

### 2.3 Frontend pages

- `frontend/src/app/(auth)/login/page.tsx` and `.../register/page.tsx` — client components wrapping a small form. Both honour `?next=<url>` with a `safeNext()` open-redirect guard.
- `frontend/src/components/layout/Navbar.tsx:1` — top-of-page nav. Shows user email and a Log out button when authenticated; shows Log in / Register otherwise. The session cookie is `HttpOnly`, so the frontend never *reads* it directly — it just relies on the browser to send it and checks the `/api/auth/me` response.

### 2.4 Security posture

- Cookie flags: `httponly=True`, `samesite="lax"`, `secure=True` only when `JOB360_ENV=prod`.
- Two required secrets, both **fail-closed** (server refuses to start without them): `SESSION_SECRET` (HMAC) and `CHANNEL_ENCRYPTION_KEY` (Fernet for channel credentials — see Ring 3).
- IDOR guard: per CLAUDE.md rule #12, no per-user route accepts a user-id in the URL. Identity always comes from the cookie-resolved `CurrentUser`.

---

## 3. Ring 2 — Profile (CV + LinkedIn + GitHub + Preferences)

The profile is what turns Job360 from "show me all 41 sources' raw output" into "show me the jobs *I* care about." Every downstream piece of the pillar — what's in the feed, what gets scored highly, what gets notified — depends on a populated profile.

### 3.1 What the user experiences

There are two entry points — the **CLI** (single-tenant power-user flow) and the **API** (per-user, used by the dashboard).

**CLI** (`backend/src/cli.py setup-profile`):

```bash
python -m src.cli setup-profile --cv cv.pdf --linkedin linkedin.pdf --github username
```

Writes to `DEFAULT_TENANT_ID` (the placeholder user). Used by single-tenant installs and tests.

**API** (`backend/src/api/routes/profile.py`):

- `GET /api/profile` — returns the caller's profile, 404 if none yet
- `POST /api/profile` — accepts a CV file + a preferences JSON body; parses asynchronously
- `POST /api/profile/linkedin` — accepts a LinkedIn "Save to PDF" upload
- `POST /api/profile/github` — accepts `{username}`
- `GET /api/profile/versions` — returns the last 10 snapshots
- `POST /api/profile/versions/{id}/restore` — atomic rollback to a snapshot
- `GET /api/profile/jsonresume` — exports the profile in [JSON Resume](https://jsonresume.org/) schema

### 3.2 The four data sources

#### CV (PDF / DOCX) — `backend/src/services/profile/cv_parser.py`

1. **Extract text** with `pdfplumber` (with font-size clustering for layout-aware section splitting — see `layout.segment_sections_from_words`) or `python-docx`.
2. **Call an LLM** via `llm_extract_validated(prompt, CVSchema)` (`backend/src/services/profile/llm_provider.py`). The schema is enforced with Pydantic; on validation failure the prompt is re-sent up to 2× with the validation error appended so the model can self-correct.
3. **Provider fallback chain** (`llm_provider.py:329-334`): **OpenAI (PRIMARY)** → Gemini → Groq → Cerebras. Whichever has a working API key wins. If all four fail, `RuntimeError` is raised — the system never silently degrades to regex parsing (the old `KNOWN_SKILLS` / `KNOWN_TITLE_PATTERNS` approach was deliberately removed in commits 804725c and 3ba1342).
4. **ESCO normalisation code exists but has never run in production.** `_maybe_normalise_skills_via_esco()` (`cv_parser.py:804`) is a real no-op today: it needs both `SEMANTIC_ENABLED=true` AND a prebuilt embedding index at `backend/data/esco/`, and that directory has never been committed or generated (verified: `ls backend/data/esco` → does not exist). Root `CLAUDE.md` rule #28 states this as FACT (verified 2026-08-11): "no ontology is consulted... ESCO is inert scaffolding, never built or shipped." Reviving it means shipping the index artefacts, not flipping a flag.

#### LinkedIn PDF — `backend/src/services/profile/linkedin_parser.py`

This is *not* the old "Request your data" ZIP export — it's the PDF you get from **your own profile → More → Save to PDF**.

- **Detection** (`is_linkedin_pdf`): a 2-of-3 heuristic — (a) a `linkedin.com/in/` URL, (b) ≥3 of 17 known section headings, (c) the `Page N of M` footer pattern.
- **Section split**: deterministic — header (name, headline, industry) and skills (line-per-skill, dedup) are pulled with pdfplumber.
- **LLM structuring**: Experience, Education, Certifications, Languages, Projects, Volunteer, Courses are structured *in parallel* via `asyncio.gather` to one of the three providers.
- **Merge** (`enrich_cv_from_linkedin`): positions become `job_titles`, skills become `linkedin_skills`, everything else is stored on `CVData` as a sidecar field (so the original CV text remains untouched and can be re-merged).

#### GitHub — `backend/src/services/profile/github_enricher.py`

- Fetches up to 30 repos via `GET /users/{user}/repos?sort=pushed` (uses `GITHUB_TOKEN` for higher rate limits if set).
- **Temporal weighting**: repos pushed within the last 365 days get a 3× multiplier when inferring skills.
- **Skill inference**: maps GitHub language byte counts (Python → "Python", TypeScript → "TypeScript", …) and topic tags (`react`, `aws`, `machine-learning`, …) to canonical skill names via two lookup tables (23 languages, 40 topics).
- **Dependency-file parsing**: opens `package.json`, `requirements.txt`, `pyproject.toml`, `Cargo.toml`, `Gemfile`, `go.mod`, `composer.json` from the default branch to extract framework names (React, Django, FastAPI, etc.).
- **Merge** (`enrich_cv_from_github`): `github_languages` (dict with byte counts), `github_topics`, `github_skills_inferred`, `github_frameworks` are written onto `CVData`, dedup'd against existing skills.

#### Preferences (typed form) — `backend/src/services/profile/preferences.py`

Free-text and select fields the user fills in on `/profile`:

- `target_job_titles`, `additional_skills`, `excluded_skills`
- `preferred_locations`, `industries`
- `salary_min` / `salary_max`
- `work_arrangement` (`remote` / `hybrid` / `onsite`)
- `experience_level` (`entry` / `mid` / `senior` / `lead`)
- `negative_keywords` (e.g. "intern", "junior")
- `about_me` (free-form), `github_username`
- `needs_visa` (boolean)
- `preferred_workplace` — DERIVED, not stored: a read-only property over
  `work_arrangement` (2026-08-13). It was a stored copy kept in step by a
  bridge in the profile route; the copy is gone, so the two cannot diverge.

### 3.3 The data model — `backend/src/services/profile/models.py`

Three dataclasses, all serialised to JSON:

- **`CVData`** — what came out of the CV/LinkedIn/GitHub: skills, titles, companies, education, certifications, summary, experience text, career domain, industries, languages, ESCO skill URIs, plus the LinkedIn/GitHub sidecar fields. Has a `to_json_resume()` method for export.
- **`UserPreferences`** — the form fields above.
- **`UserProfile`** — composite of the two; `is_complete` returns True only when both halves are populated.

### 3.4 Storage — `backend/src/services/profile/storage.py`

- **Live tip**: `user_profiles` table (migration `0006`) — one row per user, latest version.
- **History**: `user_profile_versions` table (migration `0007`) — every `save_profile()` appends an immutable snapshot. Retention is 10 per user; `restore_profile_version()` rewrites the tip atomically *and* saves the restore as a new snapshot so the history is never destroyed.
- **Legacy hydration**: if no row exists for `DEFAULT_TENANT_ID` but the old `data/user_profile.json` does, it's auto-imported on first load (non-destructive — the JSON file is left in place).

### 3.5 Search-config generation — `backend/src/services/profile/keyword_generator.py`

This is the bridge to Pillar 2 (search & match engine). `generate_search_config(profile)` turns the merged profile into a `SearchConfig`:

- **Job titles**: preferences first, then CV-extracted, then LinkedIn positions
- **Tiered skills** (`primary` / `secondary` / `tertiary`): no longer a flat list — `tier_skills_by_evidence()` (Batch 1.3a) scores each skill by frequency × source weight (preferences ≫ CV ≫ LinkedIn ≫ GitHub-inferred) and slots them into tiers
- **Relevance keywords**: lowercased words from titles + all skills + LinkedIn industry, stopword-filtered
- **Locations**: UK defaults union prefs union the `work_arrangement` capitalised; top 2 are used to build search queries
- **Search queries**: top 8 titles × 2 locations, capped at 16

When no profile exists, `SearchConfig.from_defaults()` is used instead — that's the hard-coded AI/ML keyword set in `backend/src/core/keywords.py`.

---

## 4. Ring 3 — Delivery (Feed, Channels, Notifications, Pipeline)

Once the search engine has run (Pillar 2), the matched jobs need to land in front of the right user. That's everything in Ring 3.

### 4.1 The user_feed table — the single source of truth

`backend/migrations/0003_user_feed.up.sql`:

```sql
user_feed(id, user_id FK, job_id, score 0-100, bucket, status, notified_at, created_at, updated_at)
UNIQUE(user_id, job_id)
status ∈ {'active','skipped','stale','applied'}
```

This is the **SSOT (single source of truth)** for what each user sees. Both the dashboard query and the notification worker read from this same table, which guarantees the dashboard and the email always show the same jobs. Two specialised partial indexes make the hot queries cheap:

- `idx_feed_dashboard` — used by the dashboard list query (active jobs by bucket and score)
- `idx_feed_notify` — used by the notification worker (active jobs where `notified_at IS NULL`)

### 4.2 FeedService — `backend/src/services/feed.py`

The methods that wrap the table:

| Method | What it does |
| --- | --- |
| `list_for_user(user_id, bucket=None, min_score=None)` | Dashboard read |
| `list_pending_notifications(user_id)` | Worker read |
| `mark_notified(user_id, job_id)` | Set `notified_at = now()` |
| `update_status(user_id, job_id, status)` | active → applied/skipped |
| `cascade_stale(job_id)` | Mark a *job* stale across every user (e.g. posting expired) |
| `upsert_feed_row(user_id, job_id, score, bucket)` | Worker-side write after scoring |

### 4.3 The 3-stage pre-filter — `backend/src/services/prefilter.py`

Before a job ever gets *scored* against a specific user (which is the expensive step), it passes through three cheap gates per blueprint §2 ("99% elimination"):

1. **Location gate** — drop if location is not in the user's preferred locations and not `remote`
2. **Experience gate** — drop if the job's seniority is more than one band away from the user's level
3. **Skill-overlap gate** — drop if zero of the user's primary skills appear in title + description

Only the survivors get the full `JobScorer` treatment (Pillar 2).

### 4.4 Channels — `backend/src/services/channels/`

- **`user_channels` table** (`backend/migrations/0005_user_channels.up.sql`) — per-user channel config. Credentials are encrypted with **Fernet** using `CHANNEL_ENCRYPTION_KEY`. Five `channel_type` values map to Apprise URL schemes:
  - `email` → `resend://{key}:{from}/{to}/` (HTTPS:443). `mailtos://` is only the **fallback**, used when no Resend key is present but `SMTP_EMAIL` + `SMTP_PASSWORD` are — Railway blocks outbound SMTP 25/465/587, so a `mailtos://` channel cannot deliver there. Built by `services/channels/email_url.py:97-142`, never supplied by the user
  - `slack` → `slack://tokenA/tokenB/tokenC`
  - `discord` → `discord://webhook_id/webhook_token`
  - `telegram` → `tgram://bot_token/chat_id`
  - `webhook` → `json://host/path`
- **`backend/src/services/channels/crypto.py`** — `encrypt(plaintext) → bytes` / `decrypt(ciphertext) → str`. Fails closed if `CHANNEL_ENCRYPTION_KEY` is unset.
- **`backend/src/services/channels/dispatcher.py`** — thin wrapper around Apprise. Imports `apprise` *lazily inside the function* (CLAUDE.md rule #11 — Apprise pulls ~30 MB of deps; library code must not pay that cost on import). Tests monkeypatch `apprise.Apprise`. The dispatcher runs each notification through gates before sending: (1) `enabled=0` skip, (2) `match_score < score_threshold` skip, (3+4) `notify_mode` is a **bundling** mode (`daily` / `every_n_hours`) **or** the moment falls inside the quiet-hours window (evaluated with stdlib `zoneinfo` against `users.timezone`) → queue for the digest instead of sending now (`backend/src/services/channels/dispatcher.py:317`). `force=True` bypasses gate 3+4 and is how `send_bundle` delivers already-queued rows.
- **There is only ONE delivery path.** The pre-Batch-2 single-tenant notification code (`backend/src/services/notifications/{base,email_notify,slack_notify,discord_notify}.py`, env-var webhooks) was **deleted** — see the removal note in `ARCHITECTURE.md` / `README.md`. Everything user-facing goes through the per-user `backend/src/services/channels/dispatcher.py`, invoked by ARQ worker tasks under an authenticated `user_id`. The CLI batch run no longer sends its own summary: it writes a markdown report via `generate_markdown_report()` from `backend/src/services/notifications/report_generator.py` (`backend/src/main.py:49`) and enqueues the ordinary per-user `send_notification` task for above-threshold feed rows (`backend/src/main.py:481` `_enqueue_notifications`).
- **API** — `backend/src/api/routes/channels.py`:
  - `GET /api/settings/channels` — list caller's channels
  - `POST /api/settings/channels` — create (encrypts credential server-side)
  - `DELETE /api/settings/channels/{id}` — delete (two-layer ownership check: SELECT in route + dispatcher re-checks `user_id`)
  - `POST /api/settings/channels/{id}/test` — send a "test" message, return ok/error

### 4.5 Notification rules — `backend/migrations/0020_notification_rule_single.up.sql`

Migration `0012` created this table **per channel**. Migration `0020` collapsed it to **one row per user** — the `channel` column and `digest_send_time` are gone, and `notify_mode` gained two bundling modes:

```sql
notification_rules(id, user_id FK, score_threshold=60,
                   notify_mode='instant'|'daily'|'every_n_hours',
                   interval_hours=6, daily_send_time='08:00',
                   quiet_hours_start, quiet_hours_end, last_sent_at, enabled)
UNIQUE(user_id)
```

One rulebook governs **all** of a user's channels at once (CLAUDE.md rule #23), so the user says *"notify me for score ≥ 75, bundled daily at 08:00, nothing between 22:00 and 07:00"* — not one policy per channel. The `users.timezone` column (added by `0012`, default `'UTC'`) is what quiet hours and `daily_send_time` are evaluated against.

- API: `backend/src/api/routes/notification_rules.py` — exactly two endpoints, `GET /api/settings/notification-rule` (returns `null` when unset) and `PUT /api/settings/notification-rule` (upsert, merging unsupplied fields). Both gate on `Depends(require_user)` and key off `user.id`; neither accepts a `user_id` from the request (rule #12).
- A rulebook is **seeded at signup** (`services/notifications/defaults.py`, master switch `NOTIFY_SEED_DEFAULTS`) — before that, nothing in the product ever created a row, so no user could be alerted at all.

### 4.6 Notification ledger — `backend/migrations/0004_notification_ledger.up.sql`

```sql
notification_ledger(id, user_id, job_id, channel, status, sent_at, error_message, retry_count, created_at)
UNIQUE(user_id, job_id, channel)
```

This is the **idempotency table**: the UNIQUE constraint *guarantees* a (user, job, channel) triple can never be sent twice, even under concurrent worker retries. Statuses: `queued` / `sent` / `failed` / `dlq` (dead-letter).

- API: `backend/src/api/routes/notifications.py` — `GET /api/notifications` (paginated, filter by channel/status/job_id/time-range) + `GET /api/notifications/stats` (per-channel sent/failed/queued aggregation).

### 4.7 Digest queue — `backend/migrations/0013_user_notification_digests.up.sql`

When `notify_mode` is a bundling mode (`daily` / `every_n_hours`) — or when an `instant` match lands inside quiet hours — the match is queued in `user_notification_digests` rather than sent immediately. The `notification_tick` ARQ cron runs **every 5 minutes** (`workers/settings.py:233`), asks `_bundle_due()` per enabled rule (using `daily_send_time` / `interval_hours` in the user's timezone), and enqueues `send_bundle` when due. `send_bundle` batches the queued rows into one Apprise call per channel with `force=True`, marks them `sent`, and writes the ledger — flipping a channel's ledger row to `dlq` after `MAX_BUNDLE_RETRIES` failures. `notification_tick` also flushes an `instant` user's queue once quiet hours end, which nothing else would drain.

### 4.8 Worker layer — `backend/src/workers/tasks.py`

Pure async functions (no `arq` import at module top level, per CLAUDE.md rule #11):

- `score_and_ingest(ctx, job_id, users_override=None)` — Pillar 2's `JobScorer` runs here; on success it `upsert_feed_row()`s into every user whose profile matches, then enqueues `send_notification` for any feed row above each user's threshold. The `users_override` kwarg lets the test suite scope to a single user without seeding others.
- `send_notification(ctx, user_id, job_id, urgency='instant')` — dispatches to all of the user's enabled channels via `dispatcher.dispatch()`; one ledger row written per channel.
- `send_bundle(ctx, user_id)` (`tasks.py:971`) — drains queued rows from `user_notification_digests` across **all** the user's channels, batches into one Apprise call per channel, marks rows `sent`. There is no `send_daily_digest`; the per-channel task of that name was removed with the one-rule-per-user collapse.
- `notification_tick(ctx)` (`tasks.py:1243`) — the 5-minute cron that decides which users are due and enqueues `send_bundle`.
- `nightly_ghost_sweep(ctx)` — re-evaluates every non-expired job via `evaluate_job_state()`; transitions confirmed-dead postings to `staleness_state='confirmed_expired'` and `cascade_stale()`s them across every user's feed.
- `enrich_job_task(ctx, job_id)` — idempotent LLM enrichment (skips if a `job_enrichment` row exists). One enrichment per job, not per user — shared catalog (CLAUDE.md rule #10 / #17).
- `idempotency_key(user_id, job_id, channel)` — deterministic SHA1 ledger key.
- `mark_ledger_sent(...)` / `mark_ledger_failed(...)` — write outcomes; the latter increments `retry_count`.

Production runs them under ARQ + Redis (env var `REDIS_URL`); the test suite calls them directly as async functions and never touches Redis. The ARQ enqueue path is `ctx.get('enqueue')` — tests stub it as a list `.append`; prod calls `redis.enqueue_job`.

### 4.9 User actions & pipeline

**Actions** (`backend/src/api/routes/actions.py`) — lightweight per-job verbs:

- `POST /api/jobs/{job_id}/action` with `{"action": "liked"|"applied"|"not_interested", "notes": "..."}` 
- `DELETE /api/jobs/{job_id}/action` — undo
- `GET /api/actions` — list caller's actions
- `GET /api/actions/counts` — `{liked, applied, not_interested}`

Backed by the `user_actions` table (rebuilt with a `user_id` column in migration `0002_multi_tenant`).

**Pipeline** (`backend/src/api/routes/pipeline.py`) — full application lifecycle:

- Stages: `applied` → `outreach` → `interview` → `offer` → `rejected`
- `GET /api/pipeline` (optional `?stage=`), `GET /api/pipeline/counts`, `GET /api/pipeline/reminders` (stalled >7 days)
- `POST /api/pipeline/applications` to create — returns **410 Gone** if the target job has `staleness_state='confirmed_expired'`, so users cannot start an application against a ghost posting
- `POST /api/pipeline/{job_id}/advance` to move stage
- `GET /api/pipeline/{job_id}/timeline` — full stage history
- `PATCH /api/pipeline/{job_id}/notes`

Backed by the `applications` table (also rebuilt in `0002_multi_tenant`) and a sidecar `application_stage_history` table from migration `0014` that records every transition with optional notes. `0014` also added `last_advanced_at`, `interview_dates` (JSON array of dates), and `notes_history` (JSON array of `{note, timestamp}`) columns on `applications`.

---

## 5. Ring 4 — Frontend (Next.js 16 dashboard)

> Next.js **16**, not 14 or 15. App Router has breaking changes: `params` is `Promise<{...}>`, `searchParams` is async, `"use client"` on a `page.tsx` disables `generateMetadata`. The frontend was deliberately written against the 16 idioms — `frontend/AGENTS.md` warns against assuming the older patterns.

### 5.1 URL → page map

| URL | File | Server/Client | What the user sees |
| --- | --- | --- | --- |
| `/` | `frontend/src/app/page.tsx` | Client | Marketing landing page — "41 sources, 8D scoring, one dashboard". CTAs link to `/profile` and `/dashboard`. |
| `/(auth)/login` | `frontend/src/app/(auth)/login/page.tsx` | Client | Email + password form, `?next` honoured via `safeNext()` |
| `/(auth)/register` | `frontend/src/app/(auth)/register/page.tsx` | Client | Same shape, redirects to `/profile` on success |
| `/dashboard` | `frontend/src/app/dashboard/page.tsx` | Client | Job browser. Time-bucket pills (24h / 48h / 3d / 5d / 7d / all), min-score slider, source dropdown, visa toggle, async "Run search" button polling `getSearchStatus()`. Renders `<JobList>` of `<JobCard>`s. |
| `/jobs/[id]` | `frontend/src/app/jobs/[id]/page.tsx` | Server + Client | Server shell fetches via `getJob(id)` with 5-min revalidate, emits JobPosting JSON-LD for SEO, then renders `<JobDetailClient>` for interactive actions. Uses the Next.js 16 `params: Promise<{id}>` pattern. |
| `/profile` | `frontend/src/app/profile/page.tsx` | Client | The single biggest page — CVUpload, LinkedIn upload, GitHub username input, PreferencesForm, skill-tier display, version-history drawer with restore (**no ESCO badges** — see below). Profile-completeness % is shown at the top (40% CV / 15% job titles / 15% skills / 15% prefs / 7.5% LinkedIn / 7.5% GitHub). |
| `/pipeline` | `frontend/src/app/pipeline/page.tsx` | Client | 5-column Kanban; drag-and-drop between stages calls `advancePipelineStage`. Reminder banner for >7-day stalled applications. |
| `/channels` (linked from Navbar) | `frontend/src/app/channels/page.tsx` | Client | List / add / delete / test channels, plus the Telegram deep-link connect flow (`connectTelegram` / `pollTelegram`) and the Slack/Discord OAuth hand-off (`channelConnectUrl`) |

> **This table is a tour of the main pages, not an inventory.** There are **22** `page.tsx` files under `frontend/src/app/`; the rest are the auth long-tail (`forgot-password`, `reset-password`, `verify-email`, `auth/magic`), settings (`/settings`, `/settings/account`, `/settings/notifications`), `/notifications`, `/jobs`, `/admin/sources`, the legal pages (`/privacy`, `/terms`, `/contact`) and `/sentry-test`. Count them, don't trust this list.
>
> **No ESCO badges on `/profile`.** The page does carry an "ESCO mappings" block (`profile/page.tsx:546-558`), but it renders only when `profile.skill_esco` is non-empty, and that map is filled only when `SEMANTIC_ENABLED` is on **and** the ESCO index artefacts are present on disk (`backend/src/services/profile/cv_parser.py:804-831` returns the identity transform otherwise). Hard rule #28: those artefacts are inert scaffolding, never built and never shipped. So in production the block never draws — do not describe it as something a user sees.

### 5.2 API client — `frontend/src/lib/api.ts`

A thin fetch wrapper (`request<T>()` with an `ApiError` class). Every call uses `credentials: 'include'` so the HttpOnly session cookie is sent automatically. Functions exposed:

- **Auth**: `register`, `login`, `logout`, `me`, `changePassword`, `changeEmail`, `deleteAccount`
- **Jobs**: `getJobs(filters)`, `getJob(id)`, `exportJobsCsv()`, `getJobDuplicates(id)`
- **Actions**: `setJobAction`, `removeJobAction`, `getActions`, `getActionCounts`
- **Profile**: `getProfile`, `uploadProfile(cv, prefs)`, `uploadLinkedin(file)`, `uploadGithub(username)`, `getProfileVersions`, `restoreProfileVersion`, `getJsonResume`, `getProfileVersionDiff`
- **Search**: `startSearch`, `getSearchStatus`
- **Pipeline**: `getPipelineApplications`, `createPipelineApplication`, `advancePipelineStage`, `getPipelineReminders`, `getPipelineCounts`, `getApplicationTimeline`, `updateApplicationNotes`
- **Channels**: `listChannels`, `createChannel`, `deleteChannel`, `testChannel`, `getProviders`, `channelConnectUrl`, `connectTelegram`, `pollTelegram`
- **Notifications**: `getNotificationRule` / `saveNotificationRule` (**singular — there is no create/update/delete pair**; rule #23 gives each user exactly ONE rulebook, so the client mirrors the two endpoints `GET`/`PUT /api/settings/notification-rule` — `api.ts:542,551`), `getNotificationLedger`, `getNotificationStats`
- **Meta**: `getSources`, `getHealth`, `getStatus`, `getRecentRuns`, `getSourceHealth`

### 5.3 Type system — `frontend/src/lib/types.ts`

**Generated, not hand-written.** `types.ts:11` imports `components` from `./api-types`, and every backend-shaped type is a one-line alias off it (`export type JobResponse = Schemas["JobResponse"];`). `frontend/src/lib/api-types.ts` and `frontend/openapi.json` are both produced offline by `scripts/gen-api-types.sh`, which calls `app.openapi()` and pipes it through `openapi-typescript` — run it after any change to `backend/src/api/models.py` (or `npm run gen:types` from `frontend/`). The commit gate regenerates both files and fails on any diff (`scripts/agent-gate.sh:231-239`), so the hand-sync burden this section used to describe is gone. Only genuinely frontend-only shapes are still written by hand (`JobFilters`, `DuplicateJobsResponse`, `DuplicateJobSummary`, `ProfileVersionDiff`, `PreferencesRequest`, `SkillProvenance`, `SkillTiers`, `TailorDocKind`), plus two that intentionally narrow a backend `str` to a literal union via `Omit<…> & {…}` (`TailoredDocOut`, `TailorBundle`).

Top-level types include `JobResponse`, `ProfileResponse`, `NotificationRule`, `NotificationLedgerEntry`, `PipelineApplication`, `TimelineEntry`.

> **The 8D breakdown on `JobResponse` is not the list this doc used to print.** The eight dimensions the engine actually computes are `role`, `skill`, `location_score`, `recency`, `seniority_score`, `salary_score`, `visa_score`, `workplace_score` (`backend/src/api/models.py:80-87`). `experience`, `credentials` and `semantic` are also on the model, but they are **legacy dead columns from migration `0011` that the engine never produced** (`models.py:94-99`) — a UI that draws them draws zeros forever. `penalty` is not a dimension either. Never sum the eight to get the total: the raw max is 130 clamped to `[0, 100]` (rule #27) and the −30 negative-title penalty lands on no dimension, so `match_score` is the only truth for the total.

### 5.4 Component organisation

```
frontend/src/components/   (8 folders)
├── ui/         — shadcn primitives (badge, button, card, dialog, input, label, select, separator, sheet, skeleton, slider, tabs, textarea, tooltip, empty-state)
├── jobs/       — JobCard, JobList, FilterPanel, ScoreRadar, ScoreCounter, TimeBuckets, DedupGroupViewer, ApplyButton, SearchingFor
├── profile/    — CVUpload, CVViewer, PreferencesForm, VersionHistoryDrawer, VersionDiffDrawer, JsonResumeExportButton, ClearButton
├── pipeline/   — KanbanBoard, NotesEditor, PipelineFilterPanel
├── tailor/     — TailorButton, TailorPanel, TailorSection
├── consent/    — ConsentBanner
├── providers/  — QueryProvider, PostHogProviderWrapper
└── layout/     — Navbar, Footer, FloatingIcons, AuthProvider, ThemeProvider
```

State is cached with **TanStack Query** keyed by `queryKeys.jobList(filters)` etc., which is what enables the optimistic UI on the like/apply buttons.

---

## Environment variables — every var the User pillar reads

Consolidated so you can `grep` once and see them all. Defaults come from `backend/src/core/settings.py`; the two secrets are read at call time by `api/auth_deps.py` and `services/channels/crypto.py` instead (`services/auth/sessions.py` reads no environment at all — it takes the secret as an argument).

| Var | Required | Default | What changes when you flip it |
| --- | --- | --- | --- |
| `SESSION_SECRET` | **yes, everywhere** | **none — there is no dev fallback** | HMAC secret for session cookies. Rotating invalidates every active session. Fail-closed at CALL time: `auth_deps._secret()` raises `RuntimeError` whenever it is unset, in dev as well as prod (`backend/src/api/auth_deps.py:36-41`). `settings.py` deliberately does **not** bind it as a module constant — the old `os.getenv(..., "")` default read as "an empty secret is a valid state" and was removed in M10 (`core/settings.py:362-376`). Also boot-checked in prod, below. |
| `CHANNEL_ENCRYPTION_KEY` | **yes, everywhere** | **none — there is no dev fallback** | Fernet key for `user_channels.credential_encrypted`. Same fail-closed shape: `channels/crypto.py:19-33` raises if unset. **Do not rotate without a re-encryption migration** — existing channels become undecryptable. |
| ~~`JOB360_ENV`~~ | — | — | **DEAD — read by nothing under `backend/src/`.** The only trace is a comment at `api/routes/auth.py:130`. The session cookie's `Secure` flag now gates on the same signal as HSTS, Sentry and CORS: `middleware._is_production()`, i.e. `APP_ENV=production` **or** `RAILWAY_ENVIRONMENT` set (`api/middleware.py:34-38`, used by `_set_session_cookie` at `auth.py:125-132`). Setting `JOB360_ENV=prod` on its own changes nothing, and on Railway it was never set at all. |
| `APP_ENV` / `RAILWAY_ENVIRONMENT` | no (Railway sets the latter) | (unset) | The real production switch, and the two halves are **not** symmetric: `APP_ENV` counts only when it equals `production` (case-insensitive), whereas **any non-empty `RAILWAY_ENVIRONMENT` counts, whatever its value** — `staging` and `preview` included. Either half satisfying that makes `_is_production()` true → `Secure` session cookie + HSTS, and makes `validate_required_env()` refuse to boot when `SESSION_SECRET`, `CHANNEL_ENCRYPTION_KEY` or `DATABASE_URL` is empty (`api/middleware.py:34-38`, `core/settings.py:384-400`). |
| `FRONTEND_ORIGIN` | no | `http://localhost:3000` | CORS allow-list (comma-separated for multiple). |
| `REDIS_URL` | only for ARQ worker | `redis://localhost:6379` | Worker broker; not used by API or CLI. |
| `OPENAI_API_KEY` (+ `OPENAI_MODEL`, default `gpt-4o-mini`) | no | (unset) | **First-choice** LLM for CV parsing — the chain is OpenAI → Gemini → Groq → Cerebras (`services/profile/llm_provider.py:329-334`). A lowercase `openai_api_key` is accepted too (`core/settings.py:60`). |
| `GEMINI_API_KEY` | no | (unset) | Second-choice LLM. Unset → falls through to Groq. |
| `GROQ_API_KEY` | no | (unset) | Third-choice LLM. Unset → falls to Cerebras. |
| `CEREBRAS_API_KEY` | no | (unset) | Last-choice LLM. **All FOUR unset** → `require_llm_key()` raises `LLMKeyMissing` before any provider is tried (`llm_provider.py:264-283`); it subclasses `LLMError(RuntimeError)`, which is what the 502 in the failure table below is made of. |
| `GITHUB_TOKEN` | no | (unset) | Bumps GitHub API quota from 60 → 5000 req/hr. Anonymous still works for public repos. |
| `LOG_LEVEL` | no | `INFO` | Python logging level. `DEBUG` exposes request bodies + profile parsing internals. |
| `RESEND_API_KEY` | recommended in prod | (unset) | The platform's mail transport. Used for BOTH system email (magic links, `auth/email_sender.py`) and the per-user email alert channel, which is built as `resend://` (`services/channels/email_url.py:51-61`). A key sitting in `SMTP_PASSWORD` is recognised by its `re_` prefix and honoured too. |
| `SMTP_EMAIL` / `SMTP_PASSWORD` / `SMTP_FROM` / `SMTP_HOST` / `SMTP_PORT` | no | — | **Live, not legacy.** These are the PLATFORM's mail credentials. `SMTP_FROM`→`SMTP_EMAIL`→`onboarding@resend.dev` is the from-address (`email_url.py:83-94`), and with no Resend key the email channel falls back to `mailtos://` built from `SMTP_EMAIL`/`SMTP_PASSWORD`/`SMTP_HOST`/`SMTP_PORT` (`email_url.py:121-140`). A user never supplies any of these. |
| `NOTIFY_EMAIL` / `SLACK_WEBHOOK_URL` / `DISCORD_WEBHOOK_URL` | no | — | **DEAD at application runtime.** Declared at `core/settings.py:78,81,82` and read by nothing under `backend/src/`, `backend/tests/` or `scripts/`. They belonged to the pre-Batch-2 single-tenant notifier that was deleted, so setting them sends no user a thing. The NAMES do survive outside the app: `cron_setup.sh:41-42` greps `.env` for them to print a "configured" line, `setup.sh:56`/`setup.bat:69` mention `NOTIFY_EMAIL`, and CI reads a `SLACK_WEBHOOK_URL` **repo secret** for its own alerts (`.github/workflows/post-merge-watch.yml:413-423`) — that is the harness talking to the owner, not the product talking to a user. |

---

## Failure modes — when things go wrong

A non-exhaustive table of failures an operator or agent will actually see, where they surface, and what to do.

| Symptom | Root cause | Where it surfaces | Fix |
| --- | --- | --- | --- |
| API refuses to start: `RuntimeError` naming `SESSION_SECRET` / `CHANNEL_ENCRYPTION_KEY` / `DATABASE_URL` | One of those three empty, **and** `validate_required_env()`'s gate is open. That gate is `APP_ENV=production` **or a non-empty `RAILWAY_ENVIRONMENT` of any value** (`core/settings.py:393-395`) — so a Railway `staging` or preview environment boot-checks exactly like production does; "production" here means the gate, not the deploy's name | FastAPI `lifespan` → `validate_required_env()` (`api/main.py:114`, `core/settings.py:387-400`) | Set the env var; fail-closed by design |
| Dev/CLI instead: server starts fine, then a 500 on the first request that needs a session or a channel credential | `SESSION_SECRET` / `CHANNEL_ENCRYPTION_KEY` empty with that gate shut (no `APP_ENV=production`, no `RAILWAY_ENVIRONMENT`), and the CLI never builds the FastAPI app, so nothing boot-checks them either way | `auth_deps._secret()` (`:36-41`) / `channels/crypto._fernet()` (`:19-33`) raise at CALL time | Set the env var. Do not read the boot row above as "it always fails at startup" — with the gate shut it fails later, further from the cause |
| Not this row: `DATABASE_URL` | It has a working dev default (`postgresql://job360:job360dev@localhost:5433/job360`, `core/settings.py:25-27`), so outside the gate it is never *missing* — it is merely pointed somewhere | A bad or unreachable DSN surfaces as a connection error from `pg`/`pgsync`, never from `_secret()` or `_fernet()` — those two only ever cover the session and channel secrets | Check the DSN and that Postgres is up (`docker-compose.dev.yml`, port 5433) |
| Login fails on a correct password | `password_hash` in DB corrupted (typically from direct SQL writes) | `passwords.verify_password()` returns False | Re-hash via `setup-profile` route or DB UPDATE |
| 401 on every frontend `/api/*` call | Cookie missing or expired or domain mismatch | Browser devtools → Cookies | `credentials: 'include'` is required (already in `api.ts`); confirm cookie domain matches `FRONTEND_ORIGIN` |
| CV upload returns 502; profile fields empty | All **four** LLM providers (OpenAI → Gemini → Groq → Cerebras) exhausted / returned malformed JSON twice | `parse_cv_async` raises — `LLMKeyMissing` if no key is set at all, else `LLMAllProvidersFailed` / `LLMRateLimited`; all three subclass `LLMError(RuntimeError)` (`llm_provider.py:48-71`), and the route surfaces 502 | Check provider env vars; tail logs filtered by `cv_parser`; try a smaller/cleaner PDF |
| LinkedIn PDF treated as a regular CV | 2-of-3 detection heuristic failed | `is_linkedin_pdf` returns False → CV pipeline runs → fields land in wrong slots | Inspect PDF for: `linkedin.com/in/` URL, ≥3 known section headings, "Page N of M" footer |
| GitHub enrichment slow / 403 errors | Anonymous rate limit (60 req/hr) hit | `github_enricher` logs 403 rate limited | Set `GITHUB_TOKEN` (no scopes needed for public repos) |
| Notification rule fires, no email arrives | The platform has no mail transport, or the built URL is malformed. Note the user never types this URL — the backend builds it from `RESEND_API_KEY` / `SMTP_*` | `notification_ledger.status='failed'`, `error_message` populated. If the channel could not even be created, `POST /api/settings/channels` returned 503 | `GET /api/notifications?status=failed` to see the error. Check `RESEND_API_KEY` first: on Railway `mailtos://` cannot deliver (SMTP ports blocked), so a channel built on the SMTP fallback times out and is recorded as failed |
| Digest queue fills, never drains | ARQ worker not running, or digest_send_time evaluated in wrong zone | `user_notification_digests.sent=0` count growing | Confirm `arq` process up; verify `users.timezone` value; quiet-hours and digest_send_time both read this column |
| `POST /api/pipeline/applications` returns 410 | Job has `staleness_state='confirmed_expired'` — guard rail | UI shows "Job no longer available" | If the job is actually live: `UPDATE jobs SET staleness_state='active' WHERE id=?` |
| Pipeline UI shows wrong stage after advance | `applications.stage` ≠ latest `application_stage_history.to_stage` (only possible via direct SQL) | `/pipeline` shows stale stage | Re-derive: `SELECT to_stage FROM application_stage_history WHERE job_id=? AND user_id=? ORDER BY transitioned_at DESC LIMIT 1` and UPDATE applications |
| Frontend page renders blank after deploy | Next.js 16 `params` not `await`ed (rule #22 — training-data trap) | Component renders without data | Convert to `params: Promise<{ id: string }>` and `await params` |
| Profile completeness stuck at 0% after upload | Either no `user_profiles` row (silent save failure) or LLM returned empty schema | Inspect: `SELECT * FROM user_profiles WHERE user_id=?` | If no row: tail logs for the save error. If row exists with empty fields: LLM produced empty extraction — retry with a clearer CV or different provider |
| Soft-deleted user reappears in some list | Query forgot `WHERE deleted_at IS NULL` | Any user-listing surface | Fix the offending query; auth path already excludes them in `_current_user_from_cookie` |
| User changes email — still logged in | Expected: route invalidates session, but the active tab still holds the old cookie until next request | UI continues briefly | The next `/api/auth/me` will 401 and bounce to login — no fix needed |

For operational queries (inspect a stuck queue, look up a session cookie, force-rebuild a profile), see [`runbook.md`](./runbook.md). For unfamiliar terminology, see [`glossary.md`](./glossary.md).

---

## 6. Current status — what works, what's incomplete

Legend: ✅ done & wired · 🟡 partial · ❌ planned but not built · ⚠️ known gap

### 6.1 Ring 1 — Identity

| Surface | Status | Notes |
| --- | --- | --- |
| Email + password auth (Argon2id) | ✅ | `passwords.py`, hardened against malformed hashes |
| Signed-cookie sessions (HMAC, 30 d) | ✅ | `sessions.py`, revoke on logout |
| `/api/auth/{register,login,logout,me}` | ✅ | `routes/auth.py` |
| Change password / change email | ✅ | session invalidation on email change |
| Soft-delete account | ✅ | `deleted_at` is honoured by `_current_user_from_cookie` |
| Session timeout extension on activity | 🟡 | `last_seen` is updated but absolute TTL is still 30 d — no rolling renewal |
| Rate-limit / brute-force lockout | ❌ | not implemented (`test_api_security.py` only covers concurrent-search throttle) |
| Email verification | 🟡 | implemented but **not enforced at login** — endpoints `/verify-email/request,confirm` (migration 0016) + frontend `/verify-email` page; `users.email_verified_at` is set but the login path does not require it |
| Password reset (forgot password) | ✅ | implemented — `POST /api/auth/password-reset/request` → 204 + `/confirm`; token SHA256-hashed, 1 h TTL, revokes all sessions on reset (migration 0015). Email send is SMTP-conditional |
| MFA / TOTP | ❌ | not implemented |
| OAuth (Google / GitHub) | ❌ | not implemented |
| `DEFAULT_TENANT_ID` placeholder user | ✅ | `core/tenancy.py`, backfilled by `0002_multi_tenant` |

### 6.2 Ring 2 — Profile

| Surface | Status | Notes |
| --- | --- | --- |
| CV upload (PDF/DOCX) | ✅ | `cv_parser.py` with `pdfplumber` + `python-docx` |
| LLM-only skill/title extraction | ✅ | regex `KNOWN_SKILLS` removed in 3ba1342 |
| LLM provider fallback (OpenAI → Gemini → Groq → Cerebras) | ✅ | `llm_provider.py:329-334` |
| LinkedIn "Save to PDF" import | ✅ | `linkedin_parser.py`, 2-of-3 detection heuristic |
| GitHub enrichment with temporal weighting | ✅ | `github_enricher.py` — 3× weight for repos pushed in last year |
| Dependency-file framework inference | ✅ | 7 file types parsed (package.json, requirements.txt, …) |
| ESCO skill normalisation | ❌ | code path exists but the embedding index it needs (`backend/data/esco/`) was never built or shipped — inert scaffolding, not a flag flip. Root `CLAUDE.md` rule #28 (FACT, verified 2026-08-11). |
| Multi-tenant SQLite storage | ✅ | `user_profiles` table (migration `0006`) |
| Version history + restore (last 10) | ✅ | `user_profile_versions` (`0007`); restore is atomic and preserves history |
| Legacy `data/user_profile.json` hydration | ✅ | non-destructive, runs on first DB read |
| Evidence-based skill tiering | ✅ | `tier_skills_by_evidence()` — frequency × source weight |
| JSON Resume export | ✅ | `getJsonResume()` API + frontend button |
| Profile completeness % on dashboard | ✅ | calculated client-side in `/profile/page.tsx` |
| Profile import from JSON Resume | ❌ | export only |
| Skill diff visualisation between versions | ✅ | `VersionDiffDrawer` component + `getProfileVersionDiff(v1, v2)` API |

### 6.3 Ring 3 — Delivery

| Surface | Status | Notes |
| --- | --- | --- |
| `user_feed` SSOT table | ✅ | migration `0003` |
| 3-stage pre-filter (location → experience → skill) | ✅ | `prefilter.py` |
| Per-user job actions (like/apply/dismiss) | ✅ | `routes/actions.py` |
| Pipeline (5 stages, history, reminders) | ✅ | `routes/pipeline.py` + migration `0014` (history, interview dates, notes log) |
| `user_channels` table + Fernet crypto | ✅ | migration `0005`, `crypto.py` |
| 5 channel types (email/slack/discord/telegram/webhook) via Apprise | ✅ | `dispatcher.py`, lazy import |
| Channel CRUD + test-send endpoint | ✅ | `routes/channels.py`, two-layer ownership check |
| `notification_rules` — ONE row per user (threshold, mode, quiet hours, daily send time, interval) | ✅ | migration `0012`, collapsed to `UNIQUE(user_id)` by `0020` |
| Notification rule endpoints (`GET` / `PUT` on `/api/settings/notification-rule`) | ✅ | `routes/notification_rules.py` |
| `notification_ledger` idempotency table | ✅ | migration `0004` — `UNIQUE(user_id, job_id, channel)` |
| Ledger pagination + filters + per-channel stats | ✅ | `routes/notifications.py` |
| Digest queue (`user_notification_digests`) | ✅ | migration `0013` |
| ARQ + Redis worker for production | ✅ | `tasks.py` are async-pure; `workers/settings.py` is wired and `backend/railway.worker.json` deploys it as its own Railway service (`arq src.workers.settings.WorkerSettings`) |
| Per-user timezone | ✅ | `users.timezone` column added in `0012`, default `'UTC'` |
| Notification dry-run / preview before save | ❌ | only post-save `/test` endpoint exists |
| Push notifications (FCM/APN) | ❌ | not in the channel list |

### 6.4 Ring 4 — Frontend

| Surface | Status | Notes |
| --- | --- | --- |
| Next.js 16 App Router | ✅ | server/client split correct |
| Landing page + marketing copy | ✅ | `app/page.tsx` |
| Login / Register pages with `?next` redirect guard | ✅ | `safeNext()` against open-redirect |
| Dashboard with time buckets, filters, async search | ✅ | TanStack Query caching |
| Job detail page with JSON-LD SEO + 5-min revalidate | ✅ | `app/jobs/[id]/page.tsx` |
| Profile editor (CV, LinkedIn, GitHub, prefs, version history) | ✅ | `app/profile/page.tsx` |
| Pipeline Kanban with drag-and-drop | ✅ | `KanbanBoard` |
| Channel management UI | ✅ | `listChannels` / `createChannel` / `testChannel` wired |
| Notification rules UI | ✅ | `getNotificationRule` / `saveNotificationRule` in `frontend/src/lib/api.ts` (there is no separate `notification_rules.ts`) |
| Notification ledger UI page | ✅ | API ready (`getNotificationLedger`) — frontend route is wired (Step 2 S4) |
| Theme toggle (dark/light) | ✅ | in `Navbar.tsx` |
| Mobile responsive | ✅ | Tailwind v4 + mobile Navbar menu |
| Codegen for frontend types from backend Pydantic | ❌ | hand-maintained — known sync burden |
| TODO / FIXME comments in `src/` | none found | clean codebase |

---

## 7. Quick reference — every file in the User pillar

```
backend/
├── migrations/
│   ├── 0001_auth.up.sql                       — users + sessions
│   ├── 0002_multi_tenant.up.sql               — user_id on actions + applications
│   ├── 0003_user_feed.up.sql                  — SSOT per-user feed
│   ├── 0004_notification_ledger.up.sql        — idempotent ledger
│   ├── 0005_user_channels.up.sql              — Fernet-encrypted channels
│   ├── 0006_user_profiles.up.sql              — multi-tenant profile storage
│   ├── 0007_user_profile_versions.up.sql      — 10-version history
│   ├── 0012_notification_rules.up.sql         — notification preferences + users.timezone
│   ├── 0013_user_notification_digests.up.sql  — digest queue
│   ├── 0014_application_history.up.sql        — pipeline stage history + interview dates
│   └── 0020_notification_rule_single.up.sql   — collapse to ONE rule row per user
├── src/
│   ├── api/
│   │   ├── auth_deps.py                       — require_user / optional_user / CurrentUser
│   │   ├── main.py                            — lifespan + CORS allow-list
│   │   └── routes/
│   │       ├── auth.py                        — register / login / logout / me / password / email / delete
│   │       ├── profile.py                     — get / upload / linkedin / github / versions / restore / jsonresume
│   │       ├── jobs.py                        — list / get / export
│   │       ├── actions.py                     — like / apply / not_interested
│   │       ├── pipeline.py                    — 5-stage Kanban API
│   │       ├── channels.py                    — channel CRUD + test
│   │       ├── notification_rules.py          — single per-user rule (GET / PUT)
│   │       └── notifications.py               — ledger pagination + stats
│   ├── core/
│   │   └── tenancy.py                         — DEFAULT_TENANT_ID
│   ├── services/
│   │   ├── auth/
│   │   │   ├── passwords.py                   — Argon2id
│   │   │   └── sessions.py                    — HMAC-signed cookies
│   │   ├── channels/
│   │   │   ├── crypto.py                      — Fernet
│   │   │   └── dispatcher.py                  — Apprise (lazy)
│   │   ├── feed.py                            — FeedService
│   │   ├── prefilter.py                       — 3-stage cascade
│   │   └── profile/
│   │       ├── models.py                      — CVData / UserPreferences / UserProfile / SearchConfig
│   │       ├── cv_parser.py                   — pdfplumber/python-docx → LLM
│   │       ├── llm_provider.py                — OpenAI (PRIMARY) → Gemini → Groq → Cerebras
│   │       ├── linkedin_parser.py             — LinkedIn PDF parsing
│   │       ├── github_enricher.py             — GitHub API + temporal weighting
│   │       ├── preferences.py                 — form validation + merging
│   │       ├── storage.py                     — Postgres (sync `pgsync`) + legacy JSON hydration
│   │       └── keyword_generator.py           — profile → SearchConfig
│   └── workers/
│       └── tasks.py                           — score_and_ingest, send_notification, notification_tick, send_bundle, mark_ledger_sent/failed
└── tests/                                     — auth (4 files), profile (3 files), feed/channels/notifications (8+ files)
frontend/
├── src/
│   ├── app/
│   │   ├── page.tsx                           — landing
│   │   ├── (auth)/login/page.tsx
│   │   ├── (auth)/register/page.tsx
│   │   ├── dashboard/page.tsx
│   │   ├── jobs/[id]/page.tsx                 — server + client split
│   │   ├── profile/page.tsx
│   │   └── pipeline/page.tsx
│   ├── components/
│   │   ├── jobs/  profile/  pipeline/  layout/  ui/
│   └── lib/
│       ├── api.ts                             — fetch client + all endpoints
│       └── types.ts                           — hand-maintained TS mirroring Pydantic
```

---

## 8. What this pillar does *not* cover

For completeness — these belong in the other two pillars and you won't find them here:

- **How a job actually gets scored** — that's `JobScorer` in `src/services/skill_matcher.py` and the 8-dimension scoring stack. → see `02-search-and-match-engine.md` (next document).
- **Where the 41 sources come from** — that's `src/sources/**`, `SOURCE_REGISTRY`, the tiered scheduler, circuit breakers. → see `03-job-providers.md`.
- **The shared `jobs` catalog table itself** — Pillar 3 (providers) writes it, Pillar 1 (this doc) reads it via `user_feed`.

---

*Last updated 2026-08-24. Backend suite: 218 `test_*.py` files (filesystem count, verified).
The collected-test count and the pass/fail count were **NOT** verified in this session — no
local Postgres was reachable (the suite needs `docker-compose.dev.yml` up on port 5433) — so
neither is recorded here. Measure them, never quote them:
`cd backend && python -m pytest --collect-only -q -p no:randomly | tail -1`. Do not carry the
old "600p/0f/3s" figure forward either; it predates this update by ~2.5 months.*
