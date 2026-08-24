# Delivery rebuild — email + webhook only

Date: 2026-08-24. Owner decision, firm. Branch: `feat/delivery-email-webhook-only`.
Rationale and evidence: [`2026-08-18-delivery-first-principles.md`](2026-08-18-delivery-first-principles.md).

---

## The decision

Job360 delivers on **two** channels and no others:

| Channel | Status | What it is |
|---|---|---|
| `email` | **The product.** Designed, supported, measured. | The daily shortlist. Carries everything the dashboard carries. |
| `webhook` | Unsupported escape hatch. Kept because it is free. | Raw JSON for a technical user's own tooling. No design, no promises. |

### Production evidence — measured 2026-08-24, before writing the migration

Read directly from the prod Postgres (`railway run -s Postgres`, read-only counts):

| Table | Rows in production |
|---|---|
| `users` | **11** |
| `user_channels` | **0** — *nobody has ever connected any channel, not even email* |
| `notification_rules` | **0** |
| `notification_ledger` | **0** — *not one notification has ever been delivered to anyone* |
| `user_notification_digests` (unsent) | **0** |
| `oauth_states` | **0** |

Two consequences, both load-bearing:

1. **The deletion is risk-free.** There is no user channel data to destroy, no pending digest to
   strand, no OAuth state to orphan. The migration cannot lose anything, because there is nothing there.
2. **A second finding, bigger than the first:** delivery has never run *at all*. With
   `user_channels` and `notification_rules` both empty, `dispatcher.dispatch()` no-ops for every
   user on every path. **Verify the seeder against a real signup before claiming email works** —
   shipping a beautiful email into a table with no rows changes nothing. This is now Phase 4's
   first test, not an afterthought.

#### Follow-up traced: the seeder is ON, the existing 11 users are simply too old

`backend/src/services/notifications/defaults.py` exists precisely to fix "a job-alert product that
had never alerted anyone" (its own docstring, issue #318). Its master switch
`NOTIFY_SEED_DEFAULTS` **defaults to `"1"`** (`defaults.py:112`) — seeding is on unless someone
explicitly turned it off. So the mechanism is healthy and every *new* signup gets a rule plus an
email channel.

Which leaves a concrete, unglamorous gap: **the 11 accounts that already exist predate the seeder,
so they have no rule and no channel, and no amount of work on the email body will reach them.**

- **This is a one-off backfill, and it is a Phase 4 deliverable, not a nice-to-have.** Seed a
  `notification_rules` row + an `email` channel (the address is already on `users.email`) for every
  existing user, using the same code path as signup so the two can never drift.
