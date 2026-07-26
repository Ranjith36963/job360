<!-- doc: LIVING | status: DRAFT — NOT IN FORCE | owner: human | supersedes-when-accepted: doc-audit/SKILL.md:94, DOC-MAINTENANCE.md:44,68 -->

# Deletion Policy — what dies automatically, what needs a human

> ## ⚠️ STATUS: DRAFT — NOT IN FORCE. DO NOT AUTOMATE YET.
>
> This document **authorises automated deletion**. It has **not** passed adversarial
> review — the review agent hit a session limit before completing (2026-07-26). Until a
> hostile reviewer has hunted for the data-loss hole in every Lane-A predicate and the
> owner has accepted it, this is a **proposal**, not law.
>
> Nothing here is wired to anything. `scripts/branch_prune.sh` exists and is dry-run by
> default; no reaper, no merge hook, and no CI check has been created.
>
> **Before this becomes law, required:**
> 1. Adversarial review of every Lane-A predicate for unrecoverable-loss scenarios
>    (specifically: A3 vs stashes/submodules/broken worktree links; A5 glob false
>    positives; A7 "which copy is canonical"; B1 vs reverted PRs; concurrency — the
>    reaper running while 6 sessions are live, with no lock).
> 2. Verification (not assumption) of the recovery claims: reflog coverage for a branch
>    deleted from a *different* worktree, reflog survival across `git gc`, and whether
>    GitHub's restore button truly covers every A2 case.
> 3. Owner acceptance of §6 (the repeal).
>
> **When accepted:** change status to `ACTIVE`, delete this banner, and record the
> acceptance date + reviewer in §6.

> **Intent once in force:** this file is law. It **repeals** the no-delete rules listed in
> §6. Where this file and any skill/doc disagree, this file wins.

## 0. Why this exists

The 2026-07-26 hygiene audit (5 agent teams + an adversarial critique) measured:

