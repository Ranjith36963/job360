# PLAN 2 — Unblock real sign-ups: production email delivery

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Rank: 2 of 5.**
Why: Job360 is LIVE on Railway (since 2026-07-02) with passwordless magic-link login — but Resend is in sandbox mode, which only delivers to the account owner's email (`rahulranjith369@gmail.com`). **No other human can log in to the live product.** Everything else (plans 3–5) matters less while the product has a maximum user count of one.

**Goal:** Any email address can receive a magic-link and log in on production; email-send failures are visible instead of silent; the undocumented email env vars are documented and tested.

**Architecture:** The code path is already correct and well-tested (token minting, single-use consume, no-enumeration 204s). The blocker is provider configuration plus three small code gaps: undocumented env vars, zero tests on the send-failure path, and a silent-failure UX. Most of Task 1 is a HUMAN step (DNS); everything after is code.

**Tech Stack:** Resend HTTP API (primary), smtplib fallback, httpx, FastAPI, pytest, Vitest.

---

## Verified facts (checked 2026-07-07 — trust these over docs)

- Sender: `backend/src/services/auth/email_sender.py`, function `send_system_email(...) -> bool` (never raises; returns False and logs a WARNING on failure).
- From-address logic (line ~53): `os.environ.get("SMTP_FROM") or os.environ.get("SMTP_EMAIL") or "onboarding@resend.dev"` — the fallback is Resend's shared sandbox sender.
- Resend key logic (lines ~60-62): reads `RESEND_API_KEY`; if unset, falls back to `SMTP_PASSWORD` **if it starts with `re_`**. Neither `RESEND_API_KEY` nor `SMTP_FROM` is declared in `backend/src/core/settings.py` or documented in `.env.example` — they are read ad hoc from `os.environ` only inside `email_sender.py`.
- SMTP fallback (lines ~88-125): used only when no Resend key is found; host from `os.environ.get("SMTP_HOST", "smtp.gmail.com")` — NOTE: `settings.py:60-62` also defines `SMTP_HOST`/`SMTP_PORT` constants but they are **dead code**, `email_sender.py` never reads them.
- Magic-link flow: `POST /api/auth/magic-link/request` (always 204; rate-limit 3/5min per email) → email link `{FRONTEND_ORIGIN}/auth/magic?token=...` → `POST /api/auth/magic-link/consume` (400 generic on invalid/expired/used; on success creates/reactivates user, sets `email_verified_at`, sets session cookie). Routes in `backend/src/api/routes/auth.py:502-573`; logic in `backend/src/services/auth/magic_link.py`; tokens table from migration `0022_magic_link_tokens`.
- `REQUIRE_EMAIL_VERIFICATION` env (default `"true"`, read in `backend/src/api/auth_deps.py:105-128`) gates `require_verified_user` routes; the code comment says it exists as an escape hatch "while Resend is in sandbox mode." It may currently be `false` on Railway.
- Existing tests: `backend/tests/test_magic_link.py` (8 tests, all monkeypatch `send_system_email` to return True — the failure path and the real provider path have ZERO coverage). Frontend: no test exists for `frontend/src/app/auth/magic/page.tsx`.
- Frontend magic pages: `frontend/src/app/(auth)/login/page.tsx` (MagicLinkForm shows "Check your email (and Spam)…" on 204 — even if the send actually failed) and `frontend/src/app/auth/magic/page.tsx` (consumes `?token=`, redirects to `/dashboard`).

## Files to touch

- Modify: `.env.example` (root)
- Modify: `backend/src/core/settings.py` (document + declare email vars; remove dead constants)
- Modify: `backend/src/services/auth/email_sender.py` (read settings-declared names consistently; error-level log on failure)
- Create: `backend/tests/test_email_sender.py`
- Modify: `backend/tests/test_magic_link.py` (add send-failure test)
- Create: `frontend/src/app/auth/magic/__tests__/magic-consume.test.tsx`
- HUMAN: Resend dashboard + DNS records + Railway env vars

---

### Task 1: Provider unblock — HUMAN STEP (do first; code tasks don't depend on it)

