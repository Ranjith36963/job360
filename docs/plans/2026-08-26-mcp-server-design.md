# Job360 MCP Server — Design

**Date:** 2026-08-26
**Status:** Design approved by owner. Not yet implemented.
**Verified against:** `origin/main` @ `9b6cfba` — confirmed via `railway status` to be the
commit production is running. Every file:line below is from that commit, not a feature branch.

---

## 1. What this is, in one paragraph

An MCP server that lets a Job360 user connect their account to Claude (and any other MCP
client) and ask for their jobs in plain language — *"any new London Python roles above 75
this week?"*, *"write me a cover letter for the Monzo one"*, *"move it to interview stage"*.
It lives inside the existing FastAPI backend, exposes Job360's already-built per-user
capabilities as MCP tools/prompts/resources, and is kept in lockstep with the SaaS by a
generated manifest that fails CI when it drifts.

**It is not new capability.** Almost everything it exposes already exists behind
`Depends(require_user)`. This is a second front door onto a house that is already built —
plus a small number of doors the UI never opened (see §10).

---

## 2. Locked decisions

| # | Decision | Choice | Note |
|---|---|---|---|
| 1 | Audience | **End users**, not internal ops | |
| 2 | Hosting | **Remote**, hosted by us | Directory accepts remote servers only |
| 3 | Auth | **OAuth 2.1 from day one** | Static API key was **killed** — see §4.1 |
| 4 | v1 scope | jobs · profile · tailor · actions · pipeline | plus the additions in §10 |
| 5 | Placement | Inside the backend, isolated in `backend/src/mcp/` | Nothing else may import it |
| 6 | Key hygiene | Caps + audit logging via the existing `get_audit_logger()` | No second log |
| 7 | Slow work | **Durable ticket via the existing ARQ queue** | Was "in-memory ticket" — killed, see §4.4 |
| 8 | End goal | Public connector directory, via open-to-all-users first | Has a non-technical blocker, see §11 |
| 9 | Surface | Tools + prompts + a few resources | "Every capability" is not buildable today, see §5 |

---

## 3. Verified facts this design rests on

### 3.1 The protocol moved last month

The current MCP revision is **`2026-07-28`**, not `2025-11-25`. It is a wire-protocol
rewrite: stateless (no `initialize` handshake, no sessions), `resources/subscribe` removed,
SSE-as-a-transport deprecated, **sampling / roots / logging deprecated** via SEP-2577, and
**tasks moved out of core** into an extension.
Source: <https://modelcontextprotocol.io/specification/2026-07-28/changelog>

**Consequence:** build on the official Python SDK `mcp` **v2.x**, which speaks `2026-07-28`
*and* serves 2025-era clients from the same object. **Do not hand-target the new revision** —
Anthropic's own connector docs still link the `2025-11-25` authorization spec, which is
evidence their client fleet has not moved.

### 3.2 Auth today is cookie-only

`backend/src/api/auth_deps.py:79-95` — `require_user` reads only the `job360_session`
cookie. There is no inbound `Bearer` path anywhere in `backend/src`; the only match for that
string is an *outbound* Resend call (`services/auth/email_sender.py:75`). There is no
API-key or token path to extend. Whatever we add is the **first non-cookie identity door**
into a multi-tenant system.

### 3.3 Production shape

- One Railway instance, hobby plan, **one uvicorn worker** — `backend/Dockerfile` final
  `CMD uvicorn main:app` has no `--workers`; `backend/railway.json` sets no replica count.
- Python **3.12** in production (`python:3.12-slim`), so the SDK installs fine.
  `pyproject.toml` declares `requires-python = ">=3.9"` — **bump to `>=3.10`** when the SDK
  lands or `pip install` breaks for 3.9 users.
- **`main` auto-deploys on merge.** The process restarts mid-request, routinely.

### 3.4 Search runs are not durable

`backend/src/api/routes/search.py:116` — `_runs: dict[str, dict[str, Any]] = {}`, with
`_RUNS_MAX = 500` (`:129`) and `_RUNS_TTL_SECONDS = 3600` (`:130`). Its own docstring says
*"Pure-process, not persisted across restarts."* Status polls read only this dict
(`:265-282`), returning 404 on a miss.

Every deploy of `main` vaporises every outstanding `run_id`, and the poller cannot tell
"deploy ate it" from "never existed". The multi-replica failure is *latent*; the
deploy-kills-run failure is **live and frequent**.

### 3.5 Tailoring blocks on two sequential LLM calls

`backend/src/api/routes/tailor.py:111-201` — the route holds the HTTP request open across a
`for kind in DOC_KINDS` loop of awaited LLM calls (`:161`, `:170`). Gated by
`require_verified_user` (`:115`); quota `TAILOR_FREE_PER_MONTH` returns HTTP 402 at `:125-131`
(value at `settings.py:244`). `record_tailored_usage` fires only on success (`:196`).