- **75%** of sampled plan docs describe work **already shipped** — leftover paperwork, not plans
- **37%** of 136 tracked docs have **zero inbound references**
- **51%** were created and last-touched **the same day**
- **107 merged PRs** vs **112 branch refs still alive** — deletion never kept pace with merges
- One stale worktree already caused a **4,348-line clobber** (PR #44)

Root cause, in one line: **creation is distributed and instant; destruction was centralised
on one human who never showed up.** Every agent was *correctly* obeying a written
"never delete any doc" rule written after the 2026-06-21 ralph-loop incident. The pile is
the invoice for that overcorrection.

**The fix is not better judgment. It is deletion by predicate.** A predicate is a
yes/no question a script can answer with no context and no opinion. If a thing can only
be judged, a human judges it. If it can be *checked*, the machine acts.

---

## 1. The three lanes

Everything the repo produces falls in exactly one lane.

| Lane | Who acts | Reversible? | Rule |
|---|---|---|---|
| **A — AUTO-DELETE** | machine, no approval | yes (reflog/GitHub, 90d) | predicate is true → delete |
| **B — AUTO-ARCHIVE** | machine, no approval | yes (git history) | predicate is true → stamp + move, **never delete** |
| **C — HUMAN ONLY** | human decides | — | machine may **report**, never act |

**Default for anything not listed here is Lane C.** Silence is not permission.

---

## 2. Lane A — AUTO-DELETE (machine deletes, no approval)

Each rule states its predicate. All are machine-checkable. All are reversible.

| # | Thing | Predicate (must ALL hold) | Recovery |
|---|---|---|---|
| A1 | **Local branch** | fully merged into `origin/main` · not checked out in ANY worktree · not in `PROTECTED` (`main`/`master`/`develop`) · not the current branch | `git reflog`, 90 days |
| A2 | **Remote branch** | its PR is `merged` | GitHub "Restore branch" button |
| A3 | **Worktree** | its branch satisfies A1 **AND** `git status --porcelain` is **empty** (nothing modified, nothing staged, nothing untracked) | branch still exists until A1 runs |
| A4 | **Worktree metadata** | the directory no longer exists on disk (`git worktree prune`) | nothing to lose — metadata only |
| A5 | **Generated artifacts** | matches a known generator glob (`.mypy_cache*/`, `graphify-out/`, `.playwright-mcp/`, `**/build/`, `*.pyc`) | regenerate by re-running the tool |
| A6 | **Rotating logs** | file is in the rotation list (`STATUS-DAILY.md`, `SCOUT-NOTES.md`, `TELEMETRY.jsonl`) **AND** entry older than 30 days | superseded by design |
| A7 | **Byte-identical duplicate** | `diff` against the canonical path is empty **AND** the copy is outside its canonical directory | the canonical copy still exists |

**Hard constraints on every Lane-A action:**
- Branch deletion uses `git branch -d` — **never `-D`.** Git itself refuses anything unmerged.
- **Never** rewrite history. `filter-branch`, `filter-repo`, force-push: forbidden, always, Lane C+.
- **Never** delete anything that exists in only one place. Push first, delete second (§4).
- Every Lane-A run writes what it deleted to the weekly report.

---

## 3. Lane B — AUTO-ARCHIVE (machine moves, never deletes)

Completed work keeps its record. It just stops occupying the working space.

| # | Thing | Predicate | Action |
|---|---|---|---|
| B1 | **Plan doc** | the PR named in its header is `merged` | stamp `> **IMPLEMENTED** in PR #N (sha) — archived <date>`, `git mv` → `docs/archive/plans/`, fix referrers |
| B2 | **Superseded audit/snapshot** | a newer doc declares "supersedes \<this\>" | `git mv` → `docs/archive/` |
| B3 | **Root-level stray** | a `.md` at repo root not on the root whitelist (§5) | `git mv` → `docs/` or `docs/archive/` |
| B4 | **Dead framework file** | describes a process with zero runs and no scheduler wired | `git mv` → `docs/archive/`, one-line banner why |

Archiving is **not** deletion. The file stays in git, readable forever. This is the answer
to "we need a record": **the record is the archive + `CHANGELOG.md` + `IMPLEMENTATION_LOG.md`
— not a live file in `docs/`.**

---

## 4. Lane C — HUMAN ONLY (machine reports, never acts)

If any of these is true, the machine **stops and reports**. No exceptions, no "probably fine".

| # | Never auto-touch | Why |
|---|---|---|
| C1 | **Unmerged branch** — regardless of age | commits may exist nowhere else |
| C2 | **Branch not pushed to any remote** | one disk failure from permanent loss |
| C3 | **Worktree with ANY uncommitted or staged change** | 722 staged lines were found this way in one worktree |
| C4 | **Ground-truth docs** — `CLAUDE.md`, `STATUS.md`, `ARCHITECTURE.md`, `README.md`, `CONTRIBUTING.md`, `SECURITY.md` | the map; fixing ≠ deleting |
| C5 | **Permanent records** — `IMPLEMENTATION_LOG.md`, `CHANGELOG.md`, `docs/decisions/`, `docs/reviews/`, `docs/research/` | append-only history, archive-never-delete |
| C6 | **Active design for unshipped work** | it's a promise, not exhaust |
| C7 | **Anything holding real personal data** — `User_info/`, `.env`, `backend/data/` | irreplaceable + PII |
| C8 | **History rewrite of any kind** | irreversible, breaks every clone |
| C9 | **Anything a predicate can't answer** | if it needs judgment, it needs a human |

**Escalation, not deletion:** an unmerged branch silent for **21 days** → the reaper opens an
issue naming it. **7 more days** silent → it may move to Lane A *only if* it is pushed to a
remote (C2 satisfied). Otherwise it stays in Lane C forever.

---

## 5. Registration at birth (this is what makes it work)

Deletion by predicate only works if the predicate exists. It is written **when the file is
created**, not re-derived later.

1. Every new `.md` carries a line-1 header: `<!-- doc: PLAN|LIVING|RECORD|DESIGN | status: ACTIVE | pr: — -->`
2. CI **rejects** a new `.md` with no header, and any `.md` at repo root not on the whitelist:
   `README.md`, `CLAUDE.md`, `ARCHITECTURE.md`, `STATUS.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`
3. When the PR that cites the doc merges, the workflow fills `pr: #N` and B1 fires.

This is the permanent answer to *"is this implemented or stale?"* — **you never have to ask
again.** The doc says so itself, and a machine wrote that answer.

---

## 6. What this repeals

| Repealed | Was | Now |
|---|---|---|
| `.claude/skills/doc-audit/SKILL.md:94` | "**Never do:** delete any doc." | Lane A/B act without approval; Lane C still needs a human. |
| `DOC-MAINTENANCE.md:44` | implemented plan "is **never deleted** (history)." | Superseded by B1 — archived, which *is* the history. |
| `DOC-MAINTENANCE.md:68` | "never silently delete an intention." | Intentions (C6) stay Lane C. Completed work is not an intention. |
| `doc-audit/SKILL.md:9` | Phase B blocked pending per-doc owner approval. | Lane A/B need no approval. Phase B applies to Lane C only. |

**Why repeal is safe:** every Lane-A action is reversible (reflog 90 days, GitHub restore,
regenerable artifacts) and every Lane-B action keeps the file in git forever. The only
irreversible operations (history rewrite, deleting sole copies) are Lane C and stay there.

---

## 7. Who runs it

| Trigger | Runs | Lane |
|---|---|---|
| **PR merge** | delete branch · remove clean worktree · stamp+archive its plan doc | A1,A2,A3,B1 |
| **Weekly cron** | `branch_prune.sh` · `worktree prune` · log rotation · report Lane-C items | A1,A3,A4,A6 + C report |
| **CI on every PR** | reject headerless/stray `.md` | §5 |
| **Human, on the weekly report** | decides Lane C only | C1–C9 |

The human's job shrinks to one thing: **reading a weekly list of Lane-C items.** Everything
else is already handled, correctly, without them.

---

## 8. One-line summary

> **Merge is the destructor. If a script can prove a thing is done, the script removes it —
> completed work is archived, generated junk is deleted, and only unmerged, unpushed, or
> uncommitted work ever waits on a human.**
