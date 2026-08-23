# PLAN 5 — Free/Premium plan gating, Phase 1 (no payments)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Rank: 5 of 5.**
Why: monetization needs a plan system before it needs a payment system — the `users.plan` column, the `require_premium` dependency, and the plan-aware UI are the foundation Stripe later plugs into. It ranks last only because plans 1–4 protect what already exists; this one builds new surface. It is deliberately Phase-1-only: **no Stripe, no billing, no trial clock** — a text column, a dependency, one real gated feature (CV tailoring), and visible plan state in the UI. The richer Free/Pro/Max + 7-day-taste model (decided in the PR #22 pricing docs) layers on top later; the `plan` column being TEXT means `'pro'`/`'max'` fit without another migration.

**Goal:** every user has a `plan` (`free` default); premium users bypass the tailor monthly cap; `/api/auth/me` and the account page show the plan; free users hitting a premium wall get a clean 402 the frontend explains.

**Architecture:** Migration 0025 adds the column → `CurrentUser` carries it → `require_premium` dependency (mirrors `require_verified_user`) → wire into the existing tailor 402 quota gate → expose via `UserResponse` → frontend `User` type + account-page plan card.

**Tech Stack:** SQL migration (SQLite-dialect, auto-translated to Postgres), FastAPI dependencies, pytest, React/Next.js 16, Vitest.

---

## Verified facts (checked 2026-07-07)

- `users` columns today: `id, email, password_hash, created_at, deleted_at, timezone, email_verified_at`. No plan/premium/tier concept exists ANYWHERE in backend or frontend code (grepped — only forward-looking comments).
- Highest migration: `0024_tailored_flagged_terms` → this plan creates **0025**. Naming: `NNNN_name.up.sql` + `NNNN_name.down.sql`, discovered by glob, tracked in `_schema_migrations`, auto-applied on API boot (`backend/src/api/dependencies.py:11-22`).
- **Migrations are written in SQLite dialect but ONLY ever run against Postgres** — `backend/src/repositories/pg.py:translate()` rewrites them (`ADD COLUMN` → `ADD COLUMN IF NOT EXISTS`, `?` → `%s`, etc.). Copy the style of `0024_tailored_flagged_terms.up.sql`. Do NOT write Postgres-native SQL.
- `CurrentUser` is a FROZEN dataclass in `backend/src/api/auth_deps.py:45-49` (`id`, `email`, `email_verified`), built in `_current_user_from_cookie` (`auth_deps.py:52-76`) from `SELECT id, email, email_verified_at FROM users WHERE id = ? AND deleted_at IS NULL`.
- The dependency to mirror: `require_verified_user` (`auth_deps.py:105-128`) — it takes `user: CurrentUser = Depends(require_user)` and raises on a missing property. Follow that exact shape.
- The first real premium feature already has a seam: `backend/src/api/routes/tailor.py:105-125` enforces a monthly cap (`TAILOR_FREE_PER_MONTH`, default 10, from `backend/src/core/settings.py:141`) returning **402** — with a comment saying a real premium tier will bypass it later. Phase 1 = premium bypasses the cap. (Do NOT convert tailor to `require_premium`-only — free users keep their 10/month.)
- `GET /api/auth/me` → `backend/src/api/routes/auth.py:226-228`, returns `UserResponse` (`auth.py:85-87`: `id`, `email`). `UserResponse` is ALSO returned by register/login/magic-consume — adding a field touches all their code paths; give it a safe default.
- Frontend: `User` type is HAND-WRITTEN at `frontend/src/lib/api.ts:340` (`{ id: string; email: string }`) — not generated; `me()` at `api.ts:362-368`. Account page: `frontend/src/app/settings/account/page.tsx` (composes cards; add a PlanCard). `frontend/src/middleware.ts:42` also fetches `/api/auth/me` — additive field is safe there.
- Test pattern to copy for gating tests: `backend/tests/test_email_enforcement.py` (free vs gated route, per the original plan doc `docs/product/plans/2026-06-21-free-premium-plans.md`).
- Rule #12/#25 (CLAUDE.md): plan must derive from the session user server-side. NEVER accept a plan value from a request body/URL — no self-upgrade endpoint in Phase 1.

## Files to touch

- Create: `backend/migrations/0025_user_plan.up.sql`, `backend/migrations/0025_user_plan.down.sql`
- Modify: `backend/src/api/auth_deps.py` (CurrentUser + SELECT + `require_premium`)
- Modify: `backend/src/api/routes/auth.py` (`UserResponse.plan` + constructor sites)
- Modify: `backend/src/api/routes/tailor.py` (premium bypasses the monthly cap)
- Create: `backend/tests/test_plan_gating.py`
- Create: `backend/scripts/set_plan.py` (owner backfill tool)
- Modify: `frontend/src/lib/api.ts` (User type), `frontend/src/app/settings/account/page.tsx` (PlanCard)
- Create: PlanCard test alongside existing account-page tests

---

### Task 0: Preflight

- [ ] **Step 0.1:**

```bash
git fetch origin main
git checkout -b feat/plan-gating-phase1 origin/main
git status --porcelain   # must be empty
```

### Task 1: Migration 0025

- [ ] **Step 1.1: Create `backend/migrations/0025_user_plan.up.sql`:**

```sql
-- 0025 — subscription plan, Phase 1 (no payments yet).
-- TEXT so future tiers ('pro', 'max') need no further migration.
-- Every existing and new user defaults to 'free'; upgrades happen server-side
-- only (scripts/set_plan.py today, Stripe webhook later).
-- IF NOT EXISTS keeps the runner idempotent (pg.py translate guarantees it anyway).
ALTER TABLE users ADD COLUMN IF NOT EXISTS plan TEXT NOT NULL DEFAULT 'free';
```

- [ ] **Step 1.2: Create `backend/migrations/0025_user_plan.down.sql`:**

```sql
-- Reverse 0025
ALTER TABLE users DROP COLUMN IF EXISTS plan;
```

- [ ] **Step 1.3: Apply + verify round-trip:**

```bash
cd backend
python -m migrations.runner up
python -m migrations.runner status    # 0025 listed as applied
python -m migrations.runner down      # reverses 0025
python -m migrations.runner up        # re-applies
```

Expected: no errors; status shows 0025 applied at the end.

- [ ] **Step 1.4: Commit:** `git add backend/migrations && git commit -m "feat(plans): migration 0025 — users.plan column, default free"`

### Task 2: Backend — CurrentUser + require_premium (TDD)

- [ ] **Step 2.1: Failing tests first.** Create `backend/tests/test_plan_gating.py`. READ `backend/tests/test_email_enforcement.py` FIRST and copy its fixture/client/user-creation style exactly (do not invent a new setup). The tests to write:

```python
"""Phase-1 plan gating: free vs premium behavior. Fixture style follows
test_email_enforcement.py — copy its client/user setup verbatim."""

# 1. test_me_includes_plan_free_by_default
#    Register/login a fresh user → GET /api/auth/me → body["plan"] == "free".

# 2. test_require_premium_returns_402_for_free_user
#    Add a throwaway premium-only route to the test app (or use the dependency
#    directly with FastAPI's dependency_overrides testing pattern):
#    free user → 402, body detail mentions upgrading, body["code"] == "premium_required".

# 3. test_require_premium_passes_for_premium_user
#    Same route; UPDATE users SET plan='premium' WHERE id=? via the test db
#    fixture → 200.

# 4. test_tailor_cap_bypassed_for_premium
#    Copy the existing tailor-quota test from tests (grep: TAILOR_FREE_PER_MONTH
#    or "402" in backend/tests/test_tailor*.py), set the user premium, exhaust
#    past the cap → still 200/201, never 402.

# 5. test_plan_cannot_be_set_from_request
#    POST /api/auth/register with an extra "plan": "premium" field in the JSON
#    → user's row in DB still has plan='free' (extra fields ignored, never honored).
```

Write them as real tests (the comments above are the spec, not placeholders to leave in). Run: `cd backend && python -m pytest tests/test_plan_gating.py -v -p no:randomly` — expected: all FAIL (plan field doesn't exist yet).

- [ ] **Step 2.2: Extend `CurrentUser` + the SELECT** in `backend/src/api/auth_deps.py`:

```python
@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str
    email_verified: bool = False
    plan: str = "free"
```

In `_current_user_from_cookie`, change the query to `SELECT id, email, email_verified_at, plan FROM users WHERE id = ? AND deleted_at IS NULL` and pass `plan=row["plan"]` to the constructor. **Then grep for every other `CurrentUser(` constructor call** (`cd backend && grep -rn "CurrentUser(" src/ tests/`) — the default `plan="free"` keeps them compiling, but any test that builds a premium user must pass `plan="premium"` explicitly.

- [ ] **Step 2.3: Add `require_premium`** in `auth_deps.py`, directly below `require_verified_user`, mirroring its exact shape:

```python
async def require_premium(
    user: CurrentUser = Depends(require_user),
) -> CurrentUser:
    """Gate for premium-only routes. Free users get 402 Payment Required.

    Phase 1: plan is a plain column ('free'/'premium'), set server-side only.
    """
    if user.plan != "premium":
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="This feature needs a premium plan. Upgrade to unlock it.",
        )
    return user
```

If `require_verified_user` attaches a machine-readable `code` to its error body (READ it — it may use a custom exception or headers), replicate that mechanism with `code="premium_required"` so the frontend can branch on it.

- [ ] **Step 2.4: Wire the tailor cap bypass** in `backend/src/api/routes/tailor.py` — find the monthly-cap check (~line 105-125) and short-circuit it:

```python
if user.plan != "premium":
    # existing monthly-cap logic stays exactly as-is, one indent deeper
    ...
```

Keep the route's existing `require_verified_user` dependency untouched. Update the code comment that said premium would bypass "later" — it's now.

- [ ] **Step 2.5: Expose the plan** in `backend/src/api/routes/auth.py`: add `plan: str = "free"` to `UserResponse`, then update EVERY place that constructs `UserResponse(...)` (grep `UserResponse(` in the file — register, login, me, magic-consume) to pass `plan=user.plan` where a `CurrentUser` is in hand; for freshly-created users pass `"free"` literally (or re-read the row).

- [ ] **Step 2.6: Run the new tests, then the full suite:**

```bash
cd backend
python -m pytest tests/test_plan_gating.py -v -p no:randomly
python -m pytest -q -p no:randomly     # one run; fresh shell if you already ran a full suite
git add -A && git commit -m "feat(plans): users.plan on CurrentUser, require_premium (402), premium bypasses tailor cap"
```

### Task 3: Owner backfill tool

- [ ] **Step 3.1: Create `backend/scripts/set_plan.py`** (mirrors the style of other scripts in that folder — plain argparse + the pg-backed DB):

```python
"""Set a user's plan by email. Phase-1 admin tool (no billing yet).

Usage (from backend/):
    python scripts/set_plan.py user@example.com premium
    python scripts/set_plan.py user@example.com free
"""
import argparse
import asyncio
import sys

from src.repositories import pg as aiosqlite  # established Postgres-shim alias
from src.core.settings import DB_PATH

VALID_PLANS = ("free", "premium")


async def set_plan(email: str, plan: str) -> int:
    if plan not in VALID_PLANS:
        print(f"invalid plan {plan!r}; choose from {VALID_PLANS}")
        return 2
    db = await aiosqlite.connect(str(DB_PATH))
    try:
        cur = await db.execute(
            "UPDATE users SET plan = ? WHERE email = ? AND deleted_at IS NULL",
            (plan, email.lower()),
        )
        if getattr(cur, "rowcount", 0) == 0:
            print(f"no active user with email {email!r}")
            return 1
        print(f"{email} -> {plan}")
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("email")
    p.add_argument("plan", choices=VALID_PLANS)
    a = p.parse_args()
    sys.exit(asyncio.run(set_plan(a.email, a.plan)))
```

Before committing: READ one existing script that opens the DB (e.g. `backend/scripts/dump_db.py`) and match ITS connect/close idiom exactly — the snippet above is the intent; the local idiom (row factory, connect signature, `DB_PATH` usage) wins on any mismatch. Test it locally: set your dev user premium, hit `/api/auth/me`, see `"plan":"premium"`, set back to free.

- [ ] **Step 3.2: Commit:** `git add backend/scripts/set_plan.py && git commit -m "feat(plans): set_plan.py owner tool"`

### Task 4: Frontend — show the plan

- [ ] **Step 4.1: Type + API:** in `frontend/src/lib/api.ts` change line ~340 to `export type User = { id: string; email: string; plan: "free" | "premium" };`. Run `npm run type-check` — fix any site that constructs a `User` literal (tests/mocks) by adding `plan: "free"`.

- [ ] **Step 4.2: PlanCard on the account page.** In `frontend/src/app/settings/account/page.tsx`, add a `PlanCard` component ABOVE the existing cards, following the exact card/styling pattern of `VerifyEmailCard` in the same file (READ it first; reuse its shadcn Card primitives and data-fetch approach — it already has access to the current user via the page's existing `me()`/query usage):
  - Shows "Your plan: Free" or "Your plan: Premium" (badge style consistent with existing badges).
  - Free state: one line on what Premium unlocks today ("Unlimited CV tailoring — free plan includes 10/month") + a disabled/`mailto:` "Upgrade" button labeled "Upgrade (coming soon)". NO fake checkout.
  - Premium state: "Premium — thanks for supporting Job360."

- [ ] **Step 4.3: 402 UX check:** `frontend/src/components/tailor/TailorPanel.tsx:143` already renders the 402 quota message. Verify its message still reads correctly now that 402 can mean "cap reached (free plan)" — if the backend detail string changed in Task 2.4, align this copy.

- [ ] **Step 4.4: Component test** for PlanCard (both states) following the test style of the existing account-page tests (find them: `cd frontend && ls src/app/settings/account/__tests__ 2>/dev/null || grep -rl "VerifyEmailCard" src --include=*.test.tsx`).

- [ ] **Step 4.5: Verify + commit:**

```bash
cd frontend
npm run type-check && npm run lint && npm run test:unit
git add -A && git commit -m "feat(plans): plan display on account page + User.plan type"
```

### Task 5: End-to-end verification + ship

- [ ] **Step 5.1:** Backend + frontend running locally: fresh user → account page shows "Free"; `python scripts/set_plan.py <email> premium` → refresh → shows "Premium"; tailor a CV more than `TAILOR_FREE_PER_MONTH` times as premium (set the env to `1` locally to make this cheap: `TAILOR_FREE_PER_MONTH=1`) → no 402.
- [ ] **Step 5.2:**

```bash
git push -u origin feat/plan-gating-phase1
gh pr create --title "feat(plans): Phase 1 free/premium gating — users.plan, require_premium, tailor-cap bypass, account-page plan card" --body "Phase 1 of docs/product/plans/2026-06-21-free-premium-plans.md. No payments; plan set server-side via scripts/set_plan.py. Premium currently unlocks: unlimited tailoring. TEXT column leaves room for the Free/Pro/Max model (PR #22 docs) without another migration. Prod migration 0025 auto-applies on deploy boot."
```

---

## Edge cases a weaker model would miss

1. **Write the migration in SQLite dialect.** It LOOKS wrong (`IF NOT EXISTS` on ADD COLUMN isn't SQLite syntax) but the pg.py translator is the only thing that ever executes it, and 0024 sets the precedent. Postgres-native syntax elsewhere in the file (e.g. `::text` casts) would break the translator's assumptions.
2. **`CurrentUser` is frozen.** You can't mutate `user.plan` in a test — construct a new instance or update the DB row. Tests that try `user.plan = "premium"` fail with `FrozenInstanceError`.
3. **`UserResponse` is shared by four flows** (register/login/me/magic-consume). Adding the field with a default keeps old constructor calls compiling — but then `/me` silently returns `"free"` for premium users if you forget to thread `user.plan` through. Test #1 catches me; grep catches the rest. This is exactly rule #21 (value-presence over schema-presence): assert the VALUE flips after `set_plan.py`, not just that the key exists.
4. **402 vs 403 semantics:** the codebase already chose 402 for the tailor quota; `require_premium` must also use 402 so `frontend/src/lib/api-error.ts` and TailorPanel treat both walls the same way. Don't "correct" it to 403.
5. **No self-service upgrade in Phase 1** — an endpoint that lets the client set its own plan is a self-upgrade IDOR (rules #12/#25). Upgrades happen via `scripts/set_plan.py` only. Test #5 pins this.
6. **Migration auto-applies on prod boot.** Merging this PR migrates the LIVE database on next deploy. `ADD COLUMN ... DEFAULT 'free'` on Postgres 11+ is metadata-only (no table rewrite, no lock pain) — safe — but mention it in the PR body so the deploy isn't a surprise.
7. **`middleware.ts` fetches `/me` too** (`frontend/src/middleware.ts:42`) — it only checks auth, so the extra field is harmless, but if type-check complains there, the fix is the `User` type, not a second type.
8. **The tailor bypass must keep the LEDGER working**: if the cap code also RECORDS usage (read the block carefully), premium users should still record usage (for future analytics/limits) — only the REJECTION is bypassed. Don't skip the whole block unless it's purely a check.
9. **Frontend `User` mocks:** vitest mocks constructing `{ id, email }` will fail type-check after Step 4.1 — that's the point; fix each mock with `plan: "free"` rather than loosening the type.

## Acceptance criteria

- [ ] `python -m migrations.runner status` shows 0025 applied; down/up round-trip works (Step 1.3).
- [ ] `python -m pytest tests/test_plan_gating.py -p no:randomly` → 5 passed.
- [ ] Full backend suite green (one run, fresh shell).
- [ ] `GET /api/auth/me` returns `"plan": "free"` for a fresh user and `"plan": "premium"` after `scripts/set_plan.py` (value-presence, rule #21).
- [ ] Free user over the tailor cap → 402; premium user over the cap → success.
- [ ] Register with `"plan": "premium"` injected in the body → DB row still `free`.
- [ ] Account page renders the correct plan in both states; `npm run type-check`, `lint`, `test:unit` all green.

## STOP conditions

- `auth_deps.py` or the tailor quota block no longer matches the Verified facts (someone landed plan work first — check `git log --oneline origin/main -- backend/src/api/auth_deps.py` before assuming).
- You find yourself designing trial clocks, Stripe hooks, or per-tier source gating — that's Phase 2 / the PR #22 model; Phase 1 ends at the acceptance criteria above.
