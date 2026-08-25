# WIRING.md — every place the product forgets

**What this is:** every hop in the Job360 journey where two parts should talk and don't.
Each item is a checkbox. We fix them one by one and tick them off.

**How each one gets proved:** [`wiring_verification.md`](./wiring_verification.md) — the
five rungs, the per-item browser walkthroughs, the drills, and the coverage bounds.
An item is not done until it climbs all five rungs.

**Measured against:** `origin/main` (production). Two multi-agent sweeps, 26 agents,
2.2M tokens, 2026-08-25. Every claim has a `file:line` for what exists and the exact
search that found nothing.

**Honesty note — read this before trusting the list.** Both sweeps used adversarial
refuters whose job was to kill false findings. They killed **0 of 37** outright (they
did downgrade 3 to PARTIAL with real corrections). A 0% kill rate twice means: treat
these as high-confidence, **not** proven-by-fire. Re-check before building anything big.

**Not audited:** security, auth internals beyond the magic-link path, source fetching,
scoring maths, billing (none exists).

---

## The one-line summary

The product is a good **filing cabinet** and not yet a **loop**.

Everything before "Apply" is well built. The moment a user applies, the product goes
silent about the thing he now cares about most. The notification engine has **never**
read the `applications` table — every message it can send is about a job he has *not*
applied to.

---

## FIX ORDER

Work top to bottom. Each block is independently shippable.

**8 PRs. One per block.** Not one big PR: `main` is production, every merge ships, and a
30-item diff is unreviewable — if something breaks you cannot tell which item did it.

| PR | Block | Items | Status | Why this order |
|---|---|---|---|---|
| 1 | **The door** | W-01, W-03 | ⚠️ **code committed on branch, NOT verified, NOT merged** `1b89491` | Own email dead-ended at our own front door; new users hit a wall on minute one. |
| 2 | **Close the loop** | W-19, W-20 | ready | One cron + one template turns the filing cabinet into a loop. Biggest value, cheapest fix. |
| 3 | **Fix the default email** | W-17, W-18 | ready | The default mode sends the worst message the system can make, and links away from us. One change closes both. |
| 4 | **Don't lose the CV he sent** | W-08, W-10 | ready | Data is being destroyed today. Every day we wait, more is gone. |
| 5 | **Pipeline card truth** | W-05, W-15, W-16 (deadline half) | tests already written | Cards lie about dead jobs, drop deadlines, and the Applied filter always returns zero. |
| 6 | **Close the silent holes** | W-06, W-12, W-28, W-29 | ready | One-liners: an untracked exit, a stale-doc warning, a feed that shows old scores as fresh. |
| 7 | **Launch gates** | W-23, W-27 | ready | No unsubscribe = cannot email the public. No analytics = the launch teaches nothing. |
| 8 | **Delete sweep** | D-01…D-05 | ready | Deleting dead code is a real fix. Fan out — 5 unrelated files. |

> **STATUS WORDS MEAN SPECIFIC THINGS HERE. Corrected 2026-08-25.**
> An earlier version of this table said PR 1 was "✅ shipped". That was false and it
> was mine. Nothing has been shipped, and no pull request exists (`gh pr list` → `[]`).
> The only true statement is: *code is committed on the branch `feat/wire-pipeline-page`*.
>
> | Word | What it must mean before it is written here |
> |---|---|
> | code on branch | committed + pushed. Proves nothing about behaviour. |
> | verified | all five rungs of `wiring_verification.md` passed, rung 4 (real browser, screenshots) included |
> | merged | merged to `main` — which auto-deploys to real users |
> | shipped | merged AND confirmed live in production with a named instrument |
>
> Green tests are rung 2. They are not permission to write any of the other three words.

**Blocked on a decision, NOT scheduled:** W-04 (D-A), W-14 (D-B, D-C), W-16's interview half
(D-D), W-24 / W-25 / W-26 (D-E), W-05's filter-vs-delete call (D-F), W-20's tone (D-G).

**Parked as LATER:** W-02, W-07, W-09, W-11, W-13, W-21, W-22, W-30.

> The IDs in this table are the authority. An earlier draft of it named four wrong items
> (W-11 for W-08, W-22 for W-23, W-13 for W-05/W-16, W-25 for W-27) — three of which would
> have sent us to build a LATER item instead of the real one. Cross-check any ID here
> against its own `### W-xx` section before starting work.

