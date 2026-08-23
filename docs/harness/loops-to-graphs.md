# Loops -> graphs: where our harness sits (research 2026-08-01)

> 4 Sonnet web-researchers + Opus synthesis, on the owner's own source list.
> VERIFIED = primary source. UNVERIFIED = community claim, named and quarantined.

# Where our harness sits on the loops → graphs ladder

**Read this first:** we are not behind. On the parts that matter (external verification, deterministic gates, a blind second judge), we are at or ahead of what Anthropic and Google actually publish. The gap is real but small, and one of the two named "gaps" is something Anthropic explicitly tells you *not* to build for coding work.

---

## 1. THE LADDER

**Honesty note first.** The sources name **three** rungs, not five. MarkTechPost's piece is literally titled "Prompt Engineering vs Loop Engineering vs Graph Engineering". Nobody in the research names "context" or "harness" as formal rungs. `INFERENCE` — the 5-rung version is your framing, not the literature's. I kept your rungs but marked which ones the sources actually back.

| Rung | Plain meaning | Backed by a source? | Where we are |
|---|---|---|---|
| **Prompt** | You type, the model answers, you judge it. | `VERIFIED` (MarkTechPost) | **Left this rung.** No loop in the repo needs a human to type a prompt. |
| **Context** | You stop typing the same things — the rules live in files the agent always reads. | `INFERENCE` — no source names this rung | **Done.** `CLAUDE.md` (28 numbered Hard Rules), `MEMORY.md`, `STATUS.md`. The blind checker is fed the rules as "INVARIANTS" at `.github/workflows/repair.yml:469`. |
| **Harness** | The scaffolding around the model — tools, caps, gates, cages. | `INFERENCE`/`unverified` — Thariq Shihipar reportedly discusses "harness engineering" on the Minus One podcast, but the researcher **could not get the transcript**. Don't cite it. | **Done, and unusually strong.** Path cage, `--max-turns` (4 workflows), `timeout-minutes`, `concurrency:` (12 of 18 workflows), content-addressed test stamp (`scripts/agent-gate.sh`). |
| **Loop** | Agent acts → an **outside** signal checks → failure goes back in → a hard stop exists. | `VERIFIED` (Bouchard; Anthropic loops blog) | **Done, 16 times.** But see the honest correction below — our feedback edge is missing on the main one. |
| **Graph** | More than one loop, composed: a router picks the path, nodes spawn nodes, state is shared. | `VERIFIED` (MarkTechPost; AI Builder Club; Google ADK) | **Not started, on purpose.** Our 16 loops are a hand-wired chain of separate workflow files. No router, no spawning, no shared state object. |

### Where we are further along than the material

`VERIFIED` — three places:

1. **Bouchard's own bar for a real loop:** "Asking a model to review its own answer is still useful, but it is not external verification." He treats this as the thing most teams skip. We do it: `repair.yml:353-381` reverts every test-file edit and re-runs against the **ORIGINAL** exam, and `repair.yml:445` is a separate agent with zero builder context. The comment in our own file says why: *"The builder cannot talk it round, because they never meet."*
2. **DeepMind's AlphaEvolve principle** — self-improvement is only trustworthy when scored by *automated deterministic evaluators*, not by an LLM judging an LLM. We arrived at this independently: `scripts/already_built.py`, `scripts/product_assertions.py`, `scripts/agent-gate.sh`.
3. **DRILL entrances.** Anthropic's loop material never mentions deliberately test-firing a loop. We have `workflow_dispatch` in **16 of 18** workflow files. This appears to be ours, not theirs.

### One honest correction to your own inventory

`VERIFIED` — you said "Every loop has a workflow_dispatch DRILL entrance." Two files don't: `ci.yml` and `codeql.yml` (both PR-triggered gates, arguably not loops). Everything else has one; `absence.yml`, `doc-sync.yml` and `triage.yml` have multiple.

---

## 2. WHAT ANTHROPIC PUBLISHES vs WHAT THE COMMUNITY CLAIMS

### Real, on-the-record, checkable

`VERIFIED`

