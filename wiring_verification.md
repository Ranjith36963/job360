# WIRING_VERIFICATION.md — how each fix gets proved

Companion to [`wiring.md`](./wiring.md). That file says **what is broken**. This file says
**how we prove it is fixed and a real user can use it.**

Item IDs (W-01, D-03, …) match `wiring.md` exactly.

---

## The rule

**"Tests pass" is rung 2 of 5. It is not proof a user can use the thing.**

Code-looks-right has lied here before. An item is not done until it climbs all five rungs
and I have stated the coverage bounds out loud.

---

# THE FIVE RUNGS

### Rung 1 — The test fails first
Write the test. **Watch it go red for the right reason.** A test that was never red proves
nothing — it may be asserting something that was already true.

**Rule #21 — value-presence, not schema-presence.** This is the trap that keeps catching us:

```python
assert "expired" in body          # ← PASSES against a `= False` default
                                  #   and a serializer that never reads the column
assert body["expired"] is True    # ← only passes if a real write flowed through
```

Run a real input end to end and assert the **non-default** value.
Pattern to copy: `tests/test_database.py::test_dim_columns_round_trip`.

### Rung 2 — The gate
```bash
cd backend && python -m pytest -q -p no:randomly tests/test_X.py
```
**Targeted, not `--full`.** On Windows the full suite is O(n²) — roughly 9 hours. Targeted is
~4 minutes. Linux CI is the full verifier (~2m41s). Needs the dev Postgres up:
`docker ps` → `job360-dev-postgres` on `127.0.0.1:5433`.

Two failure modes that have burned real hours:
- **Read the REAL exit code**, never a wrapper's. A pipe, a background wrapper, or a
  `| tee` reports *its own* status. Check the output file is non-empty too.
- **Never relaunch a slow gate.** `--full` is silent for ~34 minutes. Reading silence as
  death and relaunching leaked 1180 schemas and wedged the test DB. `ps aux` cannot see
  Windows processes — use `tasklist`.

### Rung 3 — The contract
If a route, request model, or response model changed:
```bash
cd frontend && npm run type-check    # and regenerate api-types
```
If the drift check fails, backend and frontend disagree — the user gets a blank field and
no error. This is silent, so it must be checked, not assumed.

### Rung 4 — Drive it as the user ← **the rung that counts**
Real browser, real click path, screenshot at the moment of proof. Per-item scripts below.
Not "the code looks right."

**Always include the negative case.** A fix that works on the happy path and opens a hole on
the malicious one is not a fix.

### Rung 5 — Prove it in production, after merge
`main` auto-deploys. Merging **is** the release. So verify after, with instruments:

| What | Instrument |
|---|---|
| The actual email that went out | Resend MCP — `list-emails`, `get-email`. Reads the real message body and link. |
| New errors after deploy | Sentry MCP — `organizationSlug: "job360"`, `regionUrl: "https://de.sentry.io"` |
| Which commit is live | `railway deployment list --service backend --json` → read `meta.commitHash` |
| Logs | `railway logs --service <name> \| head -N` (it **streams** — `tail` returns nothing and looks exactly like "no access") |
| Database | `railway run -s Postgres python <script>` using `DATABASE_PUBLIC_URL` |
| Real user behaviour | PostHog MCP (EU) — only meaningful once **W-27** lands |

**Never** trust `/api/health` — it returns a hardcoded `"version": "1.0.0"`.
**Never** print secret values; filter to key NAMES only.

---

# DEFINITION OF DONE (per item)

- [ ] Red test existed and failed for the right reason
- [ ] Targeted gate green, real exit code read
- [ ] `api-types` regenerated if the contract moved
- [ ] Browser walkthrough done, **happy path AND negative case**, screenshots kept
- [ ] Drill registered in `scripts/drill_registry.py`
- [ ] Coverage bounds stated in the PR description
- [ ] Verified in prod after merge with a named instrument

---

# WALKTHROUGHS

## PR 1 — The door (W-01, W-03)

### W-01 — the magic link must carry `?next=`
**Happy path**
1. Sign in, then clear the `job360_session` cookie (simulate an expired session).
2. Open a job the way the email links it: `/jobs/123`.
3. Bounced to `/login`. **Screenshot.**
4. Use the **magic-link form** — the default. Do *not* toggle to password; the password
   path already works and would prove nothing.
