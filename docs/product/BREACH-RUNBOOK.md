# Breach Runbook — the 72-hour plan

<!-- doc: LIVING | Fable compliance finding 05:39 — "have a one-page runbook ready before you need it" -->

**Read this top-to-bottom the moment you suspect a breach.** Do the steps in
order. The legal clock (72 hours to report to the ICO) starts when you
*become aware*, not when the breach happened — so the time you spend
panicking counts against you. This page turns panic into a checklist.

**One rule above all: write down the time of everything you do.** Open a
scratch file, timestamp every action. The ICO asks for this record even when
you don't have to report.

---

## Hour 0–1 — Contain (stop the bleeding)

Do these in order. Rotating a secret = Railway dashboard → service →
Variables → edit → redeploy happens automatically.

1. **Kill all logins** (two switches, use both):
   - Rotate `SESSION_SECRET` on Railway → every session cookie in the world
     is instantly invalid.
   - Belt-and-braces, in the DB: `DELETE FROM sessions;` → server-side
     sessions gone too. Every user (including the attacker) is logged out
     and must log in again via email.
2. **Rotate the database password** (`DATABASE_URL` / Railway Postgres
   credentials) — if the attacker had the DSN, this locks them out.
3. **Rotate the rest of the keys** (names only, values live in Railway):
   `RESEND_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`,
   `CEREBRAS_API_KEY`, `GITHUB_TOKEN`, R2 backup keys
   (`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY` in GitHub Actions secrets),
   job-source API keys. Each provider's dashboard → revoke old, issue new.
4. **`CHANNEL_ENCRYPTION_KEY` — read this before rotating.** Rotating it
   makes every stored notification-channel credential (Slack/Discord/Telegram
   webhooks and tokens) permanently unreadable — users will have to
   reconnect their channels. In a real breach that trade is CORRECT: rotate
   it, accept the reconnects. Just don't be surprised.
5. **If the app itself is compromised** (malicious deploy, defaced page):
   Railway → Deployments → roll back to the last known-good deploy.
6. **Do NOT delete or truncate any logs.** They are your evidence and your
   timeline. Containment never includes cleanup.

## Hour 1–24 — Assess (what actually got touched?)

**Evidence sources, in the order to check them:**
- Railway logs (backend + Postgres) — who connected, from where, when.
- The access-log middleware — every request has a request-id; grep for the
  suspicious window.
- Sentry — errors often show the attacker's failed attempts before success.
- Resend dashboard — were emails sent that we didn't send?
- PostHog (EU) — unusual usage patterns, mass exports.
- R2 backups — timestamps tell you the last clean snapshot.

**What data could be exposed — sensitivity map:**

| Table | What's in it | Sensitivity |
|---|---|---|
| `user_profiles`, `user_profile_versions` | CV text, LinkedIn text, GitHub data, preferences | **HIGH — this is the crown jewels** |
| `tailored_documents` | AI-generated CVs / cover letters | **HIGH** |
| `users` | email addresses, argon2id password hashes, timezone | Medium (hashes are argon2id — not reversible in practice, but report as exposed) |
| `applications`, `user_actions`, `user_feed` | job-hunt activity (who applied where) | Medium — sensitive in context (current employer must not learn) |
| `user_channels` | Fernet-encrypted webhooks/bot tokens | Medium (encrypted at rest; HIGH if `CHANNEL_ENCRYPTION_KEY` also leaked) |
| `sessions`, `oauth_states` | session + OAuth artifacts | Low once rotated/deleted |
| `jobs`, `job_enrichment`, `job_embeddings` | public job listings | Not personal data |

**Answer these four questions in writing** (the ICO form asks exactly this):
1. What happened, and how? 2. Whose data and how many people?
3. What categories of data? 4. What have you done about it?

## Within 72 hours — Report to the ICO (if reportable)

**Reportable?** Personal data was (or likely was) accessed/lost/altered AND
there is a risk to the people affected → **report**. CVs + emails = yes,
report. Genuinely no risk (e.g. encrypted backup lost, key safe) → you may
skip, but **write down why** — you must be able to defend that call later.

- Form + guidance: <https://ico.org.uk/for-organisations/report-a-breach/>
- ICO helpline: 0303 123 1113 (Mon–Fri) — you can ring first if unsure.
- **Report incomplete rather than late.** An initial report with "still
  investigating, will update" is fine and normal. Missing 72h is not.

## Tell the users (if the risk to them is high)

If exposed data could really hurt users (CVs + identities out in the open),
UK GDPR says tell them "without undue delay" — don't wait for the ICO reply.

Send via Resend from `login@job360.uk`, plain and honest:

> Subject: Security incident affecting your Job360 account
>
> On [date] we discovered unauthorised access to [what]. Your [email / CV
> profile / activity] may have been affected. Passwords are stored as
> non-reversible hashes; we have logged everyone out and rotated all keys.
> What you should do: [log in again / watch for phishing that quotes your
> CV / nothing further]. We reported this to the ICO on [date]. Questions:
> privacy@job360.uk. — We're sorry. Here's exactly what happened: [link]

No spin, no "we take security seriously" filler. Facts, what changed, what
they should do.

## After — Learn (within 2 weeks)

1. Post-mortem in `docs/`: timeline, root cause, what detection missed.
2. Fix the root cause as the top-priority mission, not backlog.
3. Update THIS runbook with what was wrong or missing in it.
4. Keep the incident log — the ICO can ask for it years later.

---

*Owner: Ranjith. Review this page every 6 months or after any incident,
whichever comes first. Last verified against the real stack: 2026-07-24
(sessions table + SESSION_SECRET dual kill-switch, Fernet channel-cred
rotation trade-off, R2 ciphertext-only backups, Resend sender domain).*
