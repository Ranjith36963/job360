# Job360 — Full App End-to-End Sweep

**Date:** 2026-06-06
**Method:** Drove the real app in a Chrome browser (Playwright) like a human — logged in/out, clicked every page and the key buttons, uploaded a CV, ran a search, applied to a job, moved it through the pipeline, added a channel + notification rule, changed the password — while watching the backend logs and SQLite DB to confirm each action actually landed. Fixed the clear issues and re-verified them.
**Test account:** `alex.tester.job360@gmail.com` (password is now `NewPass5678!` after the password-change test).

---

## Plain-English verdict

**The app works well, end to end.** Sign-up, login/logout, CV upload + AI parsing, job search (now returns real jobs), the job dashboard, the 8-dimensional job detail view, the application pipeline, notification channels, and account settings all work and persist correctly. I found **5 issues**: I **fixed 3** and verified them; **2 are design/policy decisions** I'm surfacing rather than deciding unilaterally.

---

## What WORKS ✅ (verified live, with evidence)

| Area | Evidence |
|---|---|
| **Auth** — register, login, logout, wrong-password, gated redirect | logout → `/login`; wrong pw → error + stays; correct pw → `/dashboard` |
| **Profile** — CV upload + parse, edit/save preferences, version history (restore/compare), GitHub enrich, export JSON resume | `POST /api/profile 200`, "Profile saved"; History drawer shows 2 versions; GitHub enrich `200` (rate-limit handled); `resume.json` downloaded |
| **Search** | live Reed run stored 5 scored ML jobs (was 0 before this branch's fixes) |
| **Dashboard** — job cards, Apply, Like, Details, stats, time buckets | Apply → external URL + pipeline row; Like → `user_actions(liked)`; Details → detail page |
| **Job detail** — 8D radar (Pillar 2 scoring), skill analysis, actions | renders for `/jobs/{id}` with score breakdown chart |
| **Pipeline** — Apply→Applied, Advance stage, Edit notes | DB: `applications` `applied→outreach`; note text persisted |
| **Channels** — add channel, **encrypted at rest**, list, remove | `POST .../channels 201`; credential stored as Fernet token `gAAAAAB…`, plaintext NOT in DB |
| **Notification rules** — score threshold, instant/digest, quiet hours, create | `POST .../notification-rules 201`; DB row `(slack, 50, instant)` |
| **Account** — change password (verifies current pw) | `PATCH .../password 204`; new pw logs in, old pw → 401 |
| **Notifications page** | ledger + filters render |
| **Pillars 1/2/3** | P1 sources→score→dedup (search works); P2 8D scoring (radar + dim columns); P3 multi-user auth + encrypted channels + pipeline + rules |

---

## Issues FIXED + verified this sweep ✅

### #1 (minor) — Auth forms showed raw `API error 401: invalid credentials`
Leaked the HTTP status to users. **Fix:** added `friendlyAuthError()` (`lib/api-error.ts`) mapping status→plain message; wired into login + register pages.
**Verified:** wrong-password login now shows **"Incorrect email or password."** (screenshot `sweep-10`).

### #5 (medium) — `npm run type-check` was failing (red CI gate)
`verify-email/page.tsx` used `<Button asChild>` — but this project's Button is **Base UI**, which composes via a `render` prop, not the Radix `asChild`. **Fix:** switched both usages to `render={<Link/>}` (the codebase's established Base UI pattern).
**Verified:** `tsc --noEmit` now passes clean; `/verify-email` renders and "Back to dashboard" navigates.

---

## Decisions — made by the user, now FIXED ✅

- **#2 — Light-mode toggle:** decision = **remove the toggle**. Done — removed from
  navbar (desktop + mobile) + unused import; app stays dark. type-check + lint green.
- **#4 — Password-change session:** decision = **enforce rule #26**. Done — password
  change now invalidates the session (forces re-login). **Bonus:** while fixing it I
  found `change_email` and `delete_account` had a latent bug — they set `delete_cookie`
  on the injected response but returned a *fresh* `Response(204)`, dropping the
  `Set-Cookie`, so they never actually cleared the cookie (their tests false-passed by
  manually clearing). All three fixed to mutate+return the injected response; added a
  real regression test. Backend suite: 1260 passed.

<details><summary>Original decision write-ups (for the record)</summary>

### #2 (medium) — Light-mode toggle is a no-op
The navbar "Toggle theme" button flips `html` to `light`, but `globals.css` defines `:root` identical to `.dark` with the comment `/* Always dark — the neon lime theme IS dark */`. So **the app is intentionally dark-only** and "light mode" does nothing visible.
**Decision needed:** (a) remove the toggle (honest, matches the explicit design), or (b) build a real light palette (a proper design effort — ~25 color tokens — that must not muddy the neon-lime brand). I recommend (a) unless light mode is a hard requirement.

### #4 (medium, security policy) — Password change doesn't invalidate the session
Hard rule #26 says account-mgmt routes (password/email/delete) MUST invalidate the session after the change. **Email change does** log out; **password change does NOT** (UI even says "you will remain logged in"). Current password IS required, so it's not insecure per se — but it deviates from the documented policy.
**Decision needed:** enforce rule #26 for password change (force re-login), or update rule #26 to allow staying logged in after a self-service password change.

</details>

---

## Retracted

- A suspected "inverted salary" on the job detail page was a **misread of a low-res screenshot** — the DB stores `50000-75000` for that job, matching the dashboard. No bug.

## Not exercised (would need real external services)

- Channel "Send test" (would POST to the fake Slack URL and fail by design).
- Email change / Delete account (would log out / destroy the test account).
- LinkedIn enrich (needs a LinkedIn PDF).

## Console noise (benign, not bugs)

- Dark-mode hydration mismatch (Next.js dev "Issue" badge); `GET /api/auth/me` 401 when logged out; `GET /api/profile` 404 for brand-new users. All expected.

---

## Files changed this sweep

- `frontend/src/lib/api-error.ts` — new `friendlyAuthError()` helper
- `frontend/src/app/(auth)/login/page.tsx`, `register/page.tsx` — use friendly errors
- `frontend/src/app/(auth)/verify-email/page.tsx` — `asChild` → Base UI `render` (fixes type-check)

Screenshots: `test-artifacts/sweep-*.png` (gitignored).
