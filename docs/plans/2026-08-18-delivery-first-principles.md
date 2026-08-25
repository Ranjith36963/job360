# Delivery — first principles
<!-- doc: PLAN -->

> **PLAN — not a description of today's code.** Written to be built, possibly never built or since changed. Verify against code before trusting. <!-- banner: auto -->

Date: 2026-08-18. Status: design, not built. Author: Claude, for Ranjith.

---

## 1. What we have (measured, not remembered)

**Five channels**, all through Apprise, all equal citizens:
`email · slack · discord · telegram · webhook` — `backend/src/api/routes/channels.py:33`

**The plumbing is genuinely good:**

| Piece | Where | State |
|---|---|---|
| Per-user rule, one row | `notification_rules` (migration 0012, `UNIQUE(user_id)`) | works |
| Modes: instant / daily / every_n_hours | `workers/tasks.py:_bundle_due` | works |
| Quiet hours, timezone-aware (IANA) | `channels/dispatcher.py` | works |
| Digest queue + 5-min cron + bundle drain | `user_notification_digests`, `notification_tick`, `send_bundle` | works |
| Per-channel idempotency | `notification_ledger` | works |
| Credential encryption | Fernet, `channels/crypto.py` | works |
| SSRF guard on webhooks | `channels/ssrf_guard.py` | works |
| Retry → DLQ after 5 failures | `workers/tasks.py` | works |

**The payload is a link.** That is the whole problem.

- Instant send: `f"Job360 match: {title}\n{apply_url}"` — `workers/tasks.py:355`
- Digest send: `f"• {title} @ {company} — {apply_url}"` — `workers/tasks.py:1034`

The digest query selects only `id, title, company, apply_url` (`tasks.py:1017`). Meanwhile the
dashboard shows fit score, the AI judge's verdict, its written reason, salary range and staleness
(`frontend/src/components/jobs/JobCard.tsx:98-178`). **We compute why a job fits, then don't send it.**

**Email transport: already fixed on `main`.** An earlier read (of a stale branch) said user email
channels were stuck on SMTP while auth email ran on Resend. That has since been repaired:
`services/channels/email_url.py:97-119` now builds a `resend://` Apprise URL and keeps `mailtos://`
only for local/self-hosted SMTP, because Railway blocks outbound SMTP ports 25/465/587. So the
transport for the one channel that matters **already works in production** — the missing piece is
purely what we put in the body.

**Zero delivery telemetry.** PostHog (project 213945) has ever received exactly seven event types:
`$pageview, $identify, cv_uploaded, extraction_completed, search_run, job_viewed, application_created`.
There is no `notification_sent`, `_opened`, `_clicked`, or `channel_connected`. Five channels, zero instruments.

**Zero usage.** Last 90 days: 4 identified users, 8 searches, 3 job views, **1 application**.
Last activity 2026-08-09. Nobody has told us what they want, because nobody is here yet.

---

## 2. What users look for (evidence, with its bounds)

Our own data is too small to generalise from. These are external — each claim below is now
attributed to where it was actually found (re-checked 2026-08-24 while fixing a CodeRabbit finding
that these numbers had no citations). Where a number could not be traced to the named source, it
is marked **UNVERIFIED** rather than left silently attributed.

