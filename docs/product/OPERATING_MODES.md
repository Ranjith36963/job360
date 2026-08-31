# Operating Modes — Box 1 and Box 2

**Read `INTENTION.md` first.** This file expands §7 of it.

Job360 must work in two shapes. **Neither is a fallback for the other.** The same
connections are needed in both. What changes is *who orchestrates* and *who pays for
thinking*.

---

## Box 2 — inside an AI client (Claude, ChatGPT, Gemini, Grok)

The user connects several things *in their AI client*:

```
                    Claude / ChatGPT / Gemini / Grok
                    (the intelligence AND the wiring)
                                  |
        +-------------+-----------+-----------+-------------+
        |             |                       |             |
    Job360         Gmail                   Apollo      Job providers
  career ops     the eyes                 contacts    (Indeed, Apify…)
   the brain    send + receive          who to reach     raw job data
```

**Job360's role here is the brain, not the plumbing.** The AI client already holds the
other connections. We are the one that *remembers* — the record, the lifecycle, the next
action, the improvement loop. Nobody else in that diagram keeps history.

**Who pays for intelligence:** the user, through their existing AI subscription. **We spend
nothing on tokens.** This is the cheapest mode for us to run and the one with built-in
distribution.

**Jobs in Box 2:**
- **Premium** — we supply jobs from our 40 live sources, scored against their profile.
- **Light** — we do not. The user pastes a job description, or connects their own job
  source (they pay Apify/Indeed directly). We still give them the **full lifecycle**.

**Technical shape:** Job360 runs an **MCP server**. The AI client is the MCP host. See
`docs/plans/2026-08-26-mcp-server-design.md`.

---

## Box 1 — inside our own SaaS (no AI client)

```
                          job360.uk
                    (we orchestrate everything)
                              |
        +----------+----------+----------+-----------+
        |          |                     |           |
     Gmail      Apollo            Job providers   Intelligence
    the eyes   contacts            raw job data   ours / their key
                                                  / their local model
```

**Job360 does the wiring itself.** No borrowed host, so we hold every connection and route
every LLM call.

**Who pays for intelligence:** us or them. Per-user setting — our model, their API key, or
a local open-source model they run. Some users will *want* local models; that flexibility
is a selling point.

**Jobs in Box 1:**
- **Premium** — required if they want us to find jobs. Searching 40 live sources and LLM-judging
  costs us real money.
- **Light** — no job searching. They paste a description or bring their own source.

**Technical shape:** Job360 becomes an **MCP client** too, connecting *out* to Gmail,
Apollo and other MCP servers. This is new capability — we have never built it.

---

## Why both, and why it matters

Most products are one or the other: a connector *or* a platform. Being **both** is what
makes Job360 the mount rather than a node. We sit between the data and the intelligence,
and we own the record that neither of them keeps.

- A user on Claude gets us as a connector, free intelligence, instant distribution.
- A user who wants no AI subscription, or wants local models for privacy, still gets the
  full product.

---

## The hard constraint on borrowed intelligence

MCP once had `sampling` — a server asking the host's model to generate something. That
would have let Box 1 borrow the user's Claude.

**It was deprecated in spec revision `2026-07-28` (SEP-2577).** Migration guidance is to
integrate with an LLM provider directly.

So: **Box 2 is the only place intelligence is free.** In Box 1 we route to a configured
provider. Design the LLM provider as a per-user parameter from the start.

---

## Build order

**Box 2 first.** Reasons:

1. Intelligence is free there — no token cost while we have no revenue.
2. Distribution is built in — the connector directory is a discovery channel.
3. It is the agentic bet, and `INTENTION.md` §3.5 says build for three to five years out.
4. The Gmail and Apollo work is **the same either way**, so nothing is wasted.

Box 1 follows once the record proves useful.

---

## What must be true in both

- Every capability in one mode exists in the other (`INTENTION.md` §3.3).
- Nothing is Claude-specific. Vendor-neutral names, schemas and descriptions.
- Nothing is UK-specific. Region is a parameter.
- The LLM provider is a per-user parameter, never a hardcode.
