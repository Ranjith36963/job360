# Connectors — what plugs into Job360, and why

**Read `INTENTION.md` first.** This file expands §9 of it.

A career-ops orchestrator is only as good as its hands and eyes. This is the map of what
connects, what job each one does, and the order to build them.

**The test for any connector:** does it make the *record* more complete, or the *next
action* better? If neither, it does not belong.

---

## Tier 1 — the system cannot be proactive without these

### Gmail — the eyes
**Job:** send and receive. Watch what actually happens.

This is the single most important connector. Without it, Job360 only knows what the user
remembers to tell it. With it, Job360 knows the truth:

- an interview invite arrived
- a rejection arrived, and what it said
- a recruiter replied, and what they asked
- **nothing arrived for 14 days** — the ghosting signal

**Silence is a signal.** That is the whole point. A career-ops system that cannot see
silence cannot be proactive, and proactivity is the product.

*Note:* the Resend account already exposes received-email and attachment endpoints, so a
forwarding address (`you@inbox.job360.uk`) may be a much cheaper first step than a Gmail
OAuth integration. **Verify before designing the OAuth flow.**

### Calendar (Google / Outlook) — the clock
**Job:** interviews are calendar events, not dates in a text field.

**This is the biggest gap in the current thinking.** Gmail sees the invite arrive; the
calendar knows it is *tomorrow at 2pm*. Everything time-shaped depends on it:

- prep reminders before the interview, not after
- the follow-up window after it
- "you have three interviews this week, here's the prep order"
- rescheduling, which happens constantly and silently breaks a date field

Today `applications.interview_dates` is a JSON array of date strings with no source of
truth behind it. Calendar makes it real.

### Apollo — the contacts
**Job:** given a company and a role, find the right humans to reach.

Turns "I applied into a void" into "I applied and messaged the hiring manager." This is the
input to the outreach log — the gap identified in `INTENTION.md` §5 as the biggest hole.

### Job providers — the raw data
**Job:** the jobs themselves. Our 47 sources, plus the user's own (Indeed, Apify, whatever
they already pay for).

**Premium supplies these. Light does not** — Light users paste a job description or bring
their own source. See `docs/product/OPERATING_MODES.md`.

---

## Tier 2 — makes the record richer

### GitHub
Already wired into profile extraction. Evidence of real skill; also the asset the B2B
direction (`INTENTION.md` §11) pitches to recruiters.

### LinkedIn
**Highest value, highest risk.** Most recruiter contact happens there, and most Easy Apply
applications never touch email. But there is no official API for this, and scraping breaks
their terms.

**Treat as a research item, not a build item.** Do not design a dependency on it.

### Cloud storage (Drive / Dropbox / OneDrive)
Where people's CVs actually live. Removes the upload step and gives version history for
free.

### Phone / SMS
Recruiters call. A call is a lifecycle event with no record today. Twilio is already in the
owner's stack.

---

## Tier 3 — later, or never

| Connector | Why it is not Tier 1 |
|---|---|
| ATS candidate portals (Greenhouse, Workday) | Status lives here, but there are no candidate-side APIs. Very hard. |
| Slack / Discord | Some hiring happens here; already exist as our notification channels, not as data sources. |
| Notion / Obsidian | Where some people keep prep notes. Nice, niche. |
| Calendly and similar | Mostly covered once Calendar exists. |

---

## Build order

1. **Gmail** — without eyes, nothing else is proactive
2. **Calendar** — without a clock, nothing is timely
3. **Apollo** — the outreach loop needs contacts
4. **Job providers** — already have 47; the user's own sources come later
5. Everything in Tier 2, by evidence of demand

---

## Rules for every connector

1. **A connector is a source of truth, never a copy.** Store what we learn from it with its
   provenance — which connector, when, what raw payload.
2. **Every connector must degrade.** If Gmail is not connected, the product still works with
   a thinner record. Never a hard dependency.
3. **The user connects it, the user can disconnect it**, and disconnecting removes access
   without destroying the record they already own.
4. **Read before write.** Reading Gmail is a small trust ask. Sending as the user is a large
   one. Earn the second with the first.
5. **In Box 2, the AI client may already hold the connection** — do not force a duplicate
   connection inside Job360 when the host already has it. Detect and defer.