- **Volume is a real risk; the exact "~10/day" figure is UNVERIFIED.** High-volume job-alert
  aggregators are a known complaint pattern — WhatJobs' own review of a competitor notes users
  "often report receiving multiple alerts per day" after signing up
  ([whatjobs.com — Job2Careers review](https://www.whatjobs.com/news/job2careers-review-2026-legit-job-board-or-high-volume-email-engine/)),
  and Scale.jobs frames its pitch around targeted alerts instead of high-volume ones
  ([scale.jobs — "8 Ways To Set Up Smart Job Alerts"](https://scale.jobs/blog/smart-job-alerts-setup-methods)).
  Neither source states "~10 emails/day" or "more than half irrelevant = broken" as a measured
  figure — treat those two numbers as this document's own estimate, not a citation.
- **Choice overload is a real, studied effect; the specific numbers below are UNVERIFIED against
  the sources checked.** UX writing on the paradox of choice
  ([usertesting.com — "Using Paradox of Choice in UX Design"](https://www.usertesting.com/blog/how-to-use-the-paradox-of-choice-in-ux-design))
  confirms the *direction* — more options reduce action, e.g. Unbounce cut a form to 3 options and
  saw conversions rise ~17% — but "6 options → 30% acted; 24 options → 3%" and "10 extra pension
  options → −2% participation" are the classic Iyengar/Lepper jam study and Iyengar/Huberman/Jiang
  401(k) study. Neither page found under unbounce.com or usertesting.com states these exact
  numbers, so they are **UNVERIFIED** here — likely correct as a description of the underlying
  research, but not confirmed against the two sources this doc names.
- **Ambiguity hurts more than rejection — confirmed.** The average job seeker takes 6–10
  rejections before landing a role, with confidence typically wavering after the fifth, and 32.4%
  report exhaustion —
  [blog.theinterviewguys.com — "Coping with Job Rejection Fatigue"](https://blog.theinterviewguys.com/coping-with-job-rejection-fatigue/)
  (citing Huntr's Job Search Trends Report, Q1 2025). The specific psychological cost of
  unexplained silence (vs. a clear no) is discussed in
  [staffingbystarboard.com — "The Psychology of Rejection"](https://staffingbystarboard.com/blog/the-psychology-of-rejection-how-to-handle-100-applications-with-no-response/).
- **Email is where scams reach them — confirmed.** 95% of job seekers report meeting a suspicious
  offer; email is the top channel (65%), ahead of text (63%) and recruiter outreach (56%) —
  [monster.com — "Job Scam Statistics 2026"](https://www.monster.com/career-advice/research/job-scam-statistics)
  (Monster's 2026 Job Scam Report, n=884 U.S. workers).

**What this actually means:** the scarce resource is not the user's inbox. It is the user's
*decision capacity and their remaining confidence*. Everything below follows from that.

---

## 3. First principles

Strip it back. Three questions.

**Q: What is the job-to-be-done?**
Not "see jobs". Not even "get alerts". It is: **an application submitted to a role worth having,
without me having to grind.** Shopping ends when you buy; a job hunt ends when someone *else* says
yes. So delivery has not delivered until an application exists.

**Q: What is a channel, really?**
A channel is not a broadcast pipe. It is **the surface where the human says yes.** The moment you
see it that way, the question stops being "how many channels" and becomes "where is this person
willing to make a decision?"

**Q: What is the unit we send?**
Not "a notification". **A decision that needs a human yes.** Notifications are free to produce and
expensive to receive; decisions are the opposite. We should meter the expensive thing.

### The correction to "3 a day"

3 was a starting default, and defending a constant is the wrong instinct. The principle is:

> **Never send more decisions than this person currently has capacity to make.**

That capacity is a *learned per-user budget*, not a global number. Someone who opens and acts every
morning earns a wider budget. Someone who has not opened in four days gets one carefully chosen item,
or silence. The system should discover the number, not us. This is the part that scales without a
setting, and it is the opposite of a fixed rule.

---

## 4. What to remove

First-principles cut: **we currently have 5 channels × 1 weak payload. Invert it — 1 channel × the
full payload.** Surface area is the enemy; depth is the product.

| Remove | Why | Size |
|---|---|---|
| Slack / Discord / Telegram as first-class channels | A private job hunt does not live in your employer's Slack, a gaming server, or (in the UK) Telegram. They exist because Apprise made them free, not because anyone asked. Zero evidence of demand — and we cannot even measure them. | ~450 of 807 lines in `channels.py` (lines 357→end), 3 OAuth flows, 7 env secrets, 3 live-HTTP failure modes |
| Nothing on the email transport — it is already right | `email_url.py` already prefers `resend://` and falls back to `mailtos://` only for self-hosted SMTP. Leave it alone; spend the effort on the body. | — |
| The three user-facing cadence settings | Users do not want to configure plumbing. They want "tell me when it's worth it". Keep the digest *mechanism*; delete the *choice*. | `notify_mode` as a UI concept |
| Quiet hours as a user setting | Derive it from the timezone we already store. Keep the behaviour, remove the form field. | one settings section |
| The duplicated payload builders | Two places compose the message by hand (`tasks.py:355`, `tasks.py:1034`) and neither knows what the dashboard shows. | replace both with one builder |

**Keep webhook.** It costs nothing (it is already just Apprise), it is the escape hatch for the
one power user in fifty, and it is the honest way to say "we do not support your channel, here is
the raw feed." Demote it, do not delete it.

**Net: three channels' worth of code deleted, one channel made ten times better.**

---

## 5. What to build — the exponential version

### 5.1 The inbox is the app

Email is not the small choice. It is the only channel that can carry the thing we actually sell —
a tailored CV as an attachment — and, critically, **Resend can receive email as well as send it.**

That makes the channel *bidirectional*, and that is the whole unlock:

```
we send:   "2 worth your time today. Reply 1 or 2 to apply — CV is attached."
they send: "1"
we do:     submit application 1, log it, learn from it
```

No app to open. No dashboard to visit. No new channel to build. The reply *is* the interface, and
every reply is a labelled training example we currently have no way to collect. That is the
compounding loop — not more pipes.

### 5.2 One payload, many renderers

```
        ┌─────────────────┐
        │  DecisionCard   │   score · why · salary · staleness · CV · action
        └────────┬────────┘
     ┌───────────┼───────────┬─────────────┐
   email      dashboard    webhook     (future)
   (full)     (full)       (raw JSON)
```

One builder, shared with the dashboard, so the number in the email is *the same number* on the
screen and the words are the same words. Delivery stops being a different product.

### 5.3 The three properties, named

**Scalable** — channel-agnostic core, thin renderers. Adding a surface later is a renderer, not a
subsystem. Per-user decision budget means send volume grows with engagement, not with catalogue size.

**Reliable** — we already have the hard parts: idempotency ledger, retry, DLQ, advisory-locked
migrations. What is missing is an *outbox*: write the intent to send in the same transaction as the
decision, then let a drainer own delivery. Never lose a send, never double-send.

**Trustworthy** — this is the one that decides whether we survive contact with a real user:
- Verified sender (`job360.uk`), plain subject lines, no hype, visible unsubscribe. In 2026 an
  unbranded jobs email reads as fraud by default.
- **Explainability is the anti-scam signal.** A scammer cannot write "this matches because your
  CV shows 4 years of Postgres and they need exactly that." Our `llm_reason` field, built for
  ranking, is accidentally our strongest proof of legitimacy. Ship it in the body.
- **Say the no out loud.** "Checked 41, dropped 38 — too junior, wrong city, no visa." This
  directly attacks *ambiguous rejection*, the thing the psychology says does real damage.
- **Write on empty days.** "Nothing good today." Costs one email, buys the trust that makes the
  good days believable, and removes the dread of opening it.
- Every send visible to the user in an audit view. Nothing happens to them that they cannot see.

### 5.4 Why this is exponential, not linear

| Linear (what we did) | Exponential (what compounds) |
|---|---|
| add a 6th channel | every reply teaches the matcher → next week's shortlist is better |
| add more sources | agent drafts the application; human approves in one keystroke |
| more rows per email | fewer, better rows as the budget adapts per user |

More channels is addition. A feedback loop is multiplication. We currently have five additions and
zero multiplications — and we cannot even start the loop, because the events do not exist.

---

## 6. Sequence

1. **Instrument.** Emit `notification_sent / delivered / opened / clicked / replied`,
   `channel_connected`, `apply_clicked`. Without this every later decision is a guess. ~1 day.
2. **One payload builder.** `DecisionCard` shared by email and dashboard. Kill both hand-rolled
   string builders.
3. **Fill the email body.** Transport is already on Resend and working; put score + reason +
   salary + tailored CV attached + one apply action into it.
4. **Demote the chat channels.** Remove ~450 lines and 3 OAuth flows; leave webhook as the raw feed.
5. **Outbox + drainer** for exactly-once delivery.
6. **Inbound email** — reply to apply. This is where it stops being an alert product.
7. **Adaptive decision budget** — learned per user from steps 1 and 6. Only possible after them.

Do not build 6 or 7 until 1 is feeding them.

---

## 7. Bounds on this document

- Usage numbers: our PostHog project, last 90 days, read 2026-08-18. Small n — they prove *we*
  have no demand signal, not what the market wants.
- Psychology and scam figures: external sources, not our users. Citations added 2026-08-24 (see
  §2) — two of the four bullets carry numbers that could not be traced to a specific source and
  are marked UNVERIFIED there rather than presented as sourced.
- Code claims: re-read against `origin/main` (`8facf85`) on 2026-08-18. An earlier pass read a
  stale test branch and got the email-transport claim wrong; that is corrected above. Line numbers
  drift — re-grep before trusting them.
- Resend inbound capability: present in the Resend API surface; not yet tested by us.