This cannot be done by an agent. Present it to the owner exactly like this:

**Option A (recommended, permanent): verify a domain in Resend.**
1. In the Resend dashboard → Domains → Add Domain (e.g. `job360.example` — any domain you own; if you own none, buy one first, ~£10/yr).
2. Add the DKIM/SPF DNS records Resend shows you at your DNS provider. Wait for "Verified".
3. On Railway (backend service) set:
   - `SMTP_FROM=Job360 <login@yourdomain>` ← must be on the verified domain, otherwise Resend rejects with 403
   - `RESEND_API_KEY=re_...` (a production key, not the sandbox one)
4. Redeploy the backend service.

**Option B (stopgap, works today, no domain needed): Gmail SMTP fallback.**
1. Create a Gmail app password (https://myaccount.google.com/apppasswords).
2. On Railway set: `SMTP_EMAIL=<your gmail>`, `SMTP_PASSWORD=<app password>`, `SMTP_FROM=<your gmail>`, and **REMOVE any `re_`-prefixed value from `SMTP_PASSWORD` and remove/blank `RESEND_API_KEY`** — if a `re_` key is visible anywhere, the code takes the Resend path and Gmail is never used (see Verified facts).
3. Redeploy. Deliverability is worse than Option A (may land in spam) but any address can receive links.

**Either way, afterwards:** if `REQUIRE_EMAIL_VERIFICATION=false` was set on Railway as the sandbox escape hatch, set it back to `true` once delivery works.

### Task 2: Document + declare the email env vars

- [ ] **Step 2.1: Read the whole sender first** (`backend/src/services/auth/email_sender.py`) — the exact env names and fallback order in "Verified facts" must match what you see; if the file has changed, adapt.

- [ ] **Step 2.2: Add the missing entries to `.env.example`** in the existing "Email notifications" section (keep the existing SMTP_* lines):

```
# === Transactional email (magic-link login, verification, password reset) ===
# Preferred: Resend (https://resend.com). Key must be a production key and
# SMTP_FROM must be an address on a domain verified in Resend.
RESEND_API_KEY=
SMTP_FROM=
```

- [ ] **Step 2.3: Declare them in `backend/src/core/settings.py`** next to the existing SMTP block, and delete the two dead constants:

```python
# Transactional email (magic-link / verification). email_sender.py prefers
# Resend when RESEND_API_KEY is set; otherwise falls back to SMTP.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
SMTP_FROM = os.getenv("SMTP_FROM", "")
```

Remove `SMTP_HOST = "smtp.gmail.com"` and `SMTP_PORT = 587` ONLY IF a repo-wide grep proves nothing imports them:

```bash
cd backend && grep -rn "SMTP_HOST\|SMTP_PORT" src/ tests/ | grep -v email_sender
```

If anything else imports them, leave them and add a comment `# NOTE: email_sender.py reads SMTP_HOST/SMTP_PORT from os.environ directly; these constants are not used by it.`

- [ ] **Step 2.4: Keep `email_sender.py` behavior identical** but make it read the same names (it already does — this step is verification only). Do NOT refactor it to import from settings (settings is loaded at import time; tests monkeypatch `os.environ` — changing the read location breaks the monkeypatch pattern).

- [ ] **Step 2.5: Run the env-example parity gate + commit:**

```bash
cd backend && python scripts/check_env_example.py
git add -A && git commit -m "docs(email): declare RESEND_API_KEY + SMTP_FROM; remove dead SMTP constants"
```

Expected: gate passes. If it complains about other undocumented vars, fix only the ones this plan added; report the rest.

### Task 3: Make send failures loud (log level + tests)

- [ ] **Step 3.1: Failing test first.** Create `backend/tests/test_email_sender.py`:

```python
"""Send-failure paths of send_system_email — previously untested."""
import pytest

from src.services.auth import email_sender


class _FakeResponse:
    status_code = 500
    text = "internal error from resend"


class _FakeAsyncClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return _FakeResponse()


@pytest.mark.asyncio
async def test_resend_5xx_returns_false_and_logs_error(monkeypatch, caplog):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.delenv("SMTP_EMAIL", raising=False)
    monkeypatch.setattr(email_sender.httpx, "AsyncClient", _FakeAsyncClient)
    ok = await email_sender.send_system_email(
        to_email="someone@example.com", subject="s", body_text="b"
    )
    assert ok is False
    assert any(r.levelname == "ERROR" for r in caplog.records), (
        "delivery failure must be logged at ERROR so it is visible in Sentry/Railway logs"
    )


@pytest.mark.asyncio
async def test_no_provider_configured_returns_false(monkeypatch):
    for var in ("RESEND_API_KEY", "SMTP_PASSWORD", "SMTP_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    ok = await email_sender.send_system_email(
        to_email="someone@example.com", subject="s", body_text="b"
    )
    assert ok is False
```

Before running: READ `email_sender.py` and adjust the patch target if httpx is imported differently (e.g., `from httpx import AsyncClient` would need `monkeypatch.setattr(email_sender, "AsyncClient", ...)`). Also check what the current no-provider behavior is — if it currently attempts SMTP to smtp.gmail.com with empty creds, the second test may need `monkeypatch.setattr` on the SMTP helper instead; the assertion `ok is False` is the contract.

```bash
cd backend && python -m pytest tests/test_email_sender.py -v -p no:randomly
```

Expected: the ERROR-level assertion FAILS (current code logs WARNING).

- [ ] **Step 3.2: Minimal fix.** In `email_sender.py`, change the failure logs (Resend non-2xx, Resend exception, SMTP exception) from `logger.warning(...)` to `logger.error(...)`. Do not change return values or add raises — callers rely on the never-raises contract.

- [ ] **Step 3.3: Add the magic-link-specific failure test** to `backend/tests/test_magic_link.py` (follow the file's existing fixture style — it has a `_no_real_email` autouse-style fixture; this test overrides the send to fail):

```python
async def test_request_returns_204_even_when_send_fails(client, monkeypatch):
    """Delivery failure must not leak email-existence (204 always) but the
    token row is still minted, so a re-send after fixing the provider works."""
    async def _failing_send(**kwargs):
        return False

    monkeypatch.setattr(
        "src.services.auth.magic_link.send_system_email", _failing_send
    )
    resp = await client.post(
        "/api/auth/magic-link/request", json={"email": "fail@example.com"}
    )
    assert resp.status_code == 204
```

Adapt the client fixture name/signature to match the existing tests in that file (READ the file first — it may use a sync TestClient; copy the shape of `test_request_mints_token_and_returns_204`).

- [ ] **Step 3.4: Run + commit:**

```bash
cd backend && python -m pytest tests/test_email_sender.py tests/test_magic_link.py -v -p no:randomly
git add -A && git commit -m "test(email): cover send-failure paths; log delivery failures at ERROR"
```

### Task 4: Frontend test for the consume page

- [ ] **Step 4.1:** READ `frontend/src/app/auth/magic/page.tsx` fully, then create `frontend/src/app/auth/magic/__tests__/magic-consume.test.tsx` following the pattern of the existing `frontend/src/app/(auth)/login/__tests__/login-redirect.test.tsx` (same mocking style for `next/navigation` and `@/lib/api`). Cover three cases:
  1. No `?token=` param → error state shown, `consumeMagicLink` NOT called.
  2. `consumeMagicLink` resolves → `router.replace("/dashboard")` called.
  3. `consumeMagicLink` rejects → error message + link back to `/login` rendered.

Use the login test's exact mock setup for `useSearchParams`/`useRouter` — do not invent a new mocking approach.

- [ ] **Step 4.2: Run + commit:**

```bash
cd frontend && npm run test:unit
git add -A && git commit -m "test(frontend): cover magic-link consume page states"
```

### Task 5: Live verification + ship

- [ ] **Step 5.1: Local end-to-end sanity** (backend running locally, `python main.py` from `backend/`): POST a magic-link request and confirm a token row appears:

```bash
curl -s -X POST http://localhost:8000/api/auth/magic-link/request -H "Content-Type: application/json" -d "{\"email\":\"local-test@example.com\"}" -i | head -1
```

Expected: `HTTP/1.1 204 No Content`. Check backend logs — with no local provider configured you should now see an ERROR-level delivery-failure line (that's the new visibility working).

- [ ] **Step 5.2: Push + PR:**

```bash
git push -u origin feat/unblock-real-signups
gh pr create --title "feat(email): production email readiness — env docs, failure visibility, tests" --body "Code half of the sign-up unblock. HUMAN half (Resend domain verify or Gmail stopgap + Railway vars) documented in PLAN-2-unblock-real-signups.md Task 1. After Railway vars are set: send a magic link to a NON-owner address on prod and confirm receipt + login."
```

- [ ] **Step 5.3: HUMAN verification on prod (after Task 1 done):** request a magic link for an address that is NOT the Resend account owner's, receive it, click it, land on `/dashboard`. Then confirm `REQUIRE_EMAIL_VERIFICATION` is back to `true` on Railway.

---

## Edge cases a weaker model would miss

1. **The `re_` smuggling quirk:** a Resend key can arrive via `SMTP_PASSWORD` (if it starts with `re_`). If the owner picks Option B (Gmail), leaving the old `re_` value in `SMTP_PASSWORD` silently keeps routing through sandbox Resend. The stopgap instructions must explicitly remove it.
2. **`SMTP_FROM` must match the verified domain.** With Resend, a from-address outside the verified domain gets a 403 — and because `send_system_email` never raises, the user sees "Check your email" while nothing was sent. This is why Task 3's ERROR-level logging matters.
3. **`settings.SMTP_HOST`/`SMTP_PORT` are decoys.** They exist in `settings.py` but `email_sender.py` reads `os.environ` directly. Editing settings.py to "configure SMTP" does nothing.
4. **Don't refactor `email_sender.py` to import from settings.** Settings values freeze at import time; the existing tests (and this plan's tests) monkeypatch `os.environ` at call time. Keep the ad hoc reads.
5. **No-enumeration is a security property, not sloppiness.** The request endpoint must return 204 on unknown emails, rate-limited requests AND send failures alike. Do not "improve" it to return an error when the send fails — that leaks provider state and invites email-existence probing. Failure visibility belongs in logs/Sentry, not in the HTTP response.
6. **`REQUIRE_EMAIL_VERIFICATION` re-enable:** if it stays `false` after email works, every route guarded by `require_verified_user` is open to unverified accounts. The flag flip is part of DONE for this plan.
7. **Rate limit in manual testing:** 3 requests per 5 minutes per email. If prod testing "stops working" after 3 tries, that's the limiter (still 204, silently no token) — wait 5 minutes, don't debug.
8. **Magic-link URL uses `FRONTEND_ORIGIN`'s first entry.** If Railway's `FRONTEND_ORIGIN` has multiple comma-separated origins, the link is built from the first — make sure the production frontend URL is first, or links point at localhost.

## Acceptance criteria

- [ ] `python scripts/check_env_example.py` passes; `.env.example` documents `RESEND_API_KEY` + `SMTP_FROM`.
- [ ] `python -m pytest tests/test_email_sender.py tests/test_magic_link.py -p no:randomly` → all pass, including the new failure-path tests.
- [ ] Full backend suite green: `python -m pytest -q -p no:randomly` (one run).
- [ ] `npm run test:unit` green including the new magic-consume tests.
- [ ] A delivery failure produces an ERROR-level log line (verify in Step 5.1).
- [ ] PROD: a non-owner email receives a magic link and can log in end-to-end (human-verified).
- [ ] PROD: `REQUIRE_EMAIL_VERIFICATION=true` on Railway.

## STOP conditions

- `email_sender.py` on main no longer matches the "Verified facts" shape (someone changed the provider logic) — re-map before editing.
- The owner has no domain and rejects both options in Task 1 — code tasks still land, but say clearly the product remains single-user until Task 1 happens.
