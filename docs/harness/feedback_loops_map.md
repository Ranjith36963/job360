# Feedback Loops Map — where the tool can learn from itself
<!-- doc: REFERENCE -->

> **REFERENCE — pinned to its research date.** External facts move; re-check anything load-bearing. <!-- banner: auto -->

> **The idea in one line:** A feedback loop turns a static corner of the tool into a self-improving one. This is the honest, whole-tool map of **where** loops belong — only corners with a **real signal** (something the user already does) and a **gate** (a check that keeps junk out). More *gated* loops = closer to perfect; ungated loops self-poison, so this list is deliberately selective.

Companion docs:
- `raw_feedback_loop.md` — the data loops (skills / companies / domains / geo / weights) in depth.
- `peruser_cv_coverletter.md` — the CV + cover-letter tailoring loop (learn from your edits).
- `post_application.md` — interview prep / mock / follow-up / outreach.

---

## The map

Strength is honest: 🟢 = real signal + clean gate (build first) · 🟡 = real but noisier (build later, watch the gate).

| # | Corner (where the loop lives) | Pillar / Feature | Learns from (the signal) | Gate (keeps it clean) | Strength |
|---|---|---|---|---|---|
| 1 | **Skills vocabulary** (`skill_synonyms`) | Pillar 1 | LLM-extracted skills from CVs/jobs | ESCO match / seen ≥ N | 🟢 Strong |
| 2 | **Profile extraction** accuracy | Pillar 1 | user *corrects* their extracted profile | user confirms the edit | 🟡 Medium (needs an "edit my profile" UI) |
| 3 | **Domain map** (`domain_classifier`) | Pillar 1→2 | LLM classifies new domains | seen ≥ N across CVs/jobs | 🟡 Medium |
| 4 | **Company list** (`companies.py`) | Pillar 2 | jobs naming companies you don't scrape | ATS endpoint verified live | 🟢 Strong |
| 5 | **Location / geo** (UK/foreign lists) | Pillar 2 | LLM classifies unseen places | confidence + dedup | 🟡 Medium |
| 6 | **Scoring weights** | Pillar 2 | `user_actions` (liked/applied/skipped) | bounded, slow nudge | 🟢 Strong signal / ⚠️ risky |
| 7 | **Judge (E4) calibration** | Pillar 2 | do users apply to high-judged, skip low? | aggregate agreement | 🟡 Medium |
| 8 | **Feed / dashboard ranking** | Pillar 3 | what you click / like / hide | per-user | 🟢 Strong |
| 9 | **Notification timing + content** | Pillar 3 | open rate + click-through | per-user rate cap | 🟡 Medium |
| 10 | **CV tailoring** | CV feature | your draft → final edits | keep-only + per-user | 🟢 Strong |
| 11 | **Cover-letter tailoring** | CV feature | your edits (separate memory) | keep-only + per-user | 🟢 Strong |
| 12 | **Interview prep** questions | Post-app | "was I actually asked this?" + ratings | aggregate | 🟡 Medium |
| 13 | **Mock interview** coaching | Post-app | your answers + your ratings | per-user | 🟡 Medium |
| 14 | **Follow-up / outreach** drafts | Post-app | your edits to the draft | keep-only + per-user | 🟡 Med-strong |
| 15 | ⭐ **Outcome loop (MASTER)** | **ALL** | **did you get the interview / offer?** (Kanban stages: applied→interview→offer) | aggregate across users | 🟢 **Strongest** (slow) |

---

## The one that matters most: #15, the Outcome Loop

Every other loop *guesses* at "good." **#15 knows** — it sees whether you actually **got the interview or the job.** That real outcome can validate everything above it:
- which jobs deserve to rank higher (feeds #6, #7),
- which tailored CVs actually win interviews (feeds #10/#11),
- which interview prep genuinely helped (feeds #12/#13).

It's **slow** (weeks per signal) but it's the **ground truth** the whole tool should ultimately answer to. Build the fast loops first; wire them to the outcome loop as it accumulates data.

---

## Where NOT to add loops (honest)

- **Dedup thresholds** — no natural signal (nobody tells you "you wrongly merged these two jobs"). Leave static.
- **Plumbing** — URLs, regexes, prompts, model names, token TTLs. Not data — it's how the machine works. Never loop.
- **Offer / salary / negotiation** — personal + high-stakes. Optimizing a metric here hurts the human (Goodhart's law). Deliberately cut (see `post_application.md`).

---

## The rule behind the whole map

- **Every signal here is something the user *already does*** — edits, clicks, applies, gets hired. You're not asking users for feedback; you're **reading their behavior.** That's what makes each loop **free** (no extra work for the user) and **honest** (real behavior, not opinions).
- **Every loop MUST have a gate.** No signal or no gate → no loop (an ungated loop makes its corner *worse* every day).
- **Don't loop the personal.** Some things shouldn't chase a metric.
- **Quality of loops beats quantity.** The goal isn't the most loops — it's a gated loop in every corner that has a real signal, all eventually answering to the outcome loop (#15).

---

## Suggested build order (loops)

1. 🟢 **CV + cover-letter tailoring** (#10/#11) — cleanest signal (your own edits), immediate value.
2. 🟢 **Skills vocabulary** (#1) + **Company list** (#4) — the data flywheel; ESCO/ATS gates.
3. 🟢 **Feed ranking** (#8) — from `user_actions` you already capture.
4. 🟡 **Scoring weights** (#6) — high value but risky; bounded + A/B'd.
5. 🟡 The rest (domains, geo, judge calibration, notifications, post-app loops) as signals mature.
6. ⭐ **Outcome loop** (#15) — start capturing stage outcomes *now* (it's slow, so the sooner it collects, the sooner it pays off); wire the others to it over time.

---

## One-line summary
> **15 honest places the tool can learn from itself** — each from behavior the user already produces, each gated, all eventually validated by the master **outcome loop** (did you get the job?). Build the 🟢 ones first, gate every one, never loop the plumbing or the personal. That's the disciplined path to close-to-perfect.
