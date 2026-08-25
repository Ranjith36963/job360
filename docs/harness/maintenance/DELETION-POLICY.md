<!-- doc: LIVING | status: DRAFT — NOT IN FORCE | owner: human -->

# Deletion Policy — what dies automatically, what needs a human

> ## ⚠️ STATUS: DRAFT — NOT IN FORCE. NOTHING IS WIRED.
>
> v2 (2026-07-27). v1 failed a hostile review — **four of its safety claims were
> verified false**, and one rule fired on live data. Those are fixed below. It stays
> DRAFT because the fixes themselves have not been re-reviewed.
>
> **While this reads DRAFT, every item is Lane C** — report only, delete nothing.
> Flipping the word `DRAFT` to `ACTIVE` is the entire switch.
>
> **Kill switch (works even when ACTIVE):** create `docs/maintenance/REAPER-OFF`.
> Every lane goes inert immediately.

## 0. What v1 got wrong (verified, not theoretical)

| v1 claim | Reality |
|---|---|
| "Deleted branches recoverable from reflog for 90 days" | **FALSE.** `git branch -d` deletes that branch's own reflog with it. HEAD reflogs are **per-worktree** — a branch never checked out here has none here. v1 wired A1+A3 together on merge, which destroys *both*. Real grace ≈ **2 weeks** of unreachable objects (`gc.pruneExpire` unset → default), and only if you know the SHA. |
| "`git branch -d` refuses anything unmerged" | **FALSE.** Git deletes a branch merged into its **upstream** even when unmerged into HEAD — warning, exit 0. Every pushed-but-unmerged branch sails through. |
| "A3 is safe because `git status --porcelain` is empty" | **FALSE — fires today.** `.claude/worktrees/feat-cv-coverletter` satisfies A3 right now and holds **836K of gitignored `backend/data/`** that `--porcelain` cannot see. It lives in no branch, no commit, no reflog. `D:/dev/job360-tprun` holds a real `.env`, protected only by the accident that its branch is unmerged. |
| "A1 clears the branch pile" | **FALSE.** 33 merge commits vs **107 merged PRs** — most PRs are squash-merged, so their tips are never ancestors and `--merged` never lists them. ~49 branches would sit in Lane C forever, which is exactly how someone eventually "fixes" the script with `-D`. |

**The corrected axis.** v1 sorted by *object type*. The only question that matters is:
**does a durable copy exist that this action cannot touch, verified at run time?**
If yes → delete. If it cannot be proven → **park** (tag + push), never ticket-and-forget.

---

## 1. The lanes

| Lane | Who acts | Rule |
|---|---|---|
| **A — AUTO-DELETE** | machine, no approval | a durable copy is *proven* to exist elsewhere |
| **B — AUTO-PARK / ARCHIVE** | machine, no approval | cannot prove it's dead → make a copy that outlives it (tag+push / `git mv`) |
| **C — HUMAN ONLY** | human decides | machine reports, never acts |

**Anything unlisted is Lane C. Silence is not permission.**

**Execution constraints — Lane A runs ONLY through `scripts/branch_prune.sh` and the
named merge hook.** No agent or session may delete ad hoc. **Citing this policy grants
no deletion authority.** Every run takes a single lock (`mkdir .git/reaper.lock || exit`),
and any rule finding **more than 10 candidates deletes nothing** and reports instead.

---

## 2. Lane A — AUTO-DELETE

| # | Thing | Predicate (ALL must hold) | Durable copy |
|---|---|---|---|
| A1 | **Local branch** | listed by `--merged origin/main` · **tip is an ancestor of `origin/main`, re-verified per branch immediately before deleting** · not current · not checked out in ANY worktree · not protected · **tip SHA appended to `REAPED.log` first** | the commits are in `origin/main` |
| A1b | **Squash-merged local** | upstream is `gone` · `git diff --quiet origin/main...<branch>` (three dots) is empty · **`git tag reaped/<branch>` pushed first** | the pushed tag |
| A2 | **Remote branch** | its PR is `merged` **AND the remote tip SHA still equals the PR head SHA at merge time** (if it moved after merge → Lane C) | GitHub restore covers the PR head only |
| A3 | **Worktree** | branch satisfies A1 · porcelain empty · **newest mtime of ANY file, ignored included, > 24 h** · **branch merged ≥ 7 days** · removal is `git worktree remove` (**never `--force`, never `rm -rf`**) · **`.env*` and every non-empty ignored data dir copied to `docs/maintenance/quarantine/<name>-<date>/` first, kept 30 days** | quarantine + the branch |
| A4 | **Worktree metadata** | directory absent on **two consecutive** runs **AND its volume root is mounted** | nothing — metadata only |
| A5 | **Generated artifacts** | path is ignored **AND** untracked (`git ls-files --error-unmatch` fails) · **rooted globs only, never `**/`**: `backend/.mypy_cache*/`, `graphify-out/`, `.playwright-mcp/`, `frontend/.next/`, `**/*.pyc` | regenerate |
| A6 | **Rotating logs** | in the rotation list · entry older than 30 days | superseded by design |

**Deleted in v2: A7 (byte-identical duplicates).** "Canonical" is a judgment call, which
C9 already routes to a human. Concretely: `backend/railway.json` and
`backend/railway.worker.json` differ today but share lineage — the day they match, A7
would delete one and break the worker deploy. Duplicates go on the report.

