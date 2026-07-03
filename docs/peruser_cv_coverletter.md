# Per-User AI CV & Cover Letter — tailored generation that learns from you

> **The feature in one line:** When you apply to a job, the AI takes your existing CV + that job's description and produces a **CV and cover letter tailored to that job** — then it **learns from how you edit them**, per user, so it keeps getting better at writing the way *you* write.

---

## 1. What it does (user flow)

1. On a job, you click **"Tailor my CV"** (or Apply).
2. The AI reads **your profile/CV** (already stored) + **that job's description** (already stored) + **the judge's "why it fits" reason** (already computed).
3. It generates:
   - a **CV rewritten/optimized for that job** (reorders, rephrases, highlights the right skills/experience — **never invents**), and
   - a **matching cover letter**.
4. You **review + edit** them into your final, polished version.
5. You **download** them and apply on the company's own site.
6. Your polished final is **fed back** → the system learns your style for next time.

It's a *helper*, not an auto-submitter. Every application gets a custom CV + cover letter from the one CV you already uploaded.

---

## 2. What we ALREADY have (~80% built)

| Piece | Status | Where |
|---|---|---|
| Full CV text (not just skills) | ✅ stored | `cv.raw_text`, `cv.linkedin_raw_text` |
| Parsed profile (skills/experience/education) | ✅ stored | `CVData` / `user_profiles` |
| The job's full description | ✅ stored | `Job.description` |
| AI generator (Cerebras/Groq/Gemini) | ✅ wired | `services/profile/llm_provider.py` |
| "Why this job fits" analysis | ✅ built | the judge (E4) outputs a `reason` — tells us what to emphasize |
| PDF / template tooling | ✅ present | `fpdf2` installed + `jinja2` |
| Apply action + applications pipeline | ✅ built | Kanban + `user_actions` "applied" |
| Background worker | ✅ running | ARQ (generate without blocking the UI) |
| Profile versioning | ✅ built | stamp which profile made the doc |

**The hard parts already exist** — your CV, the job text, the AI, the fit analysis, and the PDF tools are all here.

## 3. What we ADD (~20% gap)
1. A **"tailor CV + cover letter"** LLM prompt (input: your CV text + job description + judge's fit reason).
2. **Templates** (jinja2 → HTML → PDF via fpdf2), **ATS-friendly** (plain, machine-readable — fancy layouts get auto-rejected).
3. A **table** to store generated + polished docs per `(user, job)`.
4. **Frontend:** "Tailor my CV" button on each job → view, **edit**, download the CV + cover letter.

---

## 3.5 Where it shows in the frontend

Plugs into **3 existing screens** — no new top-level page needed. The only new UI is the generated-doc editor/preview panel.

| Screen | What appears |
|---|---|
| **Job detail** (`frontend/src/app/jobs/[id]`) | Main spot: a **"Tailor my CV"** button next to Apply → opens the generated **CV + cover letter** in an **editable preview** → edit → **download** |
| **Dashboard** (`frontend/src/app/dashboard`) | A quick **"Tailor"** action on each job card (alongside Apply / Like / Skip) — no need to open the job first |
| **Pipeline / Kanban** (`frontend/src/app/pipeline`) | Once you apply, the **tailored CV + cover letter attach to that application card** so you can find them again |

**In one line:** button on the *job* (dashboard card + job page) → new *editor/preview* view for the output → saved copies live on the *Kanban application*.

---

## 4. Guardrails — the things NOT to miss (or it backfires)

1. **Paid / capped.** Each generation = an AI call = money. Free users spamming it explodes the LLM bill. Make it **premium (or X free/month)** → ties into the paywall (Phase 4).
2. **Never lie.** The AI may only **reorder / reword / highlight what's TRUE** in your CV — it must **never invent** skills or jobs. A fabricated CV gets you rejected or fired. Lock the prompt to "reshape, don't fabricate."
3. **Always editable.** Nobody sends raw AI output. Flow is **generate → you review/edit → then use.**
4. **We can't auto-apply.** "Apply" opens the company's site; we generate the docs, **you** submit them there. Set that expectation.
5. **ATS-friendly output.** Companies scan CVs with software → output clean, plain, machine-readable format.

> Guardrails **#2 (don't lie)** and **#3 (let me edit)** are the whole *trust* of the feature. Nail those, not the PDF styling.

---

## 5. The edit-feedback loop — your edits are the teacher ⭐

The AI draft is **not** the finished thing. You polish it into your final version — and that final is **gold**:

```
AI drafts CV/cover letter
      │
      ▼
YOU review + edit → your polished FINAL
      │
      ▼
Feed the final back → system learns the DIFF (AI draft → your final)
      │
      └──── next draft is closer to how you'd write it → repeat, forever improving
```

The **diff between the AI draft and your polished final is the highest-quality training signal there is** — a human saying "here's better." (It's literally how the best AI is trained.) The generator's writing keeps rising, from real usage, for free.

**Only learn from KEPT docs.** If you generate a draft and abandon it, that's a "bad" signal — only learn from ones you **finalized / downloaded / used**.

**Same loop, independently, for the cover letter.**

---

## 6. Per-user learning — a 2-layer setup

Different people write differently (honest vs bold, heavy-editor vs "ship it", formal vs warm). One "average" style fits nobody. But pure per-user has a cold-start problem (a new user has no history). So **both**:

**Layer 1 — Universal base (shared, PATTERNS ONLY):**
- Learns general "good tailoring" from everyone → a new user gets a solid CV on **day 1**.
- ⚠️ **Structure/style patterns only — NEVER personal content.** (privacy)

**Layer 2 — Per-user personalization (YOUR data only) ⭐:**
- Sits on top. Learns **your** style from **your** edits — private + personal.
- The more you use it, the more it becomes *your* assistant.

> **Universal makes it good. Per-user makes it yours.**

**CV and cover letter are SEPARATE per-user memories** — different documents, different voices (formal CV, warm cover letter). Learn each from its own edits.

### What the per-user layer learns (your "editing fingerprint")
- How much you edit (heavy reviser vs "ship it")
- Honesty level (plain vs bold)
- Tone (formal / conversational)
- What you always add or cut

---

## 7. Privacy (non-negotiable)
- **Per-user layer:** your polished CV trains **only your layer**, nobody else's.
- **Universal layer:** learns **shape only** (how tailoring works) — never memorizes or leaks any user's content/PII.
- Get "learn the pattern, not the person" wrong → it's a lawsuit, not a feature. Get it right → it's unbeatable.

---

## 8. Build sequence
1. **Generator + editor first** (MVP): prompt + templates + edit UI + download. Gate it as premium/quota.
2. **Store the polished finals** (per user, per job) — the raw material for learning.
3. **Per-user layer** (Layer 2): retrieve the user's past polished CVs/edits as few-shot examples in the prompt → immediate personalization, cheap.
4. **Universal layer** (Layer 1): learn shared patterns (patterns-only, privacy-scrubbed) → improves cold-start.
5. **Cover-letter loop** mirrors the CV loop, independently.

*(Few-shot / example-retrieval is the cheap immediate version; a fine-tune is a later optimization.)*

---

## 9. One-line summary
> **Turn your one CV into a tailored CV + cover letter per job — then learn from every edit you make, per user (CV and cover letter separate), on top of a shared patterns-only base.** It's ~80% built already (your CV, the job text, the AI, the fit reason, the PDF tools). The value is the loop: it writes more like *you* every time — a paid feature that compounds. Guardrails: don't lie, stay editable, learn patterns-not-people.