### 3.6 A durable queue already exists

`backend/src/workers/queue.py:42` (`enqueue_job`) plus `rescore_user_feed_task` in
`workers/tasks.py`, built to fix issue #271 — *"a deploy alone dropped the work"*
(`profile.py:129-145`). **This is the pattern for MCP tickets. It is already written,
tested and shipped.**

---

## 4. What was killed, and why

### 4.1 KILLED — static API key in a header

Three independent strikes:

1. **claude.ai and Claude Desktop cannot reliably send one.** A configured request header is
   ignored, and claude.ai instead starts an OAuth flow using the header's *name* as
   `client_id`. — [#644](https://github.com/anthropics/claude-ai-mcp/issues/644),
   [#112](https://github.com/anthropics/claude-ai-mcp/issues/112),
   [#110](https://github.com/anthropics/claude-ai-mcp/issues/110)
2. `static_headers` is beta and **organisation-level, admin-entered** — not per-person.
3. The directory **hard-requires OAuth 2.0** for authenticated services.

Static keys work only in Claude Code (`--header`) and the raw API — a developer audience
Job360 does not have. **Replaced by: OAuth 2.1 from day one.**

### 4.2 KILLED — "resources and prompts are v1 nice-to-haves"

Wrong on both ends. Prompts and resources **are** synced by the directory portal and should
ship in v1 (small numbers — §5). Meanwhile completions/tasks/elicitation/subscriptions,
which sound like the "full surface", are client-dead (§5).

### 4.3 KILLED — the 25-second in-process wait as the primary mechanism

The `2026-07-28` spec **removed SSE resumability**: a broken response stream loses the
in-flight request and the client must re-issue. Combined with `main` auto-deploying
mid-call, "wait on the stream" degrades to "lost the answer" unless the work is durable and
the tool is idempotent.

### 4.4 KILLED — an in-memory ticket store

See §3.4 and §3.6. Tickets are ARQ jobs with a Postgres-backed status row. Never a module dict.

---

## 5. Capability mapping — what MCP offers vs what we build

The rule applied: **a capability no Claude client can call is waste, however good the spec is.**

| Primitive | Build? | Reason |
|---|---|---|
| **Tools** | **Yes** | The only primitive every Claude surface supports. Anthropic's API connector is *tools-only*. |
| **Tool annotations** (`title`, `readOnlyHint`, `destructiveHint`) | **Yes — mandatory** | Missing annotations is a top directory-rejection cause. |
| **Structured output** (`outputSchema`) | **Yes** | Nearly free — Pydantic response models already are the schema. Backbone of the sync design (§9). |
| **Pagination** | **Yes** | Back it with the `limit`/`offset` already on `GET /jobs` (`jobs.py:543-544`). Default page ~20, score-descending. |
| **Prompts** | **Yes — 2 to 3** | Synced by the directory portal; appear as user-clickable actions. Each one is another surface to keep in sync — stop at three. |
| **Resources** | **Yes — 2 to 3** | In most Claude UIs resources are *user-attached*, not model-pulled. A resource nobody attaches is dead code. |
| Resource templates | No | Duplicates `get_job` with worse model affordance. |
| Completions | No | No evidence any Claude client surfaces `completion/complete`. |
| Tasks | No | Moved to an extension no Claude client implements. Use the durable-ticket-as-tool pattern instead. |
| Elicitation | No | Redesigned as MRTR this month; effectively zero client support. We get it free — the model asks the user in chat. |
| Subscriptions / listChanged | No | New mechanism nobody speaks; our tool list is static per deploy. |
| Sampling · Roots · Logging | No | Deprecated by the spec (SEP-2577). |
| Icons | Listing asset only | A submission-portal asset, not engineering work. |

---

## 6. Architecture

```
Claude Desktop / claude.ai / Claude Code / any MCP client
        |  HTTPS, JSON-RPC (Streamable HTTP)
        v
  job360.uk  /mcp                      <- mounted from backend/src/mcp/
        |
        +-- resolve_mcp_user()         <- THE ONE CHOKEPOINT
        |     validates OAuth access token -> CurrentUser
        |     sets request.state.user_id  (keeps the access log honest)
        |
        +-- tools / prompts / resources
              |
              +--> the SAME service functions the HTTP routes call
                   (JobDatabase, tailoring, actions, pipeline, profile)
```

**Isolation rule:** everything lives in `backend/src/mcp/`. **No module outside that folder
may import from it.** That keeps a future "lift it into its own Railway service" a
copy-paste rather than a rewrite, and it bounds the blast radius of a bad import.

**Mounting:** via the official `mcp` SDK v2 into the existing ASGI app. The v1 instruction
*"call `mcp.session_manager.run()` in your own lifespan"* is **unverified for v2** and the
session-centric architecture appears to have been removed — read the v2 ASGI docs before
writing the mount rather than copying v1 guidance.

---

## 7. Auth design (OAuth 2.1)

Job360 becomes both the **resource server** (holds the tools, checks the token) and the
**authorization server** (shows the login page, mints the token). Users already live in our
Postgres; renting a second identity system would mean maintaining two.

**Flow the user sees:** click Connect → job360.uk opens → magic-link login (existing) →
*"Allow Claude to access your jobs?"* → done. No copy-paste.

**What has to be built:**

- `GET /.well-known/oauth-protected-resource` — **required** by the spec (RFC 9728).
- `GET /.well-known/oauth-authorization-server` — AS metadata (RFC 8414).
- `GET /authorize` — consent screen; reuses the existing session/magic-link login.
- `POST /token` — code exchange + refresh. **PKCE mandatory.**
- Client registration: prefer **Client ID Metadata Documents (CIMD)**; Dynamic Client
  Registration is deprecated but kept as fallback for clients that only speak DCR.
- `resource` parameter (RFC 8707) for audience binding; `iss` validation (RFC 9207).
- Token storage: hashed at rest. Precedent for the discipline is `auth_deps.py:26-41`,
  which refuses to boot without `SESSION_SECRET` rather than shipping a default.

**Non-negotiable guardrails:**

1. **One chokepoint.** Every tool resolves identity through a single `resolve_mcp_user()`.
   No tool reads a token itself.
2. **No tool accepts a `user_id` argument. Ever.** This is Hard Rule #25, and a bearer path
   bypasses the cookie-derived defence *by definition*. Pinned by a
   `test_design_rules.py`-style test that inspects every registered tool's input schema and
   fails if `user_id` (or `tenant_id`) appears.
3. `request.state.user_id` is set on every MCP call, or the access log goes blind to MCP
   traffic (`auth_deps.py:86` is the precedent).
4. `require_verified_user` semantics are preserved. Tailoring and search are email-gated
   today; MCP must not become a way around our own gate.
5. Scopes: read-only by default; write scope requested separately at consent time.

---

## 8. Slow work: durable tickets

Applies to tailoring (§3.5) and, later, search runs (§3.4).

1. Tool call checks for an existing result first — `GET /tailor/{job_id}` already does this
   read. **If a bundle exists for (user, job), return it. No LLM call, no quota burn.**
2. Otherwise `enqueue_job()` onto the existing ARQ queue and write a status row in Postgres.
3. Return a ticket immediately, with poll-interval guidance in the tool result text so the
   model knows when to check back.
4. A second read tool collects it.

**Why idempotency is load-bearing:** a model retrying a timed-out tailor call burns real
money and a 10/month quota with no human in the loop. `record_tailored_usage` firing only on
success (`tailor.py:196`) helps, but a *successful* 60-second call the user never wanted is
still spend. MCP caps must be **stricter** than the web quota.

---

## 9. Keeping the MCP in sync with the SaaS

Four layers, each copied from a pattern already proven in this repo.

**Layer 1 — Derive, don't mirror.**
MCP tools are thin wrappers over the *same* service functions and *same* Pydantic models as
the HTTP routes. Schemas come from `app.openapi()` — the artifact `scripts/gen-api-types.sh`
already exports offline. One schema source, three consumers: frontend types, docs, MCP.
Principle, quoted from this repo's own `scripts/gen_doc_blocks.py`:

> *"A guard makes drift VISIBLE. A generator makes it IMPOSSIBLE — and costs nothing to
> maintain afterwards, because there is no second copy to keep in step."*

**Layer 2 — Generate-then-diff gate.**
Commit `backend/src/mcp/manifest.json` (tool names, schemas, annotations, prompt texts). A
script regenerates it from the live `app` and runs `git diff --exit-code`. Route changes →
manifest drifts → build red until a human regenerates **and reads the diff**.

> **Do not inherit the existing hole.** `check:types-drift` runs in `scripts/agent-gate.sh`
> only — no `.github/workflows/` file references it. Any agent that skips the gate ships
> drift today. **The MCP check must be wired into `ci.yml`.**

**Layer 3 — Drill it.**
Register in `scripts/drill_registry.py` with a drill that mutates one tool schema and
asserts the gate goes red. Repo law: *"a guard is not trusted because it exists, it is
trusted because someone watched it go RED."* Do not add an undrilled guard.

**Layer 4 — Parity tests.**
For each tool: `mcp_tool(x) == http_route(x)` through the same service call. **Value-presence,
not schema-presence** (Hard Rule #21) — and **refusal paths get their own cases** (402 quota,
403 unverified, 404 missing), because those are where policy drift hides.

### What this will NOT catch — stated honestly

- **Semantic drift.** `visa_only` flipping from wall to spotlight (rule #31) changed **zero
  schema bytes**. *This already happened.* Partial mitigation: generate tool descriptions
  from the `Query(description=...)` text — `jobs.py:529-541` already carries rule #31's
  meaning there, so generation would have caught this specific case. That is a discipline,
  not a guard.
- **Policy drift.** Quota values, 402 vs 429, `require_user` → `require_verified_user`
  (already happened to `/search`). Layer 4's refusal cases are the partial answer.
- **Selection drift.** A generator cannot see *absence* — nothing says tomorrow's new route
  *should* become a tool. Mitigation: an "unmapped per-user routes" report as a **warning,
  not a gate**, modelled on the existing `scripts/absence_check.py`.
- **Description-vs-model drift.** Descriptions can be perfectly synced and still steer Claude
  badly. Only an eval catches this: one scripted conversation smoke test against the live
  server, on a schedule, in the shape of `journey.yml` / `verify-live.yml`.
- **Client drift.** Nothing here detects Anthropic changing what clients call. The
  `2026-07-28` rewrite is proof it happens. Same mitigation as above: scheduled live smoke.

---

## 10. SaaS changes this exposes — do these first

The audit found capability that exists in code but is unreachable, plus one real defect.
An agent-friendly API needs verbs a click-driven UI never needed.

| # | Change | Why | Evidence |
|---|---|---|---|
| 1 | **Persist search runs** to Postgres | Deploys vaporise `run_id`s; an MCP poller cannot distinguish that from "never existed" | `search.py:116,129,130,265-282` |
| 2 | **Bump `requires-python` to `>=3.10`** | SDK needs it; prod is already 3.12 | `pyproject.toml` |
| 3 | **Wire the drift check into `ci.yml`** | Today it only runs in the local gate | no workflow references `check:types-drift` |
| 4 | Expose **GDPR export** in the UI | Backend exists, no `api.ts` function, no button — and the directory review wants a privacy story | `auth.py:344`, `database.py` `export_user_data` |
| 5 | Add a **"re-score my feed"** route | Exists only as a side effect of saving a profile | `services/rescore.py`, ARQ task |
| 6 | Add **enrich-one-job** on demand | ARQ-only today | `workers/tasks.py` |

Items 1–3 are **prerequisites**. Items 4–6 are the "MCP makes the SaaS better" dividend and
can follow.

---

## 11. Phases

**Phase 0 — prerequisites** (§10 items 1–3).

**Phase 1 — OAuth + the server.** OAuth 2.1 endpoints, `resolve_mcp_user()`, the tool set,
2–3 prompts, 2–3 resources, annotations, structured output, pagination, the manifest gate
(Layers 1–4), the `no user_id argument` rule test.

**Phase 2 — open to all Job360 users.** A "Connect to Claude" page with instructions.

**Phase 3 — directory listing.** Prerequisites, all confirmed from Anthropic's
[submission docs](https://claude.com/docs/connectors/building/submission):

- [ ] **A Claude Team or Enterprise plan.** *"Organization settings aren't available on
      individual plans."* **A solo founder on an individual plan cannot submit at all.**
      OPEN — owner to confirm current plan tier.
- [ ] OAuth 2.0 (delivered by Phase 1)
- [ ] Every tool annotated with `title` + `readOnlyHint`/`destructiveHint`
- [ ] Privacy policy URL (`/privacy` exists) and docs URL
- [ ] A reviewer test account with a **fully populated** profile
- [ ] Seven policy acknowledgements; review takes weeks

---

## 12. Open items and coverage bounds

**Open — needs the owner:**
- Claude plan tier (individual vs Team). Gates Phase 3 entirely.

**Unverified — check before relying on:**
- Which protocol era each Claude client speaks on the wire (legacy `initialize` vs modern
  per-request `_meta`). Not published anywhere. Resolve by pointing a test server at each
  client and inspecting the first request. Mitigated meanwhile by SDK v2's dual-era support.
- Claude Desktop's elicitation support — treated as absent on a no-evidence-of-support
  basis, not a positive source.
- The `mcp` v2 mounting API. The v1 `session_manager.run()` lifespan requirement may not
  apply. Read v2 docs first.
- Claude client tool-call timeout values. Not documented; the 25s figure was plausible but
  unmeasured, and is now moot given §4.3.
- Whether `static_headers` will graduate to per-user. Open feature requests only, no roadmap.

**Coverage bounds of the audit behind this doc:** four parallel read-only agents over
`backend/src`, `backend/migrations`, `frontend/src`, `scripts/`, `.github/workflows/`, plus
an adversarial pass verified against `origin/main` @ `9b6cfba`. No load testing, no live DB
queries, no running app. Frontend API usage was checked by import-grep, so a dynamically
constructed call path could have been missed.