5. Read the **actual emailed URL**. It must contain `next=/jobs/123`.
6. Click it → press **Sign in**.
7. **Lands on `/jobs/123`, not `/dashboard`.** **Screenshot.** ← the proof

**Negative path (open-redirect — this is a security fix, not a convenience fix)**
| Input | Must land on |
|---|---|
| `next=https://evil.com` | `/dashboard` |
| `next=//evil.com` (protocol-relative) | `/dashboard` |
| `next=/\evil.com` | `/dashboard` |
| `next` with a CRLF (`%0d%0a`) | `/dashboard`, and **no header injected into the email** |
| `next` absent | `/dashboard` |

**Where the guard lives:** `safeNext()` at `frontend/src/app/(auth)/login/page.tsx:22`,
already unit-tested at `login/__tests__/login-redirect.test.tsx:36`. **Reuse it.**
**The backend needs its own copy of the rule** — the emailed link is built server-side
(`magic_link.py:66-67`), so `next` is a server input and a frontend-only check is bypassed by
calling the API directly. An unvalidated value here is an open redirect *inside an email we
send* — the worst possible place for one.

**Prod check (rung 5):** Resend `get-email` on a real magic-link send → confirm the href
carries `next=` and points at `job360.uk`.

### W-03 — day one must not be a dead end
1. Register a **brand-new** address. Land on the dashboard.
2. **Must NOT see:** "Try adjusting your filters, expanding the time range, or lowering the
   minimum score."
3. **Must see:** a CV call-to-action with a working link to `/profile`. **Screenshot.**
4. Upload a CV → return to the dashboard → the CTA is gone and jobs appear. **Screenshot.**
5. **Negative:** a user who *has* a profile but genuinely zero results must still get the
   old filter advice — the new message must not swallow the real empty state.

---

## PR 2 — The instant email (W-17, W-18)

1. Fire one instant notification for a job with a known score and a known LLM reason.
2. Assert on the **body that was actually sent**, not the function's return value:
   - contains the **match score**
   - contains the **LLM reason**
   - contains a **`job360.uk/jobs/{id}`** link
   - does **NOT** contain the bare employer `apply_url` as the primary link
3. Send a real one to your own inbox. **Open it on a phone.** Screenshot.
4. Click the link → lands on the Job360 job page, signed in. Screenshot.
5. **Regression guard:** the digest email must be unchanged — diff one before and after.

**Prod check:** Resend `get-email` on the real send. This is the only way to see what
actually left the building.

**Bounds:** this proves content and link shape. It does **not** prove deliverability
(spam placement, Gmail clipping) — that needs a real inbox test per provider.

---

## PR 3 — The chase cron (W-19, W-20)

1. Backdate an application to 8 days ago in the dev DB.
2. Run the cron **by hand** — do not wait for the schedule.
3. Assert a chase notification lands in `notification_ledger` with `status='sent'`.
4. Assert the message names the **job and company**, not a generic string.
5. **Negative cases — each needs its own test:**
   - an application in `offer` or `rejected` → **no** chase
   - inside quiet hours → **queued**, not sent
   - already chased today → **not** chased twice
   - a user with no channel → skipped cleanly, no crash
6. Timezone: dispatch converts UTC to `users.timezone` via stdlib `zoneinfo` (**not pytz**).
   Test a BST user and a UTC user — skipping this leaks notifications across DST.

**Bounds:** local runs mock HTTP. Real delivery is prod-only.

---

## PR 4 — Pipeline card truth (W-05, W-15, tailored summary)

Tests already written: `backend/tests/test_pipeline_wiring.py` (9 tests, currently red).

1. Apply to a job → it appears under the job list's **Applied** filter. Screenshot.
2. **Like** a job, then apply to it → the heart is **still there** and `applied` is true.
   This is the whole reason we don't write into `user_actions`.
