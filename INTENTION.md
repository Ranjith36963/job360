# INTENTION.md — what Job360 is for

**Read this before designing anything.** Not the code, not the tests — the *why*.
When a decision could go two ways, this file breaks the tie.

Written 2026-08-27, from the owner's own words. Owner is the only person who edits
the convictions below.

---

## 1. The one-line version

**Job360 is not a job board. It is the memory and operations layer for a job search —
usable by a human or an agent, from any client.**

We sell **shovels, not gold**. We do not win by having the most jobs. We win by being
the place where your entire job search is *recorded, operated and improved*.

---

## 2. The pain we exist to remove

> You apply to company X. You tailor your CV. You write a cover letter. You send an
> outreach message. Three to four weeks later they call you.
>
> **You have no idea which CV you sent. Or what your cover letter said. Or how you
> contacted them. Or what you prepared.**

That is the pain. And it compounds:

- People no longer apply to 2 jobs. They apply to dozens.
- AI writes most of those applications now, so the volume went up and the memory got worse.
- You get rejected — and **you learn nothing**, because there is no record of what you
  actually did.

**No record means no improvement loop.** That is the hole in the market. Indeed,
LinkedIn, ChatGPT and a thousand job boards all leave it open.

---

## 3. The five convictions

### 3.1 We are not a data provider
Everyone has job data. Indeed, LinkedIn, thousands of boards. **We do not compete on
having jobs.** Aggregation is table stakes we already paid; it is not the product.

### 3.2 Store everything now. Measure later.
Capture every corner of every application — which CV, which cover letter, which version,
which model wrote it, when it was sent, what was said, what was prepared, what came back.

Intelligence gets cheaper every year. **The data does not appear retroactively.** A field
we fail to capture today is an insight that is impossible forever. When in doubt, store it.

### 3.3 Humans and agents drive the same system
Two doors, one house. The dashboard is for people. The MCP server is for agents. **Any
operation available to one must be available to the other.** An agent-only capability is a
missing feature in the UI; a UI-only capability is a missing tool in the MCP.

### 3.4 Be the mount, not the rival
LinkedIn, Indeed, Apollo, ChatGPT, Claude, offline LLMs — these are **partners, not
competitors**. We fill a gap they leave open, so connecting to us beats fighting us.
Every design choice should make it *easier* for someone else's product to plug in.

**Corollary: we are client-neutral.** MCP is an open protocol, not Anthropic's private
door. Never design for one AI vendor. Claude is one client among many, and the same is
true of every job board we read.

### 3.5 Build for three to five years out
The agentic era is the target, not today's habits. **Agent-native from the ground, never
agent-bolted-on.** If a design only makes sense for a human clicking a screen, it is
already out of date.

---

## 4. What this means in practice

Rules that follow directly from the convictions. These bind new work.

1. **Every operation gets an API before it gets a screen.** The screen is one consumer.
2. **Nothing is overwritten. Everything is versioned.** History *is* the product
   (§3.2). A destructive update is a bug, not a design.
3. **Record the provenance, not just the artefact.** Which profile version, which model,
   which prompt, which moment. "A CV" is worth little; "the CV I sent to X on the 3rd,
   generated from profile v7 by gpt-4o-mini, which I then edited these 4 lines of" is
   the asset.
4. **Anything that varies by country, tenant, plan or client is a PARAMETER.** Never a
   hardcode. (See the global-readiness work; see product rule #30.)
5. **No capability is Claude-specific.** Tool names, descriptions and schemas stay
   vendor-neutral and region-neutral.
6. **Empty means silent.** An unfilled field is "don't care", never a penalty and never a
   guess. (Product rule #29.)
7. **The user owns their record.** Full export, full deletion, full visibility. A memory
   layer that holds your data hostage is not trustworthy, and trust is the whole product.

**Product attributes, in the owner's words:** speed, quality, trust, efficiency, latency,
awareness. All of them, together.

---

## 5. The application lifecycle — the actual product

One application, end to end. **This list is the roadmap.** Status verified against
`origin/main` @ `9b6cfba` on 2026-08-27.

| Stage | Stored today? | Where |
|---|---|---|
| Job found, scored, deduped | **Yes** | `jobs`, `user_feed`, 47 sources |
| Why it fits me / skill gaps | **Yes** | `user_feed.llm_verdict`, fit reason, `skill_gap` |
| CV + cover letter tailored | **Yes** | `tailored_documents` — `ai_draft`, `polished`, `model`, `profile_version`, `kept_at` |
| The draft→final diff (learning signal) | **Yes** | same table, by design (migration 0023) |
| Marked applied | **Yes** | `user_actions`, `applications` |
| Stage moves, with timestamps + notes | **Yes** | `application_stage_history` |
| Notes, versioned over time | **Yes** | `applications.notes_history` |
| Interview dates | **Partial** | `applications.interview_dates` — dates only |
| Follow-up reminders | **Partial** | `get_stale_applications`, `/pipeline/reminders` |
| **Which exact CV I actually submitted** | **NO** | `UNIQUE(user_id, job_id, doc_kind)` — one row per job, so regenerating replaces. No attempt history, no "this is the one I sent". |
| **Outreach — who I messaged and what I said** | **NO** | No table exists. This is the biggest hole. |
| **Interview prep — what I prepared, what they asked** | **NO** | Only dates are stored. |
| **Outcome and reason — why I was rejected, any feedback** | **NO** | No outcome field, no feedback capture. |
| **The improvement loop — "here is what to change next time"** | **NO** | Nothing reads the history back. |

**The top five gaps are the product.** Everything above them is the foundation that
already exists.

---

## 6. How to use this file

- **Before any design decision**, check it against §3 and §4.
- **If a proposed feature makes us more like a job board, stop.** That is the wrong
  direction, however good the feature is.
- **If a proposed feature loses history to save effort, stop.** See §3.2.
- **If a capability lands in the UI but not the MCP (or vice versa), it is unfinished.**
  See §3.3.
- When this file and a ticket disagree, **this file wins** — then come back and fix the
  ticket.

The convictions in §3 change only when the owner changes them. §5's table is a living
status board — update it as gaps close, with evidence.
