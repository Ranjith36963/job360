# Job360 — Stack Upgrade Plan (Phases 1–3)

> **Goal.** Take Job360 from its current stack to the target stack in `STACK.md` / `STACK_checklist.md`, one phase at a time, each verified end-to-end before the next.
>
> **Method (every phase).**
> 1. **TDD** — write the failing test first, then the code, until green.
> 2. **Multi-agent** — Opus 4.8 designs + reviews; Sonnet subagents build in parallel where independent; delegate, then Opus verifies.
> 3. **Never break the suite** — 1,571 tests stay green; **Pillar-2 scoring/matcher is hands-off**; rule #3 (purge logic) needs confirmation.
> 4. **Verify end-to-end** — after each phase, run the full automated suite + a live smoke, then update all docs (`STATUS.md`, `STACK*.md`, `IMPLEMENTATION_LOG.md`).
> 5. **One phase = one branch → tested → committed → pushed.**

---

## 🧱 Phase 1 — Foundation (fully autonomous — no keys needed)
**Build:** SQLite → **PostgreSQL** · **Dockerfile** + prod `docker-compose` · **full CI gate** (pytest+ruff+mypy+build) · *(optional pgvector)*

**Steps (TDD):**
1. Add async Postgres driver (`asyncpg`), a DB-engine abstraction, and a Postgres test fixture in `conftest`.
2. Port `database.py` queries to Postgres-safe SQL (AUTOINCREMENT→SERIAL, `INSERT OR IGNORE`→`ON CONFLICT`, etc.), keeping behavior identical.
3. Port the 22 migrations to run on Postgres.
4. Run the full suite against Postgres → all green.
5. Write `Dockerfile` (backend) + `frontend/Dockerfile` + prod `docker-compose.yml` (backend + worker + frontend + Postgres + Redis); `docker compose up` runs clean.
6. Expand CI (`ci.yml`): pytest + ruff + mypy + `next build`, on PR + push.

**Exit:** full suite green on Postgres · `docker compose up` boots the whole app · CI green.
**💰 Cost: $0** (all local/free).

> ✅ **DONE 2026-07-01.** psycopg3 shim (`repositories/pg.py`), **1608 tests green on Postgres** (verified locally, 12m24s), app boots + register persists to `public.users`, backend+frontend Dockerfiles + `docker-compose.prod.yml` (5 services), full CI gate (`.github/workflows/ci.yml`), `arq` dep bug fixed, backup script. Branch `phase-1-postgres`.

## 🚀 Phase 2 — Go live (needs YOUR keys)
**Build:** Railway deploy · Domain · `/livez`+`/readyz` · env-validation-at-boot · DB backup script · worker+Redis live.

**Steps:** I build the code (health endpoints, env validation, backup script, Railway config) autonomously; then I pause **once** to get from you:
- 🔑 **Railway account / API token** (I can't sign up as you)
- 🔑 **Domain** (you buy it; give me the name)

Then I deploy, wire the domain, run a live smoke test, and continue.
**💰 Cost: ~$5–20/mo hosting + ~$12/yr domain** (Postgres+Redis on free tier).

## 📊 Phase 3 — Visibility (needs YOUR keys)
**Build:** Sentry · PostHog · uptime monitor · fill logging dark zones (workers/DB/auth) · security headers.

**Steps:** I build all the wiring autonomously; pause **once** for:
- 🔑 **Sentry DSN** · 🔑 **PostHog project key** (free-tier accounts you create)

Then I verify errors + events flow in, and finish.
**💰 Cost: $0** (all free tiers).

---

## Honest limit
I can build **100% of the code** for all three phases without stopping. The **only** pauses are when I physically need a real account I can't create as you: **Railway token, domain, Sentry DSN, PostHog key**. You hand those over inline; I continue immediately.

## Order & verification
Phase 1 → verify → docs → **Phase 2** → verify → docs → **Phase 3** → verify → docs. End-to-end automated tests (pytest + Playwright e2e) must pass at each phase gate.

*See also: `STACK.md`, `STACK_checklist.md`, `MONETIZATION_GAPS.md`.*