---

# STEP 1 — The door (magic-link login)

This leg is genuinely good: no email enumeration, the confirm-button beats inbox
scanners, first login seeds notification rules. Three breaks.

### [ ] W-01 — The magic link always dumps him on `/dashboard`  ⚠️ CODE ON BRANCH `1b89491` — rungs 4+5 NOT done
**Severity:** breaks the loop (it breaks your *own* email leg)
**What happens:** the emailed link carries only `?token=`. There is no `?next=`. The
password login form reads `next` and honours it; the magic form — the **default** — never
does. So when your own digest email links him to `/jobs/123` and his session expired, he
bounces to `/login`, signs in by magic link, and lands on the dashboard. The job is gone.
**Proof exists:** `services/auth/magic_link.py:66-67` builds `f"{frontend_origin}/auth/magic?token={safe_token}"`. `login/page.tsx` PasswordForm does `router.push(safeNext(next))`; MagicLinkForm never reads searchParams.
**Proof missing:** `git grep -n "next" origin/main -- frontend/src/app/auth/magic/page.tsx backend/src/services/auth/magic_link.py backend/src/api/routes/auth.py` → nothing in all three.
**Smallest fix:** carry `next` from `/login` → request → emailed link → consume redirect. Copy the password path's `safeNext()`.

**Implementation notes (verified by hand 2026-08-25):**
- The guard already exists and is already unit-tested: `safeNext()` at
  `frontend/src/app/(auth)/login/page.tsx:22` — rejects anything not starting with `/`,
  and rejects protocol-relative `//evil.com`. Exported, with tests at
  `login/__tests__/login-redirect.test.tsx:36`. **Reuse it, do not write a second one.**
- `MagicLinkForm` (`login/page.tsx:46`) takes only `onUsePassword` and never reads
  `useSearchParams` — that is the exact line where the note is dropped.
- **SECURITY — the backend needs its own validation, not just the frontend.** The emailed
  link is built server-side (`magic_link.py:66-67`), so `next` becomes a server input.
  Validate it in Python at the request route with the same rule (must start with `/`,
  must not start with `//`, no scheme, no CRLF) before it is ever interpolated into the
  email. A frontend-only check is bypassable by calling the API directly, and an
  unvalidated value here is an open redirect **inside an email we send** — worst possible
  place for one.

### [ ] W-02 — No memory of his last visit
**Severity:** degrades
**What happens:** no `users.last_login_at` anywhere. The product cannot tell a brand-new
account from a 50th visit, and cannot say "12 new since Tuesday".
**Proof exists:** `UserResponse` = `{id, email}` only. `consume_magic_link` stamps `email_verified_at` and `deleted_at`, never a login timestamp.
**Proof missing:** `git grep -n "last_login|logged_in_at|last_active"` → every hit is job-posting `last_seen_at` or the session-row liveness slide at `services/auth/sessions.py:88`.
**Smallest fix:** add `users.last_login_at`, stamp on login/consume.
**Verdict: LATER.** Nice, not urgent.

### [ ] W-03 — A brand-new user hits a wall on minute one  ⚠️ CODE ON BRANCH `1b89491` — rungs 4+5 NOT done
**Severity:** breaks the loop for every new signup
**What happens:** new account = zero profile. Dashboard shows `0 jobs` and a generic empty
state: *"Try adjusting your filters, expanding the time range, or lowering the minimum
score."* He has no filters. No search has ever run. Nothing links him to `/profile`. The
"Searching for…" hint renders `null` by design when there's no profile — so there is
**no visible next step at all**.
**Proof exists:** `dashboard/page.tsx:253-260` treats a profile 404 as "the NORMAL case for a new account". `SearchingFor.tsx`: `if (clean.length === 0) return null`. `JobList.tsx` EmptyState text is unconditional.
**Proof missing:** `git grep -n "no profile|hasProfile|profile_complete|onboard" origin/main -- frontend/src/app/dashboard frontend/src/components` → comments and test names only, no render branch.
**Smallest fix:** one conditional empty state — *"No profile yet → Upload your CV"* linking to `/profile`.

---

# STEP 2 — The Apply click