3. Expire a job after applying → the card shows **Job closed**. Screenshot.
4. A merely `likely_stale` job must **not** be flagged closed.
5. Generate a CV → the card's button reads **CV ready**, not the blind label. Screenshot.
6. **Isolation (rule #12/#25):** a second user's tailored doc for the same job must never
   appear on this user's card. Already covered by
   `test_tailored_summary_never_leaks_another_users_documents`.

---

## PR 5 — Don't lose the CV he sent (W-08, W-10)

**This one is about data that is being destroyed today.** The walkthrough is the point:

1. Generate a CV for job X. Note its text.
2. Apply to job X.
3. **Regenerate** the CV — change something visible.
4. Open the pipeline card → it must still show **the CV he actually applied with**, not the
   new one. Screenshot both.
5. Assert at the DB level that the applied-with version is still retrievable.

**Migration safety:** any schema change here runs on boot via `dependencies.init_db()`.
Test the up **and** the down migration. Existing rows must survive.

---

## PR 7 — Launch gates (W-23, W-27)

**W-23 unsubscribe:** click the link in a real email → it works **without logging in** →
`notification_rules` flips off → the next digest does not send. Screenshot each step.
Also confirm a `List-Unsubscribe` header is present.

**W-27 analytics:** perform each of the 4 actions, then confirm the event actually arrived
**in PostHog** — not that `posthog.capture` appears in the diff. An instrument must count
the way its consumer counts.

---

## PR 8 — Delete sweep (D-01…D-05)

Deleting is a change. Same bar:
1. `git grep` the symbol across **the whole repo** — backend, frontend, tests, migrations,
   scripts, workflows — and paste the zero-hit result in the PR.
2. Full gate on CI, not targeted — a deletion's blast radius is unknown by definition.
3. For **D-01** (`interview_dates`): a column drop needs a reversible migration and a check
   that no backup/restore script references it.
4. Browser smoke: the pages that *used* to touch the deleted thing still render.

---

# DRILLS — every guard declares one

`scripts/drill_registry.py` makes an undeclared guard a **red build**. This is not
bureaucracy: two loops on `main` were dead on arrival and only firing them on purpose
revealed it. A guard nobody fires is a guard nobody knows is broken.

| Item | Drill — the deliberate way to fire it |
|---|---|
| W-01 | Request a magic link with `next=//evil.com` → must land on `/dashboard`, never off-site |
| W-03 | Register a fresh account → the dashboard must render the CV call-to-action |
| W-15 | Flip a job to `confirmed_expired` → its pipeline card must show **Job closed** |
| W-17/18 | Fire one instant notification → body must contain the score **and** a `job360.uk/jobs/` link |
| W-19 | Backdate an application 8 days, run the cron by hand → a chase lands in the ledger |
| W-23 | Click unsubscribe while logged out → `notification_rules` flips off |

---

# ANTI-PATTERNS THAT HAVE ALREADY COST US TIME

1. **Schema-presence ≠ value-presence.** `assert "field" in body` passes against a default.
2. **The filter IS the blind spot.** A predicate that narrows what you measure is a claim
   about where the damage *is not*. Two distribution checks once reported 0 while 172 rows
   were damaged. Before trusting a check, ask what it cannot see.
3. **An instrument must count like its consumer.** A dashboard said 83% where the real
   figure was 52%, because it counted shapes instead of values.
4. **A correctness fix with an operational cost is still a regression.** A 5-row test cannot
   see event-loop blocking. Test at production scale where the change touches a hot path.
5. **Tests must not encode the merge queue.** A test that names an open PR turns `main` red
   the moment that PR merges. Make the example chosen, not named.
6. **Never quote a count from a doc — measure it.** Three docs once disagreed by 400–800
   tests.

---

# COVERAGE BOUNDS — say these out loud with every verdict

- Local tests **mock all HTTP** (`aioresponses`); the suite must run offline. Real
  deliverability — spam placement, Gmail rendering, `List-Unsubscribe` being honoured — is
  provable **only in prod, via Resend**.
- **CI has no LLM keys.** `OPENAI` / `GEMINI` / `CEREBRAS` secrets render empty in Actions.
  Anything touching the judge, tailoring, or enrichment cannot be verified in CI — it needs
  a local run with real keys.
- A browser walkthrough proves **one path**. It does not prove every filter combination,
  mobile layout, a second concurrent user, or a slow network.
- The full Windows suite crashes psycopg natively (exit 139) when run twice back to back.
  That is environmental, not a real failure. Linux CI is clean.
- The findings in `wiring.md` came from agents whose refuters killed **0 of 37**. Re-verify
  by hand before building anything large on a single finding. That practice already caught
  one wrong claim (the email link).