- **Four loop patterns**, named on `claude.com/blog/getting-started-with-loops` by Delba de Oliveira and Michael Segner. Details in §3.
- **"define done, set a budget, add a checkpoint"** — Mark Nowicki, Applied AI @ Anthropic, webinar page.
- **More than 80% of code merged into Anthropic's codebase was authored by Claude** (May 2026); typical engineer merges **8x** more code/day than 2024; a March 2026 poll of 130 research staff self-reported a **median 4x** output rise. Source: `anthropic.com/institute/recursive-self-improvement`.
- **Multi-agent costs ~15x the tokens** of a single chat, and **token spend alone explains 80% of performance variance**. Source: Anthropic's own multi-agent research engineering post.
- **Cara Phillips (Anthropic):** multi-agent coordination overhead is **3-10x tokens**; "Start with the simplest approach that works, and add complexity only when evidence supports it."
- **Boris Cherny (creator of Claude Code):** *"I don't prompt Claude anymore... My job is to write loops."* Real, named, on record. But treat the scale as a moving anecdote — Feb 2026 he said 10-30 PRs/day; Jul 2026 he said "hundreds, sometimes thousands" of parallel Claudes.
- **Thariq Shihipar (Anthropic):** *"When you say Claude can run for eight hours, what you're really saying is Claude can spend like 500 bucks."*

### Fake / untraceable — name it and never repeat it

`UNVERIFIED — DO NOT CITE`

**"90% of Anthropic engineers use loops and 'dreaming' to build self-improving agentic systems."**

This is engagement bait. The research found **four mutually contradictory versions**:

| Number | Attributed to | Posted by |
|---|---|---|
| 80% | "Anthropic Head of AI Lab" | anon |
| 90% | "an Anthropic engineer" | @0xMovez |
| "over 90%" | "Anthropic Managed Agents Lead" | @AnatoliKopadze |
| 99% + "swarms of 300+ agents" | "Anthropic research lead" | @0xCodez, @DataChaz |

Four numbers, four job titles that don't verifiably exist, zero primary source, several posts pushing a paid course. It looks like a garbled restatement of the **real** 80% figure — which is about *how much code Claude wrote*, not *how many engineers use loops*. Completely different claim.

Also unverified: the "graph engineering" trend itself started from **one Peter Steinberger tweet** — *"Are we still talking loops or did we shift to graphs yet?"* — with no framework, model or capability shipped alongside it. Even that quote is second-hand; x.com returned HTTP 402 to the researcher. `@PawelHuryn`'s reply is worth keeping in mind: *"I call BS on graph engineering."*

---

## 3. THE FOUR LOOP PATTERNS — which of ours is which

`VERIFIED` — the four names are Anthropic's; the mapping to our files is `INFERENCE`.

| Anthropic pattern | What it means | Ours |
|---|---|---|
| **Turn-Based** | Human prompts, Claude stops when it thinks it's done. | **We have none in CI, correctly.** This only happens in a live session. Putting a turn-based loop in a repo would mean a bot that stops when it feels finished. |
| **Time-Based** | Runs on a clock. | `absence.yml`, `product-health.yml`, `checker-scorecard.yml`, `dependabot-auto.yml`, `db-backup.yml`, `uptime.yml`, `synthetic-live.yml`, `doc-sync.yml`. Well covered. |
| **Proactive** | Event-triggered, no human watching in real time. | `triage.yml` (issue opened), `repair.yml` (`agent:fix` label), `pr-repair.yml` (PR goes red), `pr-shepherd.yml`. Well covered. |
| **Goal-Based** | An **evaluator model checks the stop condition every time the agent tries to quit**, and sends it back to work until the goal is met. | **This is the one we're missing.** We have the evaluator (blind checker, `repair.yml:445`) but not the *send-it-back*. |

**The exact gap, in our own words.** `repair.yml:452` says the verdict *"gates nothing"*. And when a repair fails, `repair.yml:~608` writes: *"Re-apply the `agent:fix` label to try again."* A **human hand** closes that loop. Anthropic's Goal-Based pattern and Google ADK's Generator-and-Critic pattern both close it automatically, with a max-iteration cap and an `escalate=True` early exit.

That is not a graph. That is one missing arrow on a loop we already built.