Employer tab opens, a row is written `stage='applied'`, timestamped server-side at the
click, second click is a safe no-op. **Your "when did he apply" requirement is already
satisfied.** Three breaks.

### [ ] W-04 — "Applied" really means "a tab opened"
**Severity:** degrades — **DECISION REQUIRED (see D-A)**
**What happens:** `window.open()` runs first with no return check (popup blockers fail
silently), then the DB write runs unconditionally. He could close the tab in two seconds.
Same row. There is no button anywhere to say "I actually submitted it".
**Proof exists:** `JobCard.tsx:126-140`; `database.py:1100` writes `stage='applied'` directly. `_VALID_STAGES` (`pipeline.py:34`) has no intermediate state.
**Proof missing:** `git grep -n "confirm.*submit|mark.*submitted|confirmed_applied"` → one unrelated hit (the magic-link confirm page).
**Smallest fix:** decide D-A first. Then at most a "submitted?" tick on the pipeline card.

### [ ] W-05 — Two different truths for "applied"
**Severity:** degrades — **resolved by D-05, see delete list**
**What happens:** `applications` (written by Apply) and `user_actions` (read by the job
list's "My Actions → Applied" filter) never speak. So that filter **always returns zero**.
**Proof exists:** filter UI `FilterPanel.tsx:522` → `jobs.py:657`. The only `user_actions` writer is `database.py:846`, which the apply path never calls.
**Do NOT fix by writing `applied` into `user_actions`** — that table is one row per (user, job) with `ON CONFLICT ... DO UPDATE` (`database.py:848`), so writing `applied` would silently **erase a `liked`**.
**Smallest fix:** derive `applied` from the `applications` table (pipeline is the one truth), or delete the filter option. See D-05.
*(Tests already written for the derive-from-applications approach: `backend/tests/test_pipeline_wiring.py`, 9 tests, currently red.)*

### [ ] W-06 — A second, untracked apply link
**Severity:** degrades — **one-line fix**
**What happens:** the job detail page has *two* links to the employer. The Apply button
tracks. Right above it, "View full description on source website" is a bare anchor with the
same URL and **no onClick**. It reads like a legitimate apply path. Use it and Job360 never
learns he went.
**Proof exists:** `JobDetailClient.tsx:494-503` (bare anchor) vs `:641` (tracked ApplyButton).
**Smallest fix:** route the anchor through the same handler, or remove it.

### [ ] W-07 — No apply link anywhere is click-tracked
**Severity:** degrades
**Proof missing:** `git grep -ni "click_track|apply_click|redirect_url|/go/|link_track" origin/main -- backend/src frontend/src` → one unrelated hit (Adzuna's own upstream field name). No redirect route exists for any apply link.

---

# STEP 3 — The documents (which CV did he apply with)

**Honest correction to an earlier answer:** there is only ever **one** doc per
(user, job, kind) — DB-enforced UNIQUE. So joining by `job_id` is unambiguous *today*,
and the Kanban CV/Letter button already reaches it. The danger is not ambiguity — it is
destruction.

### [ ] W-08 — No document is bound to the application row
**Severity:** breaks the loop — **your explicit requirement**
**What happens:** applying stores `job_id` + `user_id` only. Tailored docs live in a
separate table keyed by `job_id`. Nothing ties "this application" to "this document".
**Proof exists:** `pipeline.py:99`; `database.py:149-157` (applications table, no doc column); `migrations/0002_multi_tenant.up.sql:40-56`.
**Proof missing:** `git grep -n "tailored_doc_id|cv_document_id|document_id|tailored_documents_id" origin/main -- backend/src backend/migrations` → zero.
**Smallest fix:** write `cv_doc_id` / `cover_letter_doc_id` onto the application at apply time.

### [ ] W-09 — Apply sends no payload at all
**Severity:** degrades (same root as W-08)
**Proof exists:** `api.ts:337` `createPipelineApplication(jobId)` — one parameter. `pipeline.py:85` takes no body.

### [ ] W-10 — Regenerating a CV **destroys** the one he applied with
**Severity:** breaks the loop — **data loss happening today**
**What happens:** `upsert_tailored_doc` is an explicit `DELETE` then `INSERT`. There is no
v1 and v2 — only "the current one". Generate → apply → regenerate later, and the CV he
actually sent to the employer is **gone from the database permanently**, unrecoverable,
with no proof it existed.
**Proof exists:** `database.py:910-943`, with its own comment: *"Regenerating a doc is a fresh draft — old polished/kept state for THIS (user, job, kind) is superseded."*
**Proof missing:** `git grep -n "version" origin/main -- backend/migrations/0023_tailored_documents.up.sql backend/migrations/0024_tailored_flagged_terms.up.sql` → only the unrelated `profile_version` column. No doc-version column, no history table.
**Smallest fix:** snapshot the doc text/id onto the application at apply time (cheap), **or** make `tailored_documents` append-only and versioned (proper).

### [ ] W-11 — A download is not an event
**Severity:** degrades
**What happens:** downloading sets `status='kept'` and overwrites one `kept_at` on the
single live row. No format, no per-download row. The separate "Keep" button sets the same
field without downloading anything — so `kept_at` cannot even tell you he got the file.
**Proof exists:** `tailor.py:281-321` → `database.py:978-987` (plain UPDATE, no insert).
**Proof missing:** `git grep -n "download_log|tailored_downloads|doc_download"` → zero.
**Verdict: LATER / probably DELETE the idea.** `kept_at` is enough for now.

### [ ] W-12 — No staleness warning on a tailored doc
**Severity:** degrades
**What happens:** the backend *does* stamp `profile_version` on every doc. The API response
model drops it before it reaches the browser, so no "this CV was written from an older
profile" banner can ever exist.
**Proof exists:** `tailor.py:39-47` `TailoredDocOut` fields (no `profile_version`); `tailor.py:70-78` `_doc_out` never sets it; `frontend/src/lib/api-types.ts:2394-2414` mirrors the omission.
**Proof missing:** `git grep -n "profile_version" origin/main -- frontend/src/components/tailor frontend/src/components/pipeline` → zero.
**Smallest fix:** add the field to `TailoredDocOut`, compare on read, banner in `TailorPanel`.

### [ ] W-13 — No "My Documents" screen
**Severity:** degrades
**What happens:** every tailor route is scoped by `job_id`. There is no `GET /tailor` that
lists everything he has made.
**Proof missing:** `git grep -n '@router.get("/tailor")' origin/main -- backend/src/api/routes/tailor.py` → zero.
**Verdict: LATER.**

---

# STEP 4 — The pipeline board

Six columns, drag works, every move logged to `application_stage_history`. **Fine as a
board.** But your mental model breaks here.

### [ ] W-14 — The board has **no behaviour**. Every move is a manual drag.
**Severity:** breaks the loop — **DECISION REQUIRED (D-B, D-C)**
**What happens:** *"outreach"* is a string in a set. **No code gives it meaning.**
*"ghosted"* is set only by a manual button on a banner he sees only if he opens the page.
There is no rule anywhere that says "21 days of silence = ghosted".
**Proof exists:** all 5 `ghosted` hits in the codebase are: `pipeline.py:29-36` (a comment saying it's deliberately user-confirmed), `pipeline/page.tsx:154-162` (`handleMarkGhosted`, the only writer), `KanbanBoard.tsx:121,142` (column definitions).
**Proof missing:** none of those 5 is a cron, worker, or scheduled writer.

### [ ] W-15 — A pipeline card never learns its job died
**Severity:** degrades
**What happens:** staleness is checked **once**, at the moment the row is created. Never
again. The card looks alive forever.
**Proof exists:** `pipeline.py:108` is the only check. The 3 write statements to `applications` (`database.py:1103, 1130, 1802`) never touch `staleness_state`.
**Smallest fix:** select `j.staleness_state` in `get_applications`; flag `expired` on the card. *(Tests written.)*

### [ ] W-16 — Deadlines vanish, interview dates were never captured
**Severity:** degrades — **DECISION REQUIRED (D-D)**
**What happens:** before applying, the card shows "Apply by 30 June". The second it becomes
an application, the date is gone — the pipeline query never selects it and the model has no
field. Separately, `applications.interview_dates` is a **dead column**: nothing in the whole
app reads or writes it, so moving a card to Interview captures no date, so an interview
reminder is impossible.
**Proof exists:** `database.py:1181-1188` / `1219-1226` (no `j.deadline` in either SELECT); `models.py:392-398` (no deadline field); `database.py:217` (the dead column).
**Proof missing:** `git grep -n "interview_dates" origin/main -- backend frontend` → 3 hits, all schema. `git grep -n "deadline" origin/main -- frontend/src/app/pipeline frontend/src/components/pipeline frontend/src/lib/types.ts` → nothing.
**Smallest fix (deadline):** add `j.deadline` to the SELECT + model. **(interview_dates):** decide D-D — wire it or delete it.

---

# STEP 5 — The notification engine

**This is the heart of the problem.** The plumbing is genuinely good — dispatcher, ledger,
quiet hours, digest, retries, a notifications history page. Nothing pipeline-shaped ever
enters it.

### [ ] W-17 — The instant email strips the score, the reason, and the salary
**Severity:** breaks the loop — **instant is the DEFAULT mode**
**What happens:** the digest joins `user_feed` + `job_enrichment` and calls
`build_decision_card`. The instant path runs `SELECT title, company, apply_url` and builds
a bare string. Two users on different settings see completely different products, and the
default one is the poor one.
**Proof exists:** `workers/tasks.py:354` (the 3-column SELECT), `:360` (the bare body) vs `:1059` (`build_decision_card` in `send_bundle`). `build_decision_card` has exactly 3 hits in `backend/src`: its definition (`decision_card.py:173`), one import (`tasks.py:27`), and one call site (`tasks.py:1059`) — `send_notification` never touches it.
**Side effect:** `job_row.get("match_score")` passed to `dispatch()` is therefore **always `None`** — it is used only as a threshold gate (`dispatcher.py:330`), never in the text.
**Smallest fix:** make `send_notification` reuse the digest's join + `build_decision_card`.

### [ ] W-18 — The instant email links **straight to the employer**, not to us
**Severity:** breaks the loop — **your explicit requirement, and this is the hard blocker on it**
**What happens:** the digest link is `job360.uk/jobs/{id}` — correct, the click *can* come
back. The instant email sends the **raw employer `apply_url`**. In the default mode the
click physically cannot return. Your two notification modes ship opposite designs.
**Proof exists:** `services/delivery/decision_card.py:205` → `f"{site_base_url.rstrip('/')}/jobs/{job_id}"` (good) vs `workers/tasks.py:360` → `body = f"Job360 match: {title}\n{apply_url}"` (raw employer link). Also `services/notifications/report_generator.py:87` → `[Apply]({apply_url})`, same raw link.
*(Verified by hand, not just by agent — this corrects an earlier report that said all email links point at our own page.)*
**Smallest fix:** same fix as W-17. One change closes both.

### [ ] W-19 — Nothing ever notices "no reply"
**Severity:** breaks the loop — **THE ONE BREAK**
**What happens:** the query **already exists** — `get_stale_applications`, 7+ days dormant,
correctly excludes offer/rejected. It is wired to exactly one consumer: an in-app banner on
the Pipeline page. No cron. No notification. He finds out his applications went quiet only
if he remembers to open that page and look.
**Proof exists:** `pipeline.py:74-81` (the route), `database.py:1214-1235` (the query), `pipeline/page.tsx:93` + `:253-296` (banner only).
**Proof missing:** `git grep -n "get_stale_applications" origin/main -- backend/src` → exactly 2 hits, the definition and that one route. `workers/settings.py:220-241` is the complete cron list — `nightly_ghost_sweep`, `refresh_catalog`, `notification_tick`, `enrichment_sweep` — **none** references applications or pipeline.
**Smallest fix:** one daily cron next to the existing four → `get_stale_applications` per user → existing dispatcher.

### [ ] W-20 — Every message is about a NEW JOB, never about HIM
**Severity:** breaks the loop
**What happens:** email bodies are built from the shared catalog + his feed score + the LLM
verdict. The engine has **never** read `applications`. There is no "your application moved
to interview", no "your week: 3 applied, 1 interview, 2 quiet".
**Proof missing:** `git grep -n "applications" origin/main -- backend/src/services/delivery backend/src/services/notifications` → **zero hits**.
**Smallest fix:** a "your pipeline" section in the digest build, plus a notify call inside `advance_application`.

### [ ] W-21 — No deadline / interview / CV-staleness reminders exist at all
**Severity:** breaks the loop — **design gap, not a quick wire**
**What happens:** these facts aren't merely unwired, they aren't tracked. The only thing
called "staleness" in the codebase (`ghost_detection.py`, the 2am cron) is about a **job
listing disappearing from its source** — a completely different concept from *his
application* going quiet.
**Proof missing:** `git grep -niE "deadline|interview_date|cv.*stale|tailored.*stale" origin/main -- backend/src/workers backend/src/services/notifications backend/src/services/channels` → zero.
**Depends on:** W-16 (capture the dates first).

### [ ] W-22 — An instant notification that fails once is never retried
**Severity:** degrades
**Proof exists:** `tasks.py:400-407` (one-shot `mark_ledger_failed`) vs `tasks.py:1170-1184` (`send_bundle`'s retry → DLQ after 5). Only `send_bundle` has retry logic.
**Verdict: LATER.**

---

# STEP 6 — Channels, the email, and the click that must come back

### [ ] W-23 — No unsubscribe link. At all.
**Severity:** blocks public launch (deliverability + legal), technically small
**What happens:** the only way to stop the emails is to log in and delete the channel.
**Proof exists:** `services/delivery/email_body.py` — full body construction, no unsubscribe line.
**Proof missing:** `git grep -niE "unsubscribe|opt.?out|List-Unsubscribe" origin/main -- backend/src frontend/src` → two unrelated hits (PostHog `opt_out_capturing`, an auth listener comment).
**Smallest fix:** one tokenised unsubscribe endpoint + a `List-Unsubscribe` header.

### [ ] W-24 — The email click is invisible
**Severity:** degrades — **DECISION REQUIRED (D-E)**
**What happens:** the link carries no token, no marker. No open state, no click state.
**Proof missing:** `git grep -niE "router\.(get|post)\(.*(click|track|redirect|/r/)" origin/main -- backend/src` → no matches. `utm_` → no matches. No `webhooks.py` route file exists. `resend.*webhook|svix` → no matches.
**Note:** your plain-text, pixel-free email is quietly a **deliverability asset**. Adding tracking is a trade, not an upgrade.

### [ ] W-25 — An application never records that it came from an email
**Severity:** degrades
**Proof exists:** `database.py:149-157` — applications columns are `id, job_id, stage, notes, created_at, updated_at` only. `ApplyButton.tsx:37` passes only `job_id`.
**Verdict: LATER** (depends on D-E).

### [ ] W-26 — The ledger has nowhere to put "opened" or "clicked"
**Proof exists:** `migrations/0004_notification_ledger.up.sql:4-15` — status is `queued|sent|failed|dlq`. `ChannelSendResult` (`dispatcher.py:64-71`) has `ok/error/skipped/queued_digest` only.
**Verdict: LATER** (depends on D-E).

---

# CROSS-CUTTING (found in sweep 1)

### [ ] W-27 — The funnel goes dark right after Apply
**Severity:** blocks a useful launch — **4 one-line fixes, cheapest item here**
**What happens:** 6 analytics events exist total: `signup_completed`, `cv_uploaded`,
`extraction_completed`, `search_run`, `job_viewed`, `application_created`. **Nothing** fires
for: moving a card between stages, generating a CV, downloading a CV, adding a channel.
**Proof missing:** `git grep -l "posthog" origin/main -- frontend/src` → 11 files; `KanbanBoard.tsx`, `pipeline/page.tsx`, `TailorPanel.tsx`, `TailorButton.tsx`, `TailorSection.tsx`, `channels/page.tsx` are all absent. Backend has zero posthog.

### [ ] W-28 — Clearing a profile section or restoring a version never re-scores the feed
**Severity:** degrades — the app shows a stale number as if it were fresh
**Proof exists:** `_maybe_trigger_rescore` has exactly 2 callers — `profile.py:696` (`_extract_save_trigger`) and `:885` (`upload_linkedin`). Neither is inside `clear_profile_section` (`:1061-1103`) or `restore_version` (`:1136-1151`).
**Smallest fix:** two lines.

### [ ] W-29 — Three CV facts feed the score but are never shown
**Severity:** degrades
**What happens:** career domain, spoken languages, and education detail are read into the
scoring prompt. He can never see or correct them.
**Proof exists:** `services/llm_matcher.py:249-260`; `services/profile/cv_parser.py:993-994`.
**Proof missing:** `cv_languages` / `career_domain` / `cv_education_details` → zero hits in `api/models.py`, `routes/profile.py`, and all of `frontend/src`.

### [ ] W-30 — Owner-side blindness (bundle — all LATER)
- Finished-search per-source stats returned but discarded (`status.result` → 0 reads in `frontend/src/app`)
- Run-history endpoint + typed client wrapper, **0 UI callers** (`getRecentRuns`)
- Circuit-breaker trips log a warning and vanish (`main.py:980-988`, `newly_opened` never persisted)
- `audit_log` written by 8 routes, no product screen reads it (only `scripts/observe.py:63`, a manual ops CLI that counts rows, never shows content)
**Verdict: DON'T BUILD YET.** You are the only admin, and you have Railway logs + SQL.

---

# DELETE LIST — deleting dead code is a real fix

### [ ] D-01 — `applications.interview_dates` column
Dead schema. Nothing reads or writes it. Delete now; re-add properly when a real user
reaches an interview. Dead columns lie to future you. *(Conditional on D-D.)*

### [ ] D-02 — "Keep without downloading"
Backend route + typed API client wrapper exist; **no button calls them**. Your own code
comment says download = keep. Delete the route and the wrapper.

### [ ] D-03 — `getActionCounts()` in `lib/api.ts:212`
Defined, exported, **zero callers** anywhere in frontend or backend.

### [ ] D-04 — `applications.notes_history`
Written on every notes edit (`database.py:1784-1808`), never returned by any model or
route, never rendered. Either surface it or stop writing it — storing data you never show
is liability.

### [ ] D-05 — The `user_actions` "applied" path
Nothing writes it. Decide with D-F: either delete the "Applied" option from the My Actions
filter, or derive it from `applications`. **Do not write `applied` into `user_actions`** —
it would erase the user's `liked`.

---

# DECISIONS ONLY YOU CAN MAKE

These are product calls, not engineering. Work below them is blocked until you answer.

### [ ] D-A — What does "applied" mean?
Clicked the link, or confirmed submitted? Two different products. **Blocks W-04.**

### [ ] D-B — What does "outreach" mean?
Today it is a word on a column with no code behind it. Is it "I chased them"? Does entering
it reset the silence clock? **Blocks W-14.**

### [ ] D-C — When is a job ghosted — 14 days? 21? 30?
And does the product **suggest** ghosted (you confirm) or **decide** it? Recommendation:
suggest. Never auto-move a card before D-B is answered. **Blocks W-14, W-19.**

### [ ] D-D — Do you track interviews at all?
`interview_dates` is a feature you never decided to build. Wire it with a real date prompt,
or delete the column. **Blocks W-16, W-21, D-01.**

### [ ] D-E — Do you want click tracking in email at all?
Privacy and deliverability trade, not an engineering default. **You get ~90% of the value
free:** the chase cron (W-19) plus the apply row already tell you the email worked.
**Blocks W-24, W-25, W-26.**

### [ ] D-F — Should the job list have an "Applied" filter at all?
The pipeline page already shows applied jobs. Either derive the filter from `applications`
or delete the option. **Blocks W-05, D-05.**

### [ ] D-G — Should the digest talk about *him*?
"Your week: 3 applied, 1 interview, 2 gone quiet." Tone call first, then a small wire.
**Shapes W-20.**

---

# ALREADY FINE — do not rebuild

- Magic-link login itself (no enumeration, scanner-proof confirm step, seeds notification rules)
- Apply timestamps — server-side, at the click, second click is a safe no-op
- The six-stage board with full `application_stage_history`
- The delivery ledger, quiet-hours handling, digest queue, and the notifications history page
- The **digest** email's design — links to `job360.uk/jobs/{id}`, carries the decision card
- One doc per (user, job, kind) enforced by a DB UNIQUE — the join is unambiguous today
- The Kanban card's CV/Letter button (it exists and works; it just can't say whether a doc is there — see W-15's sibling, `get_tailored_summary_for_jobs`, 0 callers)

---

# IN FLIGHT

- Branch `feat/wire-pipeline-page` (off `origin/main`)
- `backend/tests/test_pipeline_wiring.py` — 9 tests written, **currently red**, covering
  W-05 (derive applied from applications, never erase a like), W-15 (expired flag), and the
  dead `get_tailored_summary_for_jobs` wire. Implementation not started.