- **It must be idempotent** (`ON CONFLICT (user_id) DO NOTHING`, per rule #23) and it must respect
  the `daily` default — seeding these users as `instant` would fire a burst of back-mail at people
  who have not heard from us in months. That is the single most damaging thing this project could
  do to the sending reputation the login flow depends on.

**Deleted: `slack`, `discord`, `telegram`.** They were never configured in production —
the settings page literally tells users "Slack, Discord, and Telegram need API keys configured
on the server" (`frontend/src/app/channels/page.tsx:197-199`). Zero users, zero telemetry,
~450 lines of OAuth, 8 env vars, 976 lines of tests, and 3 live-HTTP failure modes.

### ⚠️ TRAP — there are TWO unrelated "Slack" systems in this repo

| System | Keyed on | Verdict |
|---|---|---|
| **Product notification channel** | `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`, `DISCORD_*`, `TELEGRAM_*` | **DELETE** — this plan |
| **CI / harness alerting** (pages the owner when a build breaks) | `SLACK_BOT_TOKEN`, `SLACK_WEBHOOK_URL` (repo secrets) | **DO NOT TOUCH** |

The second lives in `.github/workflows/{uptime,synthetic-live,slack-drill,revert-main,post-merge-watch,ci}.yml`,
`.github/actions/slack/*`, `.github/merge-policy.yml`, `scripts/{merge_cage,check_alert_paths,cage_blockers,gate_wiring_check,drill_registry,slack_transition,check_workflow_slack_wiring}.py`,
`backend/tests/test_slack_voice.py`. Breaking it means the owner stops being told when `main` goes red.

Confirmed false positives — leave alone: `backend/src/core/companies.py:90` (Discord Inc. as an
**employer**), `backend/src/services/profile/skill_tiering.py:247` (GitHub topic fixture),
`frontend/src/middleware.ts:5` (Discord's link-preview bot).

---

## First principles (what drives every choice below)

1. **Delivery has not delivered until an application exists.** The funnel dies at apply —
   8 searches → 3 job views → 1 application in 90 days. So the email is judged on applications, not opens.
2. **The unit is a decision needing a human yes**, not a notification. Notifications are cheap to
   produce and expensive to receive; meter the expensive thing.
3. **Volume is capped by decision capacity, not by a constant.** A hardcoded "3 a day" is a guess
   wearing a number's clothes. The cap is a per-user budget the system learns. Cold-start default is
   a PARAMETER, never a hardcode.
4. **Email must say what the dashboard says.** Same score, same words, same reason — one builder,
   two renderers. If they can drift, they will.
5. **Trust is the feature.** 65% of job scams reach seekers by email. An unexplained jobs email is
   indistinguishable from fraud. `llm_reason` is our proof of legitimacy, not a nice-to-have.
6. **Say the "no" out loud.** Unexplained silence ("ambiguous rejection") is what damages job
   seekers. "Checked 41, dropped 38 — too junior, wrong city" is a feature.
7. **Empty shelves stay silent** (hard rule #29) — an unset preference never becomes a penalty or a
   guess. Applies to the email body too: no invented reasons, no fabricated confidence.

---

## The parity landmine (found before writing code)

The dashboard's score is **not** `jobs.match_score`.

- Dashboard reads the caller's **own** row: `llm_fit_score` if the judge scored it, else the
  per-user `feed_score` from `user_feed` — `src/api/routes/jobs.py:152-167`, `JobCard.tsx:98-99`.
- `jobs.match_score` is the **shared catalog** column: user-derived, last-writer-wins across all
  users. Measured 2026-07-28: for 97% of one account's feed, that shared value was exactly one
  other person's personal score.
- Today's digest reads from `jobs` (`workers/tasks.py:1017`).

**So a naive "just add the score to the email" ships a privacy-flavoured bug: a stranger's number.**
The new builder MUST read `user_feed`. This is the single most important line in this plan.

---

## 🚨 The biggest risk — read this before writing any email code

**Login and job alerts share one sending identity, and nothing in the codebase handles bounces
or complaints.**

Job360 is passwordless: the magic link is the *only* door in. Both mailers resolve the same
`SMTP_FROM` on the one verified domain — `services/auth/email_sender.py:55` and
`services/channels/email_url.py` `_from_address()`. There is no suppression list, no bounce
consumer, no complaint handling anywhere (greps find only comments).

"Email is the product" means multiplying volume on the exact identity authentication depends on.
**One spam-folder reclassification of `job360.uk` and users cannot log in.** Reputation damage is
the one failure a revert does not undo.

Mandatory before scaling sends:
1. **Separate subdomain with its own DKIM for alerts** (e.g. `alerts.job360.uk`). Keep
   `login@job360.uk` pristine and low-volume.
2. **Consume Resend bounce/complaint webhooks into a pre-send suppression check.**
3. **`List-Unsubscribe` header on the first HTML email**, not later.
4. **Do NOT enable Resend click-rewriting.** Rewritten links through a tracking domain are exactly
   what a scam email looks like — it would dress our anti-scam product in scam clothing. Use our own
   tokens on our own domain instead. (This revises the earlier "turn on click tracking" ask.)

## Three latent bugs found while designing (fix as part of this work)

1. **The score gate is silently dead.** `tasks.py:368-371` passes
   `match_score=job_row.get("match_score")`, but the SELECT at `tasks.py:346` never fetches that
   column — and `jobs` has no per-user score anyway. `pg.Row` is a dict, so `.get` returns `None`,
   and the dispatcher's gate at `dispatcher.py:299` (`if match_score is not None`) is bypassed
   entirely. The real gate is the feed-score prefilter in `main.py:497-509`. **Two different numbers
   currently decide "notify" versus "display".**
2. **A live API key is baked into every user's stored credential.** `email_url.py:118` embeds the
   Resend API key into the encrypted per-user email credential at create/seed time. Rotate that key
   and every existing email channel fails silently into retry → DLQ. The stored credential should be
   the *address*; the transport URL should be built at send time.
3. **Parity is already broken in two places.** The "which score is primary" COALESCE lives in SQL
   (`services/feed.py:90,104`) *and* in React (`JobCard.tsx:98-107`). A third Python copy in an
   email builder is parity-by-promise. **Fix: the API serves computed `primary_score` / `is_judged`
   from one server-side function; the frontend displays and stops computing; the email builder calls
   the same function.**

## What the email can and cannot promise

- **The judge is async.** At send time most jobs carry only the keyword score; they get judged
  later. `SCORER_VERSION` bumps re-score everything. So the email is a **snapshot** — stamp it
  ("score at time of sending"), never imply it stays equal to the live page.
- **The primary button must link to `/jobs/{id}` on our own site, never the raw `apply_url`.**
  Embedding `apply_url` kills the only attribution signal we have, skips the staleness guard
  (`pipeline.py:100-104` returns 410 on `confirmed_expired`), and hands the click to a rotting URL.
- **Transport must move off Apprise for email.** Apprise sends plain text (`dispatcher.py:345-349`)
  — no HTML, no `List-Unsubscribe`, no message-id back for threading, no per-send tags. Use the
  direct Resend HTTPS API, which `auth/email_sender.py:69` already trusts. Mind email physics: no
  JS, Gmail clips at ~102 KB, dark-mode inversion, always send a text part.

## Decision budget — v1 is a control loop, not a model

With 4 users and zero delivery telemetry, "learned" would mean learned from noise.

- **Cold start:** budget `B = 5/day` (env parameter), floor `F = 1`, hard cap `H = 10`. State lives
  as columns on `notification_rules` — one row per user already (rule #23), no new table.
- **Signal: on-site actions only** (`job_viewed`, `application_created`, dismiss), attributed by a
  per-send token. **Never opens** — Apple Mail Privacy Protection auto-fetches pixels, so a budget
  trained on opens learns Apple's proxy fleet.
- **Update rule, asymmetric:** acted on ≥1 card in the last 3 digests → `B` holds or +1 (≤H). Zero
  actions across 5 consecutive **delivered** digests → `B` −1 (≥F).
- **Named failure mode — the starvation spiral:** send less → fewer chances to act → read as
  disinterest → send even less → floor forever, indistinguishable from churn. Four guards:
  (1) floor of 1 plus the always-sent empty-day note, so total silence is impossible *by
  construction*; (2) decay only on **delivered** evidence; (3) an exploration pulse — every 7th
  digest ignores `B` and sends the full above-bar set (≤H); (4) the budget is a **visible setting**
  — the learned value only occupies the slot while the user has not pinned it.
- **Sequencing law:** telemetry ships ≥2 weeks before the update rule turns on.

## Reply-to-apply — the honest version

**First, deflate the threat truthfully: Job360 cannot submit an application to an employer.**
"Apply" means opening the external URL and writing a tracker row at stage `applied`
(`pipeline.py:85-110`). A forged reply's worst case is a polluted pipeline tracker. Design safe
anyway, because the blast radius grows the moment a reply can trigger a tailored CV.

- **The real attack surface is the inbound webhook, not the `From:` header.** Resend Receiving
  POSTs our endpoint; anyone can curl fake inbound JSON without touching SMTP. **Verify the
  Resend/Svix signature before parsing one byte.**
- **Identity comes from the address, never the sender.** Per-send single-use token in the reply-to:
  `reply+<token>@…` (VERP). 128-bit random, row `(token, user_id, job_id, purpose, sent_at,
  expires_at, used_at)`, scoped to one action on one job for one user, ~30-day expiry, idempotent on
  replay. Never trust `From:`, body text naming a user/job, or a stateless token we cannot revoke.
- **Defence in depth:** also check the inbound SPF/DKIM/DMARC verdict. Token valid but DMARC failed
  → do not act; email the registered address a one-click confirm. Every acted reply sends a
  confirmation with an UNDO link. `STOP` always works, token or no token.
- **Never guess an ambiguous reply.** v1 grammar is one keyword (APPLY / SAVE / SKIP / STOP) in the
  subject or first non-quoted line. "yes please" / "the second one" → no action, reply with the link.
  A digest reply structurally cannot resolve "the second one", so **literal reply-to-apply is for
  single-job emails only**; digests get one-click signed links.
- **Build the signed one-click links FIRST.** They deliver the same UX with none of the
  inbound-parsing swamp. Literal reply is the gesture on top.

## Load-bearing — looks deletable, is NOT

- `services/notifications/defaults.py` — the signup seeder. Without it both delivery tables stay
  empty and every path no-ops forever (its docstring documents the year of silence). It **is**
  "email is the product". Its `daily` default is an interlock: seeding `instant` means up to ~280
  emails/user/night.
- `ssrf_guard` + the send-time re-check (`dispatcher.py:82-101`) — DNS-rebinding defence; webhook
  survives, so this survives.
- `crypto.py` Fernet + `key_version` — webhook URLs carry secrets.
- **`notification_ledger` rows with channel `slack`/`telegram` — do NOT delete.** They are audit
  history *and* dedup keys (`UNIQUE(user, job, channel)`); the exporter groups by string.
- `connection_status` / `target_label` columns — dropping is migration churn for nothing.
- The queued≠sent accounting and job_id-required dispatch (`tasks.py:367-370, 383-395`) — hard-won
  PR #352 fixes.
- SI2 quiet-hours flush (`tasks.py:1276-1283`) — without it, instant-mode matches stranded in quiet
  hours never drain.

## Build log — what is actually done (updated as it lands)

| Phase | State | Evidence |
|---|---|---|
| 0 — docs tell the truth first | **done** | ARCHITECTURE, PRD FR-6.1, glossary, STATUS, STORY, README, .env.example, BREACH-RUNBOOK, user pillar, verify-job360 checklist, add-source + debug skills |
| 1 — backend removal (TDD) | **done** | `channels.py` −591 lines; `settings.py` −9 settings; `format_payload` chat branches gone with a resurrection guard; `test_channels_oauth.py` (976 lines) deleted, its one live guard relocated |
| 2 — migration 0031 | **written** | up + down; `oauth_states` also removed from `database._PER_USER_TABLES` in the same commit (see below) |
| 3 — frontend removal | **done** | connect UI, provider fetch, OAuth URL builders; api-types regenerated (0 chat refs); privacy policy corrected; 333/333 unit tests, lint + type-check clean |
| 4 — the email that says what the dashboard says | **wired** | `services/delivery/decision_card.py` + `email_body.py`; `send_bundle` now joins `user_feed` and `job_enrichment` and renders cards |
| 5 — telemetry | **not started** | still the highest-value next step |
| 6 — verification | see below | layer 3 (a real human, a real inbox) not yet run |

### Three bugs caught during the build, worth remembering

1. **Account deletion would have crashed.** `oauth_states` was listed in
   `database._PER_USER_TABLES`, which account deletion iterates issuing a DELETE per table.
   Dropping the table without editing that tuple turns "delete my account" into an
   `UndefinedTable` error (rule #26). Schema and list must move in one commit.
2. **`/providers` answered 405, not 404.** After deleting the route, the path still matched
   `DELETE /{channel_id}`, so a GET returned "method not allowed" — which reads as "the route
   is still there". Fixed by declaring the path `{channel_id:int}` so a non-numeric segment
   no longer matches at all.
3. **An infinite drain loop, introduced by this very change.** The new digest query INNER
   JOINs `user_feed`. A queued job whose feed row has gone produces no card — and its queue
   row would never be marked sent, so `notification_tick` would re-enqueue it every five
   minutes forever. The old catalog-only query could not produce this state; the new one can.
   Guarded by `test_send_bundle_drains_queued_jobs_that_have_no_feed_row`.

### A latent outage found but NOT fixed here — do this next

`build_email_apprise_url` bakes the **live Resend API key into every user's encrypted
channel credential** at create/seed time (`services/channels/email_url.py:117`):

```python
return f"resend://{api_key}:{_from_address()}/{dest}/"
```

The key is stored, per user, at the moment the channel is created. **Rotate that key —
routine security hygiene, or forced by a leak — and every existing email channel breaks
silently**: Apprise gets a 401, the dispatcher records `ok=False`, the row retries five
times and lands in the DLQ, and the user simply stops receiving email with no error
anywhere a human looks.

The fix, when someone picks this up: store the **address** as the credential and build the
transport URL **at send time** from current settings. The credential then contains nothing
secret, which also shrinks what a database leak exposes. Not done here because it changes
the meaning of stored rows and deserves its own migration and its own tests — but it is the
highest-severity thing this audit found that is still live.

### One parity decision worth stating plainly

Salary in the email comes from the **same source and the same parser** as the dashboard —
the `job_enrichment` blob through `services.salary.normalize_salary` — not from the
`jobs.salary_*` columns. Reading a different column would have produced a number that is
defensible on its own and still disagrees with the screen. Formatting mirrors
`formatSalaryRange` in `JobCard.tsx` exactly (`£70k–£85k`), because "£70,000 - £85,000" is
a visible difference to the only person who matters.

---

## Phase plan

Each phase is a separate commit. Tests first, always. `main` is production — every merge
auto-deploys — so nothing merges until Phase 6 verification passes.

**Step 0 — measure production before any deletion** (in flight): `user_channels` grouped by
`channel_type`, pending `user_notification_digests` by channel, `oauth_states` row count. If any
real chat rows exist, the owner sees whose before they are destroyed.

### Phase 0 — Docs tell the truth first
Update every doc that claims five channels, **before** the code changes, so no doc is ever ahead of
or behind reality mid-flight.

- `README.md:4`, `STATUS.md:202`, `STORY.md:79`
- `ARCHITECTURE.md:8,16,658,674` (system description, diagram, env-var table)
- `docs/product/pillars/01-user-pillar.md:557`, `docs/product/pillars/glossary.md:50`
- `.claude/skills/verify-job360/CHECKLIST.md:70,73` — drop items 34 and 37 (providers + OAuth flows)
- `CLAUDE.md` — rule #24's delivery-path line
- `.env.example:85-113` (8 vars), `cron_setup.sh:41-42`
- Add an `ARCHITECTURE.md` "⚠️ REMOVED" callout, matching the existing precedent at
  `ARCHITECTURE.md:546-552` where the pre-Apprise channel classes were retired.

### Phase 1 — Backend removal (TDD)
RED tests already written in `tests/test_channels_routes.py`:
`test_chat_channel_types_no_longer_exist` (422, not 400), `test_only_email_and_webhook_are_valid_channel_types`,
`test_chat_connect_routes_are_removed` (404, not 503 — 503 would mean the route still exists).

Then:
- `channels.py`: delete L357→end (Slack/Discord/Telegram flows), `_CONNECT_ONLY_TYPES` (+ its only
  use at L128), `_VALID_TYPES` (**dead code — referenced nowhere in the repo**), `ProvidersOut` +
  `GET /providers`, `TelegramConnectOut`/`TelegramPollOut`, `_consume_oauth_state`.
  `ChannelIn.channel_type` pattern → `^(email|webhook)$`.
- `core/settings.py:80-106`: delete all 8 vars. `SLACK_WEBHOOK_URL`/`DISCORD_WEBHOOK_URL` have
  **zero consumers** — pure dead weight. `OAUTH_REDIRECT_BASE` goes with them (only the two OAuth
  callbacks used it).
- `dispatcher.py:104-119`: `format_payload()` loses its slack/discord/telegram branches.
- `cli.py:24`: `--no-email` help text.
- Tests: delete `test_channels_oauth.py` (976 lines) **except** re-home
  `test_list_channels_returns_connection_status_and_target_label` — it is generic. Swap the many
  `"slack"` fixture channels in `test_notification_rules.py`, `test_notification_tick.py`,
  `test_worker_send_notification.py`, `test_dispatch_logging.py`, `test_metrics_exporter.py`,
  `test_notifications_endpoint.py`, `test_channels_dispatcher.py` to `webhook`/`email`.

### Phase 2 — Drop `oauth_states` (migration 0031)
Safe: nothing but the three deleted flows ever wrote to it. But it appears in **three generic
registries** that will break if the table vanishes without them:
- `src/repositories/database.py:1539` `_PER_USER_TABLES` (soft-delete cascade)
- `scripts/observe.py:62` `PER_USER_TABLES` (orphan-row sweep)
- `tests/test_migrations.py:303-374`, `tests/test_data_export.py:50`

Forward + reverse SQL pair, per repo convention.

### Phase 3 — Frontend removal
- `frontend/src/app/channels/page.tsx`: `OAuthReturnToast` (L41-70), the whole `ConnectRow`
  (L76-204) incl. Telegram polling, page copy L416 and L196-200.
- `frontend/src/lib/api.ts`: `channelConnectUrl` (L55-57), the `Channel["channel_type"]` union
  (L502-504) and its now-false "only these five values" comment, `ChannelProviders`/`getProviders`/
  `connectTelegram`/`pollTelegram` (L649-667).
- `frontend/src/app/privacy/page.tsx:137-138` — user-facing legal copy.
- Delete `frontend/src/lib/__tests__/channel-connect-url.test.ts`; strip the Telegram describe-block
  and provider fixtures from `channels-page.test.tsx`; fix the providers route-mock in
  `frontend/tests/e2e/corners-channels-settings-notifications.spec.ts:26`.
- **Regenerate** `openapi.json` + `api-types.ts` (`npm run gen:types`) — never hand-edit.

### Phase 4 — The email that says what the dashboard says
New `src/services/delivery/decision_card.py` — ONE builder, consumed by the email renderer and
asserted against the dashboard serializer.

Fields, all sourced from `user_feed` joined to `jobs`/`job_enrichment` (never `jobs.match_score`):

| Field | Source | Empty-shelf behaviour (#29) |
|---|---|---|
| `score` | `user_feed.llm_fit_score` ?? `user_feed.score` | omit, never 0 |
| `is_judged` | both `llm_fit_score` and `llm_verdict` present | drives the label wording |
| `reason` | `user_feed.llm_reason` | omit the line entirely |
| `salary` | `job_enrichment` → `salary_min_gbp`/`max` | omit |
| `staleness` | `jobs.staleness_state` | omit |
| `apply_url` | `jobs.apply_url` | required |

Plus the honest header: **considered N, sending M, dropped N−M** with the top drop reasons.
And the empty-day send: "Nothing good today" — a deliberate feature, not a failure path.

The tailored CV attachment and reply-to-apply are **Phase 7+**, gated on the owner's Resend switches.

### Phase 5 — Telemetry (the thing that makes everything after this knowable)
Emit `notification_sent`, `notification_delivered`, `notification_opened`, `notification_clicked`,
`channel_connected`, `apply_clicked`. Today PostHog has received **none** of these, ever — so every
claim about delivery is currently unfalsifiable.

### Phase 6 — Verification

Three layers, in order of how much they prove. Layer 3 is the only one that counts as "it works".

#### Layer 1 — the machine agrees with itself (I run this)

```bash
cd backend && python -m pytest -q -p no:randomly        # canonical full run
cd backend && python -m ruff check . && python -m mypy .
cd frontend && npm run lint && npm run type-check && npm run test:unit
```

Plus the targeted proof that the removal is real, not renamed:

```bash
# Every deleted route must 404 — not 503, not 405. 503 would mean the route
# still exists and is merely unconfigured, which is how those three spent
# their entire life in production.
cd backend && python -m pytest tests/test_channels_routes.py -q -p no:randomly

# The channel-type surface has exactly two members, in exactly one place.
grep -n 'channel_type: str = Field' src/api/routes/channels.py
```

#### Layer 2 — the database agrees (I run this, read-only, against prod)

```bash
railway run -s Postgres python scripts/check_prod_channels.py
```

Expected after the migration: no `slack`/`discord`/`telegram` rows anywhere, and
`oauth_states` gone. **Take a backup first** — the migration is the one irreversible step
in this plan.

#### Layer 3 — a real human gets a real email (the only proof that matters)

Everything above can pass while the product still delivers nothing — that is exactly the
state production has been in since launch (0 ledger rows, ever). So:

1. **Register a brand-new account** on job360.uk with a real inbox.
2. Confirm the seeder fired: that user now has one `notification_rules` row and one
   `email` channel. If not, nothing downstream can work and the rest of this is theatre.
3. Run a search so the feed has scored rows.
4. Trigger the digest and **open the actual email**. Check, as a user would:
   - the subject is quiet — no `!`, no `£`, no ALL CAPS;
   - the first line is a verdict ("2 jobs worth a look"), not a list;
   - the "we checked 41 and dropped 38" line is present and its arithmetic is right;
   - each job shows the **same score the dashboard shows for that job** — open both
     side by side, this is the parity claim and it is the one most likely to be wrong;
   - the judge's reason is there when the job was judged, and **absent, not invented**,
     when it was not;
   - every link points at `job360.uk`, none at an employer's domain;
   - it does not land in spam.
5. **The empty day matters too.** Force a run with no qualifying jobs and confirm the
   "nothing worth your time today" email still arrives.

#### What would make me say it does NOT work

- The email's score differs from the dashboard's score for the same job.
- A reason line appears for a job the judge never scored.
- Any link leaves job360.uk.
- The email arrives but the ledger has no row (delivery succeeded, accounting silent).
- A new signup ends with zero channels.

---

## Owner-side switches (only Ranjith can do these)

Verified on the Resend account 2026-08-24 — domain `job360.uk`, region eu-west-1:

| Setting | Now | Needed for |
|---|---|---|
| Sending | ✅ verified, enabled | already working |
| **Receiving** | ❌ **disabled** | reply-to-apply (Phase 7) |
| **Open tracking** | ❌ off | `notification_opened` |
| **Click tracking** | ❌ off | `notification_clicked` |

Phases 0–6 need none of these. They are the gate for Phase 7 only.

---

## Out of scope, deliberately

- Channel settings / cron / quiet hours / `notify_mode` — **owner says keep as-is for now.** The
  digest machinery (`notification_tick` → `send_bundle` → `notification_ledger`, retry → DLQ)
  stays untouched.
- The CI/harness Slack alerting (see the TRAP box).
- Reply-to-apply, tailored-CV attachment, adaptive decision budget — designed, not built yet.

---

## Bounds

Code claims re-read against `origin/main` on 2026-08-24. Line numbers drift — re-grep before
trusting any of them. Usage numbers from our own PostHog project, last 90 days; small n.