**Never:** rewrite history · delete anything whose only copy is local · use `-D` without a
pushed `reaped/` tag already existing.

---

## 3. Lane B — AUTO-PARK / ARCHIVE (never deletes)

| # | Thing | Predicate | Action |
|---|---|---|---|
| B1 | **Plan doc** | `pr:` header **written by CI, never hand-typed** · PR merged · **not reverted** (no `Revert "…"` of its merge SHA on `origin/main`) · its diff touches/mentions the doc | stamp `IMPLEMENTED in PR #N (<sha>) — archived <date>` **and** `<!-- doc: FROZEN -->`, `git mv` → `docs/_archive/`, then grep the repo for the old path and repair EVERY inbound link |
| B2 | **Superseded snapshot** | a newer doc declares "supersedes \<this\>" | stamp `<!-- doc: FROZEN -->` (or `LOG` for a dated record), `git mv` → `docs/_archive/`, repair every inbound link |
| B3 | **Root-level stray** | `.md` at root not on the §5 whitelist | `git mv` into `docs/` |
| B4 | **Stale-but-unprovable branch** | can't prove merged, can't prove alive | **`git tag park/<branch> && git push origin park/<branch>`**, then drop the local ref only |

B4 is the rule that actually shrinks the pile. **Archive/park is the universal move
whenever "dead" can't be proven** — it costs a tag and loses nothing.

---

## 4. Lane C — HUMAN ONLY (report, never act)

| # | Never auto-touch | Why |
|---|---|---|
| C1 | **Any branch ahead of origin whose extra commits exist on no other ref** — *regardless of age* | time never converts sole-copy work into junk; only a copy does |
| C2 | **Anything never pushed anywhere** — 9 branches today, incl. `worktree-tp-final` (20 commits) | one disk failure from gone |
| C3 | **Worktree with ANY uncommitted or staged change** — or a live stash on its branch (**5 stashes exist right now**) | 722 staged lines were found this way |
| C4 | **Ground truth** — CLAUDE.md, STATUS.md, ARCHITECTURE.md, README, CONTRIBUTING, SECURITY | the map |
| C5 | **Permanent records** — IMPLEMENTATION_LOG, CHANGELOG, decisions/, reviews/, research/ | append-only history |
| C6 | **Active design for unshipped work** | a promise, not exhaust |
| C7 | **Real personal data** — `User_info/`, `.env`, `backend/data/` | irreplaceable + PII. **A3 quarantines these rather than blocking on them — quarantine purges are Lane C.** |
| C8 | **Any history rewrite** | irreversible, breaks every clone |
| C9 | **Anything a predicate can't answer** | judgment needs a human |
| C10 | **Deleting any `park/`, `reaped/`, or manual tag** (e.g. `tp-final-safe`) | those tags *are* the recovery index |

**Escalation never ends in deletion.** Day 21 silent → issue opened. Day 28 with no reply →
**park it** (B4: tag + push, drop the local ref). It stays on every weekly report until a
human deletes the tag. **Silence, age, and your absence are never a yes.**

---

## 5. Registration at birth

Deletion by predicate needs the predicate to exist — written when the file is created.

1. Every new `.md` carries a line-1 header: `<!-- doc: PLAN|LIVING|RECORD|DESIGN | status: ACTIVE | pr: — -->`
2. CI rejects a headerless `.md`, and any root `.md` not in: `README`, `CLAUDE`, `ARCHITECTURE`, `STATUS`, `CONTRIBUTING`, `SECURITY`, `CHANGELOG`
3. The **CI workflow** fills `pr: #N` on merge (never a human) → B1 fires

---

## 6. What this repeals

Repeals the blanket **"Never do: … delete any doc"** from `.claude/skills/doc-audit/SKILL.md`,
and the all-or-nothing Phase-A/Phase-B approval gate in the same file (now per-lane).

*Anchored by quoted text, not line numbers — v1's line-number citations had already rotted
within days.*

**Unchanged and still correct** in `DOC-MAINTENANCE.md`: *"archived plans are never deleted"*
(that is Lane B — archived **is** the record) and *"never silently delete an intention"*
(Lane C6 — unshipped plans stay human-only, permanently).

**Why repeal is safe *now*:** every Lane-A action writes its recovery index first
(`REAPED.log` SHA, or a pushed `reaped/`/`park/` tag, or a quarantine copy) — **not** the
reflog, which v1 wrongly relied on. Lane B never deletes. Irreversible operations stay
Lane C. And Lane A executes only through one locked, capped script — the guard ralph-loop
lacked when it wiped worktrees unsupervised on 2026-06-21.

---

## 7. Who runs it

| Trigger | Runs | Lane |
|---|---|---|
| **PR merge** | delete branch · quarantine+remove clean worktree · stamp+archive its plan doc | A1,A2,A3,B1 |
| **Weekly cron** | prune · park stale · rotate logs · report Lane C + stashes + duplicates | A1,A1b,A4,A6,B4 |
| **CI per PR** | reject headerless/stray `.md`; fill `pr:` on merge | §5 |
| **Human** | Lane C only | C1–C10 |

---

## 8. One line

> **Auto-delete only what the script can prove exists somewhere it cannot reach.
> Auto-park what it cannot prove. Never let silence, age, or your absence stand in for
> your yes.**
