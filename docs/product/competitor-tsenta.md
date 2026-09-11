# Tsenta, feature by feature — what we copy, what we refuse
<!-- doc: LIVING | decided 2026-09-07 by the owner; status column re-measured against main e7beabb on 2026-09-11 -->

Tsenta is an auto-apply product: it watches career pages, matches roles to a
CV, fills the ATS form and submits, then keeps a receipt and reads Gmail for
the outcome. It is the closest thing to Job360 on the market and the mirror
image of it on the one rule that matters: **it finds and sends; we remember.**
This table is the owner's call on every feature, with what actually exists in
our code on the day it was decided. [VISION.md](VISION.md) still wins where the
two disagree — this file is the reasoning behind four rows of the roadmap.

| # | What Tsenta does | Our call | Why | State on main (2026-09-11) |
|---|---|---|---|---|
| 1 | Watches 50k+ career pages on 19 ATSes, matches roles to your CV | **No** | Rule 4: the user brings the job. We never source. | — |
| 2 | "Swipe to apply" feed of matched roles | **No** | Same. No feed of jobs we found. | — |
| 3 | Auto-apply the moment a role posts ("applicant #4 of 312") | **No** | Volume halves conversion; boards ban it; their own FAQ says "not undetectable". | — |
| 4 | Fills the ATS form: login, fields, open questions, documents, submit | **No for now** | Only makes sense with #3. Maybe later as "help me on *this* form", one at a time. | — |
| 5 | Tailored CV + cover letter per job, "review before submit" with a diff | **Copy** | We tailor already. Add the **side-by-side diff + Keep button**. | **Done** — diff shipped 2026-09-11 (PR #543, `frontend/src/components/applications/ArtifactDiff.tsx`, `GET /applications/{id}/artifacts/{id}/diff`). **No Keep button** — VISION decision 26: the receipt names the applied version; an Applied badge shows it. Tailor stays the web fallback. |
| 6 | Sponsorship signals surfaced | **Copy** (was wrongly "have") | `jobs.visa_flag` is dead code since slice 5; nothing writes it, nothing shows it. Only the CV-side `cv_right_to_work` lives. Agent supplies the signal, we store + show. | **Done** — shipped 2026-09-11 (PR #554, migration 0042, `backend/src/services/applications/visa.py`), country-agnostic by VISION decision 27: the agent's reading of the ad + the candidate's own country list, one list-membership comparison, no country rule of ours. `jobs.visa_flag` still dead, still present — its own cleanup. |
| 7 | **Receipt** per application: fields, answers, CV, cover letter, confirmation | **Copy exactly** | Their best idea. Ours covers *every* application, not only machine-sent ones. | **Done** — `application_receipts`, `record_application` / `get_receipt` / `list_receipts` |
| 8 | "Flag for next time" on a receipt | **Copy** | The learning loop. Cheap. | **Done** — shipped 2026-09-11 (PR #552): "Flag for next time" box under the timeline, Lessons list on the profile (`GET /applications/lessons`), and `get_profile` returns the last `PROFILE_LESSONS_MAX` so the agent reads them before the next CV. |
| 9 | Gmail connected: reads replies / invites / rejections, updates the tracker | **Copy, agent-side** | The agentic part. The user's own agent reads Gmail through its connector and calls our store tools. **No Gmail OAuth from us** (VISION decision 8). | **Done** — email evidence shipped 2026-09-07 (PR #521, migration 0041): `record_event` takes an optional `source` (message id, sender, subject), idempotent per (application, message id); the timeline shows sender and subject. |
| 10 | Gmail: drafts recruiter replies into Drafts, never sends | **Copy, agent-side** | Exactly the right line: drafts, not sends. The agent drafts; Gmail keeps it. | Nothing to build on our side |
| 11 | Gmail: enters emailed verification codes | **No** | Only needed for auto-submit. | — |
| 12 | Interview dates + recruiter messages shown | **Copy** | Falls out of #9. | **Done** — a real `scheduled_at` on interview events since PR #521 (migration 0041); the application page shows the next non-superseded interview datetime. |
| 13 | Tracker / live status dashboard | **Copy, secondary** | The dashboard is the record, not the product. The outcome arrives in the agent; the page is where you look back. | **Done** — `frontend/src/app/applications/` |
| 14 | Multiple CV profiles, pick per role | **Later** | Real need, not now. `user_profiles.user_id` is the primary key — one profile, hard. | Not built, not scheduled |
| 15 | iMessage / WhatsApp "text to apply" | **Later, on a paying signal** | Needs a worker + Redis back (deleted 2026-09-02). The Claude / ChatGPT phone apps cover the phone via MCP today. | Not built, not scheduled |
| 16 | iOS + Android apps | **No** | Two more codebases. Web + MCP cover it. | — |
| 17 | Chrome extension (auto-fill) | **No for now** | Only useful with #4. | — |
| 18 | MCP for Claude Code / Claude / Cursor / Codex — find roles, tailor, submit | **Copy, different tools** | Ours store, theirs do. | **Done** — 18 tools; `bring_job`, `save_fit`, `tailor_documents`, `record_application`, `get_receipt`, `record_event` cover the six we named. **Claude.ai / ChatGPT / Grok connectors still NOT proved live** (2026-09-11: prod has 0 OAuth grants; needs `OAUTH_REDIRECT_ALLOWLIST` on Railway and a logged-in browser — VISION decision 24, the one open row of the feature audit) |
| 19 | OAuth with short-lived tokens for MCP | **Copy** | Correct auth shape. | **Done** — `services/auth/oauth_flow.py`, PR #488 |
| 20 | Per-application pricing, credits | **No** | Free until value is proven. Nothing that rewards volume. | — |
| 21 | Recruiter side | They have none | Our extra. Consent-first only (Hired / Triplebyte died selling access). Later. | Not built, not scheduled |

## What follows from it

Build order, decided the same day (see the roadmap): #513 email evidence
first — it is the part that makes status change without the user opening
anything — then #514 visa signal, #515 CV diff, #516 flag-for-next-time.
WhatsApp, multiple profiles and the recruiter side stay in "later, on
evidence". Rows 9/10 hold VISION decision 8 as written: the agent's
connector reads; we never touch Gmail.

The eight pre-pivot `wiring/*` draft PRs (#447–#454: chase cron, instant
email, card truth) were closed the same day — they targeted the notification
stack that #499 and #503 deleted.
