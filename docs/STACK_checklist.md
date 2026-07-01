# Job360 — Stack Implementation Checklist (code-audited)

> **Live checklist.** Every ✅ is backed by a real file as proof (code audit, 2026-06-30). Every ❌ = no code found → to implement. Tick items as you build.
>
> **Companion to** `STACK.md` (the have-vs-add overview). This file is the *evidence*.

**Score: 27 implemented ✅ · 21 to build ❌** (build/ops + business layers).

---

## 🖥️ Backend
| ✓ | Item | Proof (code) |
|---|---|---|
| ✅ | FastAPI app | `backend/src/api/main.py` |
| ✅ | ARQ worker (background queue) | `backend/src/workers/settings.py` |
| ✅ | ARQ tasks + crons | `backend/src/workers/tasks.py` |

## 🗄️ Database
| ✓ | Item | Proof / To build |
|---|---|---|
| ✅ | SQLite (async) | `backend/src/repositories/database.py` |
| ✅ | 22 migrations (0000→0021) | `backend/migrations/0021_add_job_deadline.up.sql` |
| ✅ | ChromaDB vector store | `backend/src/services/retrieval.py`, `vector_index.py` |
| ❌ | **PostgreSQL** | *to build — replace SQLite for concurrent writes* |
| ❌ | pgvector (optional) | *to build — fold vectors into Postgres* |

## 🎨 Frontend
| ✓ | Item | Proof (code) |
|---|---|---|
| ✅ | Next.js 16 + React 19 | `frontend/src/app/layout.tsx` |
| ✅ | shadcn/ui components | `frontend/src/components/ui/` |
| ✅ | Tailwind CSS 4 | `frontend/postcss.config.mjs` |

## 🤖 AI / LLM
| ✓ | Item | Proof / To build |
|---|---|---|
| ✅ | Multi-provider LLM layer (Claude/OpenAI/Gemini/Groq/Cerebras) | `backend/src/services/llm_provider.py` |
| ❌ | **LLM cost tracking** | *to build — `llm_usage` table + $/user* |

## 🔐 Auth
| ✓ | Item | Proof (code) |
|---|---|---|
| ✅ | argon2 password hashing | `backend/src/services/auth/passwords.py` |
| ✅ | Sessions | `backend/src/services/auth/sessions.py` |
| ✅ | Signed cookies (itsdangerous) | `backend/src/services/auth/sessions.py` |
| ✅ | Auth rate-limit (sliding-window) | `backend/src/services/auth/rate_limit.py` |

## 💳 Payments / Plans
| ✓ | Item | Proof / To build |
|---|---|---|
| ❌ | **Stripe** | *to build — no code (companies.py "stripe" = the company)* |
| ❌ | **Plan tiers + usage quota** | *to build — no tier column, no quota enforcement* |
| ❌ | **Pricing page** | *to build — no `frontend/src/app/pricing`* |

## 📧 Email / Notifications
| ✓ | Item | Proof / To build |
|---|---|---|
| ✅ | SMTP email sender | `backend/src/services/auth/email_sender.py` |
| ✅ | apprise multi-channel dispatcher | `backend/src/services/channels/dispatcher.py` |
| ✅ | Fernet channel-credential encryption | `backend/src/services/channels/crypto.py` |
| ❌ | **SES / Resend** (prod transactional email) | *to build — still Gmail SMTP* |
| ⚠️ | Notifications firing in prod | *code done; needs Redis+worker deployed* |

## 📊 Logging / Monitoring
| ✓ | Item | Proof / To build |
|---|---|---|
| ✅ | Logger + correlation IDs + `audit.log` | `backend/src/utils/logger.py` |
| ⚠️ | Full log coverage | *dark zones: workers, DB, auth, notifications = 0 logs* |
| ❌ | **Sentry** (error tracking) | *to build — no code* |
| ❌ | **PostHog** (product analytics) | *to build — no code* |

## 🚀 Infra / Deploy
| ✓ | Item | Proof / To build |
|---|---|---|
| ✅ | docker-compose (dev) | `docker-compose.dev.yml` |
| ✅ | CI (offline suite + e2e) | `.github/workflows/ci-offline.yml`, `live-e2e.yml` |
| ✅ | `/health` route | `backend/src/api/routes/health.py` |
| ❌ | **Dockerfile** | *to build — no container image* |
| ❌ | **docker-compose (prod)** | *to build* |
| ❌ | **Deploy config (Railway)** | *to build — local only* |
| ❌ | `/livez` + `/readyz` split | *to build* |
| ❌ | Env validation at boot | *to build — no fail-fast on missing secrets* |
| ❌ | DB backup script | *to build* |
| ❌ | Domain | *to buy* |

## ⚖️ Legal / Product
| ✓ | Item | Proof / To build |
|---|---|---|
| ✅ | Privacy + Terms pages | `frontend/src/app/privacy/`, `terms/` |
| ✅ | GDPR account delete | `backend/src/api/routes/auth.py` |
| ❌ | ICO registration | *your action (£40)* |
| ❌ | Guided onboarding flow | *to build — basic register→CV→search only* |
| ❌ | Support / contact + help | *to build* |

## 🧪 Testing & cross-cutting (added after deeper audit)
| ✓ | Item | Proof / To build |
|---|---|---|
| ✅ | pytest suite (1,571 tests, 111 files) | `backend/tests/` |
| ✅ | Frontend unit + Playwright e2e | `frontend/` vitest + Playwright specs |
| ✅ | Redis broker (for ARQ) | `backend/src/workers/settings.py` (`REDIS_URL`) |
| ✅ | Feature flags (env toggles) | `backend/src/core/settings.py` (`SEMANTIC_/ENRICHMENT_/MATCHER_ENABLED`) |
| ✅ | CORS | `backend/src/api/main.py` (CORSMiddleware) |
| ❌ | **Security headers** (CSP/HSTS/X-Frame) | *to build — no code* |
| ❌ | **API-wide rate limiting** | *to build — auth-only today* |
| ⚠️ | File storage for CV uploads | *CVs go to a temp file (`save_upload_to_temp` in `api/dependencies.py`), parsed, then discarded — not persisted; add S3/R2 only if keeping raw files* |

---

## Build order (the ❌ that matter most)
1. **PostgreSQL** → `docker-compose(prod)` → **Dockerfile** → **Railway deploy** — ship it live.
2. **Stripe + plan tiers + quota + pricing page** — charge money, cap AI cost.
3. **LLM cost tracking** — protect margins.
4. **SES/Resend email**, **Sentry**, **PostHog** — reliability + growth.
5. **ICO + domain** — legal + reachable.

*See also: `STACK.md`, `MONETIZATION_GAPS.md`, `LAUNCH_PLAN.md`.*