---

## 4. IS A GRAPH WORTH IT FOR US?

**No. Not yet. And for our workload, possibly never.** This is a recommendation, not a hedge.

### The case against, from primary sources

`VERIFIED` — Anthropic's own multi-agent engineering post says, plainly:

> "Most coding tasks involve fewer truly parallelizable tasks than research, and LLM agents are not yet great at coordinating and delegating to other agents in real time."

Our cognitive loops are **repair, triage, PR-fixing** — coding tasks. The exact category Anthropic names as a bad multi-agent fit. Graph-composing them would move *against* first-party advice, not catch up to a trend.

`VERIFIED` — Google's ADK production guidance says the same from the other side:

> "Do not build a nested loop system on day one. Start with a sequential chain, debug it, and then add complexity."

A fixed hand-wired chain is not our shortfall. It is **both vendors' recommended starting point**, and we are already at the far end of it.

### What a graph would actually buy

`VERIFIED` framing, from MarkTechPost — production graph systems run **two** graphs: a stable **org graph** (long-lived named roles) and an ephemeral **work graph** (task nodes that split, merge, and cancel mid-run based on evidence).

**We already have the org graph.** 16 stable named roles in `.github/workflows/`. What we lack is the work graph: no loop of ours forks based on what it discovers halfway through. Every one runs its fixed chain top to bottom.

For a one-person team, a work graph buys: parallel fan-out when a repair touches five files, and a router that picks a cheap model for a typo and an expensive one for an IDOR bug.

### What it would cost

