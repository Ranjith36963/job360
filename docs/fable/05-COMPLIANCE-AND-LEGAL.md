# 05 — Compliance & Legal

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
- **What I saw:** the source registry includes HTML scrapers and JobSpy (`indeed` + `glassdoor` keys) plus a LinkedIn scraper. These sites' Terms of Service **prohibit automated scraping**, and LinkedIn in particular has litigated it aggressively.
- **Why it matters:** this isn't a code bug — it's an existential business risk. A cease-and-desist, IP block, or lawsuit could remove core sources overnight or worse. An enterprise customer's legal team *will* ask "where does your data come from and are you licensed?" — and "I scrape LinkedIn" fails that question instantly.
- **Fix (decide deliberately):** (a) drop the ToS-violating scrapers and lean on licensed/API sources (Reed, Adzuna, the paid aggregators in your research notes — Fantastic Jobs, TheirStack); (b) or accept the risk *explicitly* as a documented, time-boxed decision with a migration plan off them. Either way — **make it a conscious choice written down**, not an accident in the registry.

## P0-COMPLIANCE — "Right to be forgotten" is soft-delete only; data is not erased
- **What I saw:** `auth.py:231-245` — delete is a **soft-delete** (sets `deleted_at`), labelled "GDPR Article 17". But: the data agent found `purge`/user-delete leaves **orphaned child rows** (CVs, embeddings, actions, feed) because cascades are stripped (`02-DATA-AND-DB.md`); the security agent found a soft-deleted account **resurrects** if a magic link is later consumed (`magic_link.py:182-186`).
- **Why it matters:** Article 17 requires actual erasure, not a hidden flag. Right now a user who asks to be forgotten still has their CV, embeddings, and behavioural data sitting in your DB and backups — and their account can come back. That's a genuine non-compliance, and it's the exact thing a data-subject complaint or audit checks.
- **Fix:** implement real erasure — hard-delete or irreversibly anonymise all user-owned rows (profile, CV text, embeddings, actions, feed, channels, tokens) on delete; make resurrection impossible; document retention in backups (encrypted backups aging out in ≤30 days is a defensible answer, but write it down).

---

## P1 — the compliance-substance gaps
- **No cookie consent, but PostHog analytics runs.** `layout.tsx:62` wraps the app in `PostHogProviderWrapper`; `AuthProvider.tsx:45` identifies logged-in users in PostHog. No cookie-consent banner exists (searched — none). Under UK-GDPR/PECR, analytics cookies + behavioural tracking need **prior consent**. **Also check PostHog session-recording is OFF** (it can capture keystrokes/PII on CV/profile forms). **Fix:** add a consent banner that gates PostHog until accepted; explicitly disable session recording on auth/profile pages.
- **Privacy & Terms are stubs** (~48/47 lines). For a PII processor these must state: lawful basis, what data you collect, **who you share it with** (subprocessors), retention, and how to exercise rights. **Fix:** write real policies (a template + an hour, or a cheap service like Termly/iubenda). This is table-stakes for any enterprise deal.
- **Subprocessor disclosure absent — users aren't told their CV goes to LLM providers.** CVs are sent to Groq/Cerebras/Gemini/OpenAI for parsing; email via Resend; hosting Railway; backups Cloudflare R2; analytics PostHog; errors Sentry. None are disclosed. **Fix:** publish a subprocessor list (page or section in the privacy policy); confirm each provider's DPA/no-training terms (esp. that CVs aren't used to train the LLMs).
- **Sentry `send_default_pii=True`** ships cookies + request bodies (possibly CV/password data) to a third party (`api/main.py:66`). **Fix:** `send_default_pii=False` + `before_send` scrubber. (Cross-ref `01`/`04`.)
- **No data-export (Article 20 portability).** There's a delete route but no "download my data." **Fix:** add a `GET /me/export` returning the user's profile + actions + applications as JSON.

## P2 — enterprise-sale readiness (do once P0/P1 clear)
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
