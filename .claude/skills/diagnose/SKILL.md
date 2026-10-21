---
name: diagnose
description: Patient root-cause diagnosis BEFORE any fix. Use whenever something misbehaves — a harness loop gives up, a check is red, a workflow fails, prod errors, "why does X keep happening". Not for trivial typos. Output is a proven cause with evidence, then (and only then) a fix plan.
---
<!-- doc: LIVING | last-verified: 2026-10-02 -->

# Diagnose — find the real culprit before touching code

Owner rule (2026-10-02): "Diagnosing is the biggest part. Patience, plan,
find the real culprit." The failure this prevents: guessing a cause from a few
data points, fixing it, and discovering in review that the guess was wrong
(each wrong round costs a ~20 min CI cycle).

## The six steps — do not skip, do not reorder

1. **Symptom, exactly.** One sentence of WHAT is observed, with the instance
   (PR #, run ID, timestamp). Not a cause. "PR #703 got `autofix:exhausted`
   at 18:38, 1 minute after attempt-1" — not "the fixer is broken".

2. **Evidence, complete — before any theory.** Collect the full timeline for
   EVERY affected instance, not one: what triggered each run, when it started
   and ended, its conclusion, the failing step and its exact error line, what
   the agent was given and what it decided. Put it in one table. If the
   symptom hit N instances, the table has N rows. Delegate the gathering to
   cheap workers (Haiku/Sonnet); read-only.

3. **Hypotheses, at least three.** Write each as a falsifiable claim:
   "If H is true, then in the evidence we must see E". Include the boring
   ones (config, permissions, timing, a merge that landed mid-run).

4. **Test every hypothesis against EVERY row.** A hypothesis that explains
   3 of 4 instances is NOT the cause — find what explains the 4th. Mark each
   PROVEN / REFUTED / UNEXPLAINED, citing the row. Prefer one experiment that
   splits the hypotheses (re-run with one variable changed) over more reading.

5. **Root cause, stated with proof.** The cause, the file:line where it
   lives, and the evidence rows that prove it. Say what is still inferred.
   If two causes stack, name both and which one dominates.

6. **Fix plan, then fix, then prove.** The fix targets the proven cause, not
   the symptom. Before merging, reproduce the original symptom against the fix
   (a drill / re-run) and show it no longer happens. "CI is green" is not
   proof the symptom is gone.

## Guardrails
- No code edits before step 5 is written down.
- Never answer "why" from memory or from the code alone — the runtime evidence wins.
- Read-only against prod; never print secret values.
- Report to the owner in plain words: symptom → cause (with proof) → fix.