`VERIFIED` numbers: **15x tokens** (Anthropic's measurement), **3-10x coordination overhead** (Cara Phillips), plus Anthropic's own observed failure modes — agents spawning 50 subagents for a simple query, and non-determinism between runs with identical prompts making debugging harder.

For a solo founder, that last one is the killer. Our whole safety model is *deterministic gates around a non-deterministic core*. A graph makes the core bigger and the determinism thinner, exactly when you have no team to debug it.

### What would change the answer

Build a graph when — and only when — one of these is true, measured, not felt:

1. `repair.yml` regularly needs work on 3+ independent files that genuinely don't share state.
2. We start paying real money for cheap fixes that a Haiku node would have handled (needs the cost meter in §5 to even detect).
3. The blind checker becomes overloaded — judging correctness *and* security *and* tenancy *and* style in one pass and getting worse at all four. AI Builder Club names "an overloaded verifier" as the strongest signal to split a loop into a graph. We already split it once (builder vs checker); the next split would be checker-by-concern.

Until one of those fires, adding a graph is buying 15x tokens for a problem we do not have.

---

## 5. NEXT 3 THINGS

### 1. Close the critic loop (Goal-Based edge-back)

**Build:** when the blind checker returns *reject*, feed the verdict back to the repair agent and let it try again — with a hard cap of 2 retries and an early-exit if the checker says approve. Same for a failed verify.

**Reuses:** the whole existing chain. Blind checker prompt at `repair.yml:445-500`, the source-only re-verification at `repair.yml:353-381` (which already protects against a retry that cheats by editing tests), the existing `--max-turns` and `concurrency:` caps. No new script.

**Effort:** small-to-medium. One retry loop + a counter + one label. A day.

**Source:** `VERIFIED` — Anthropic's Goal-Based Loop pattern ("an evaluator model checks your condition and sends it back to work until the goal is met") and Google ADK's Generator-and-Critic ("Executes until feedback equals PASS... agents can signal early completion via `escalate=True` before max_iterations").

**Why first:** it is the single highest-value move because it is not a graph — it is one arrow on a loop we already own, and it deletes the human hand currently required to type "re-apply the label". Merge authority stays with you; the trust ladder is untouched.

---

### 2. Put a dollar meter on every loop

**Build:** capture token/cost per LLM run, write it to a small file per run, and add a per-loop cost ceiling that trips before the timeout does. Report it weekly.

**Reuses:** the `checker-scorecard.yml` shape exactly — a weekly cron that reads run history and writes a scorecard (`scripts/checker_scorecard.py`). Same pattern, different metric.

**Effort:** small. Half a day.

**Source:** `VERIFIED` — Thariq Shihipar: *"When you say Claude can run for eight hours, what you're really saying is Claude can spend like 500 bucks."* Backed by Anthropic's finding that **token spend alone explains 80% of performance variance**.

**Why second:** every cap we have is turns or minutes (`--max-turns` in 4 workflows, `timeout-minutes` everywhere). Neither is money. A loop can burn a full timeout on an expensive model, produce nothing, and trip no alarm. This is also the **instrument that makes §4's decision answerable with numbers** instead of vibes — you cannot judge whether a router is worth 15x tokens until you know what 1x costs today.

---

### 3. Structured run records (trajectory, not just outcome)

**Build:** every cognitive loop run writes one structured record — goal, files touched, tools used in order, verdict, retries, cost, final state. Append-only. Not the Actions log; a parseable record.

**Reuses:** `scripts/checker_scorecard.py` already reads verdicts after the fact; this gives it a real source instead of scraping. `scripts/absence_check.py` and `scripts/watchdog_check.py` get a cleaner signal too.

**Effort:** medium. Two to three days including backfilling the four LLM workflows.

**Source:** `VERIFIED` — Google Vertex AI's agent evaluation docs grade **the trajectory** (which tools, in what order), not only the final answer. Reinforced by Annie Wang (Google Cloud DevRel): *"The quality of job memory depends on the quality of the outcome record."*

**Why third and not skipped:** this is the prerequisite for both things you said you want later. A router needs history to route on. Self-improving memory needs a trajectory to distil from. Right now that history exists only as Actions run logs, which expire and can't be queried. Build the record before building anything that would read it.

### Explicitly deferred (and why)

**Self-improving memory / "dreaming."** `VERIFIED` — Annie Wang's warning is the reason to wait: *"Reflection can preserve a wrong conclusion... [generated memory] should be treated as untrusted until it passes checks."* If we let an agent write to `MEMORY.md` or `CLAUDE.md`, that write needs its own verification gate — structurally a test-tamper guard for memory. That is a real project, and item 3 is its foundation. Also note: our repair loop is already caged out of editing its own guards. Letting it edit the rules file would punch a hole in that cage.

---

## 6. WHAT I COULD NOT VERIFY

**From the research (flagged by the researchers themselves):**

1. **The "90% of Anthropic engineers use self-improving loops" stat** — no primary source exists. Four contradictory versions (80/90/90+/99%) from four anonymous accounts citing four unverifiable job titles. Never repeat it.
2. **Thariq Shihipar's Minus One podcast episode** (harness engineering, why they stopped using Plan Mode, RAG as anti-pattern) — transcript not retrievable. The episode description exists; the actual claims do not, for us. Do not cite its specifics.
3. **The Chase AI "Graph Engineering Is Now Here" video** — the fetch returned only YouTube nav boilerplate. The definition credited to it came from search snippets, not the video.
4. **Peter Steinberger's originating tweet** — x.com returned HTTP 402. The quote and the "2.9M views" figure are both second-hand.
5. **The Mark Nowicki loops webinar recording** — not public at fetch time. The "define done / set budget / add checkpoint" framing is from the event page, and the four-pattern mapping to it is inferred from the companion blog, not confirmed word-for-word from the talk.
6. **Two Google claims marked secondary** — the Vertex AI trajectory-eval quote and the Gemini staged-rollout guidance were pulled from search renderings, not direct fetches of the live pages.
7. **Google's Scion "harness" docs** — lives on GitHub Pages under the GoogleCloudPlatform org, not `cloud.google.com`. Official status unclear.

**From my own check of our repo:**

8. I verified **file presence and specific lines** in `.github/workflows/` and `scripts/`. I did **not** run any loop, read any Actions run history, or measure actual token cost. So the claim "our loops work" is your report, not my measurement.
9. I read this **worktree** (`.claude/worktrees/loops+fix-dead-watchers`), not `main`. Per our own memory rule ("check the RIGHT INSTANCE — main's copy not the worktree's"), the workflow files on `main` may differ. Several are dated Aug 1 in this tree.
10. **Effort estimates in §5 are guesses**, not measured against anything.