# 05 — Compliance & Legal
<!-- doc: LOG -->

> **DATED RECORD — true on the day it was written.** Numbers and statuses here are historical. Do not read as current state. <!-- banner: auto -->

> The compliance sweep hit the session token limit partway; **Fable finished the
> verification directly** (facts below checked in-code). This is gap analysis against
> UK-GDPR / PECR and enterprise-sale norms — pragmatic for a solo founder, not
> box-ticking. Job360 holds real PII: CVs, LinkedIn PDFs, emails, GitHub data.

## The one-paragraph reality
You are a UK SaaS processing sensitive personal data (CVs contain names, addresses,
work history, sometimes more). That puts you squarely under UK-GDPR. Today the app has
the *shape* of compliance (a delete route labelled "Article 17", privacy/terms pages)
but not the *substance* (delete doesn't erase, policies are stubs, analytics runs
without consent, and users aren't told their CV is sent to LLM providers). None of this
blocks you from operating, but each item is a real regulator or enterprise-buyer risk.

---

## P0-LEGAL — Scraping LinkedIn / Glassdoor / Indeed is the biggest business risk
> **STATUS: OPEN — OWNER DECISION.** No code change can resolve this. `linkedin.py` plus the `indeed`/`glassdoor` JobSpy keys remain in `SOURCE_REGISTRY` (`main.py:118-123`) and no written decision doc exists. This is a business/legal call, not an engineering one.
- **What I saw:** the source registry includes HTML scrapers and JobSpy (`indeed` + `glassdoor` keys) plus a LinkedIn scraper. These sites' Terms of Service **prohibit automated scraping**, and LinkedIn in particular has litigated it aggressively.
- **Why it matters:** this isn't a code bug — it's an existential business risk. A cease-and-desist, IP block, or lawsuit could remove core sources overnight or worse. An enterprise customer's legal team *will* ask "where does your data come from and are you licensed?" — and "I scrape LinkedIn" fails that question instantly.
- **Fix (decide deliberately):** (a) drop the ToS-violating scrapers and lean on licensed/API sources (Reed, Adzuna, the paid aggregators in your research notes — Fantastic Jobs, TheirStack); (b) or accept the risk *explicitly* as a documented, time-boxed decision with a migration plan off them. Either way — **make it a conscious choice written down**, not an accident in the registry.

## P0-COMPLIANCE — "Right to be forgotten" is soft-delete only; data is not erased
> **STATUS: FIXED** — `hard_delete_user` (`database.py`) irreversibly erases the user's rows from all 17 per-user tables, anonymises the shared `run_log` (and now `audit_log`) rather than deleting it, drops email-keyed `magic_link_tokens`, and removes the `users` row last. Never touches the shared catalog (rules #10/#17). `DELETE /auth/users/me` calls it after verifying the current password (rule #26). This one fix also closed the orphan-rows and soft-delete-resurrection findings.
- **What I saw:** `auth.py:231-245` — delete is a **soft-delete** (sets `deleted_at`), labelled "GDPR Article 17". But: the data agent found `purge`/user-delete leaves **orphaned child rows** (CVs, embeddings, actions, feed) because cascades are stripped (`02-DATA-AND-DB.md`); the security agent found a soft-deleted account **resurrects** if a magic link is later consumed (`magic_link.py:182-186`).
- **Why it matters:** Article 17 requires actual erasure, not a hidden flag. Right now a user who asks to be forgotten still has their CV, embeddings, and behavioural data sitting in your DB and backups — and their account can come back. That's a genuine non-compliance, and it's the exact thing a data-subject complaint or audit checks.
- **Fix:** implement real erasure — hard-delete or irreversibly anonymise all user-owned rows (profile, CV text, embeddings, actions, feed, channels, tokens) on delete; make resurrection impossible; document retention in backups (encrypted backups aging out in ≤30 days is a defensible answer, but write it down).

---

## P1 — the compliance-substance gaps
> **STATUS: MOSTLY FIXED** — consent banner FIXED (`4af1c7b`: PostHog is never `init()`-ed until the user accepts — a real gate, not opt-out-after-loading — and `disable_session_recording: true`; Decline is exactly as easy as Accept per PECR). Sentry PII FIXED (`send_default_pii=False` + scrubber). Article-20 export FIXED (`4af1c7b`: `GET /auth/users/me/export`, secrets redacted, token tables omitted, scoped to the session user). STILL OPEN — **owner**: real privacy/terms (still stubs) and the subprocessor list. Both are writing, not code.
- **No cookie consent, but PostHog analytics runs.** `layout.tsx:62` wraps the app in `PostHogProviderWrapper`; `AuthProvider.tsx:45` identifies logged-in users in PostHog. No cookie-consent banner exists (searched — none). Under UK-GDPR/PECR, analytics cookies + behavioural tracking need **prior consent**. **Also check PostHog session-recording is OFF** (it can capture keystrokes/PII on CV/profile forms). **Fix:** add a consent banner that gates PostHog until accepted; explicitly disable session recording on auth/profile pages.
- **Privacy & Terms are stubs** (~48/47 lines). For a PII processor these must state: lawful basis, what data you collect, **who you share it with** (subprocessors), retention, and how to exercise rights. **Fix:** write real policies (a template + an hour, or a cheap service like Termly/iubenda). This is table-stakes for any enterprise deal.
- **Subprocessor disclosure absent — users aren't told their CV goes to LLM providers.** CVs are sent to Groq/Cerebras/Gemini/OpenAI for parsing; email via Resend; hosting Railway; backups Cloudflare R2; analytics PostHog; errors Sentry. None are disclosed. **Fix:** publish a subprocessor list (page or section in the privacy policy); confirm each provider's DPA/no-training terms (esp. that CVs aren't used to train the LLMs).
- **Sentry `send_default_pii=True`** ships cookies + request bodies (possibly CV/password data) to a third party (`api/main.py:66`). **Fix:** `send_default_pii=False` + `before_send` scrubber. (Cross-ref `01`/`04`.)
- **No data-export (Article 20 portability).** There's a delete route but no "download my data." **Fix:** add a `GET /me/export` returning the user's profile + actions + applications as JSON.

---

## P1 — Plaintext emails written to on-disk logs (MISSED BY THIS AUDIT — found externally)

> **STATUS: FIXED (email-in-logs) — but this is NOT the original Fable "M9"** — `mask_email()` in `utils/logger.py`, applied to all 6 email-logging sites.
> **⚠️ AUDIT 2026-07-17:** two different "M9"s got conflated. The email-address leak (this one) IS fixed. The **original FABLE_FINDINGS.md M9** — raw **client IP** in `data/logs/*.jsonl` via the access-log middleware — is now **✅ FIXED 2026-07-17**: `utils/logger.py::mask_ip` hashes the IP to a stable `ip_<hex>` token in `api/middleware.py`. user_id is kept (internal opaque uuid, low-PII). See `AUDIT-2026-07-17-VERIFIED.md` (M9).

**Honest note: this audit did not find this.** An external audit (M9) did. Recording
it here rather than quietly patching it, because the *gap in coverage* is the more
useful finding: this audit checked what data goes to **third parties** (Sentry PII,
subprocessors) but never grepped what we write to **our own logs**. Logs rotate, ship
and get grepped — they outlive the request by far, and an address is personal data.

- **What was there:** the external audit reported 2 leaking lines. There were **6**.
  `auth/email_sender.py` logged the raw recipient on **five** separate lines
  (send-ok, send-failed, resend-ok, resend-error, no-credentials) — only one was
  flagged. `auth/password_reset.py:108` logged the raw address on the *unknown-email*
  path, i.e. it leaked addresses of people **who aren't even users**.
- **Fix:** `alice@example.com` -> `a***@example.com`. Two deliberate choices:
  **keep the domain** (that carries the delivery-debugging value — bounces, DNS, spam
  filtering — with far less identifying power than the local-part) and **keep the
  first character** (enough to correlate two lines as the same user in a support
  ticket, without identifying them). Degrades safely: `None`/garbage becomes
  `<none>`/`***`, never echoed.
- **Tests (11)** include **call-site** tests, not just the helper (rule #21): a
  masking function nobody calls fixes nothing, so the tests assert the *logger output*
  contains `v***@example.com` and never the raw address.
- **For the next audit:** grep `logger.*%s.*email`, `to_email`, and any `extra={...}`
  carrying an address. Same class of bug likely exists wherever a phone number, full
  name, or CV text reaches a log line.

## P2 — enterprise-sale readiness (do once P0/P1 clear)
> **STATUS: PARTLY FIXED** — audit logging FIXED (`b939e29`: migration 0025 `audit_log` + a QueueListener tee on the existing audit logger, so every security event persists to Postgres, survives log rotation and is SQL-queryable; anonymised on erasure). Dependabot FIXED (`4af1c7b`: pip + npm + github-actions, weekly). STILL OPEN: breach-notification runbook, MFA (a real feature — magic-link already removes password phishing), status page / SLA / liability ToS. All owner decisions.
- **No audit logging** of sensitive actions (login, delete, channel changes) — needed for SOC-2-style questionnaires and incident forensics.
- **No breach-notification plan** — UK-GDPR gives you 72 hours; have a one-page runbook ready before you need it.
- **No dependency/vuln scanning** — enable Dependabot (free, one file) so a known-CVE dependency doesn't ship silently.
- **No MFA option** for accounts — magic-link is decent, but enterprise buyers ask for MFA.
- **No status page / SLA / liability-limiting ToS** — needed to answer a security questionnaire and to sell to a company.

---

## What would block a customer or trigger a regulator TODAY
1. **Scraping LinkedIn/Glassdoor** — the first thing an enterprise legal review kills, and a standing C&D risk. (P0-legal)
2. **Delete that doesn't erase** — a single data-subject complaint exposes it. (P0-compliance)
3. **Tracking without consent** — PECR/UK-GDPR analytics-consent gap; ICO's current focus area. (P1)
4. **Undisclosed CV→LLM sharing** — a trust + disclosure failure the moment anyone asks. (P1)

## Fix order (compliance)
1. **Decide the scraping question** — the biggest lever; everything else is cheaper.
2. **Make delete actually erase** (also closes the data-doc orphan P1 and the security resurrection bug — one fix, three findings).
3. **Consent banner + disable session recording** — small, closes the tracking gap.
4. **Real privacy/terms + subprocessor list** — a day of writing; unlocks enterprise conversations.
5. Sentry PII, data-export, then the P2 SOC-2-flavoured items as you approach real sales.

**Verdict:** You built the *scaffolding* of compliance early (delete route, policy pages, prod-gated telemetry) — which is more than most solo founders do. The gap is that each piece is a stub, not the real thing. The scraping decision and real erasure are the two that genuinely matter; the rest is a weekend of writing and one consent banner.
