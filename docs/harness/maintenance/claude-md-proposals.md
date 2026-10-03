# CLAUDE.md drift — the opt-in signal for the proposal hook
<!-- doc: LIVING -->

**This file is a switch, not an inbox.** Its mere presence is what lets
`.claude/hooks/claude-md-proposal.sh` fire (it checks for this path and exits
silently when it is absent). The hook never writes here, and nothing reads
what you write here.

**Where a proposal actually goes:** a GitHub issue labelled
`claude-md-drift`. The hook's `REASON` block is the authoritative shape and
command sequence — read it there. `.github/workflows/claude-md-apply.yml` is
the one designated applier: it collects the open owner-authored issues, has a
capped agent verify each claim against the repo, and opens a single PR
touching `CLAUDE.md`. The owner merges it.

**Never edit `CLAUDE.md` from a session doing something else** — the hook
states this as a hard rule, and the per-session fallback it names when `gh` is
unavailable is a gitignored file, not this one.
