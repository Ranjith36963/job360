# Production LLM — provider choice for Job360

> **One-line answer:** For a *single* provider → **Gemini 2.5 Flash** (GDPR-safe for CV data, does all 4 workloads, reliable). For *cost-optimal* → **Gemini (free, PII/CV work) + DeepSeek (cheap, batch of public job-text)**. The root problem to fix first: **stop running production on free tiers** — their per-minute walls are what cause the 429s.

Researched + cross-checked against official provider docs, 2026-07-08. Every price is vendor self-report — treat as directional; **no independent benchmark exists.**

---

## 1. Why we get 429s (the actual cause)

The whole chain (Cerebras → Groq → Gemini) runs on **free tiers**, and every free tier has a brutal **per-minute wall**:

- **Cerebras free = 5 requests/min, 30K tokens/min.** The **5 RPM** is the killer — inference is sub-second, so a batch of 30 fit-judge calls blows past 5/min in the first second. **This is the direct cause of the batch 429s.**
- **Groq free = 30 RPM, 6–12K TPM.** 30 RPM exactly equals a 30-job batch → zero retry headroom.

> The fix is **not** "add backoff." It's **move batch work off per-minute-token providers** and **use paid Developer tiers** (free to unlock with a card, ~10× the limits). Running production on a free tier is the root problem, not the specific provider.

---

## 2. The app's 4 LLM workloads

| # | Workload | Shape | Sensitivity |
|---|---|---|---|
| 1 | **CV / LinkedIn / GitHub extraction** → strict JSON | ~5–10k in, JSON out | **PII + quality** (personal data — GDPR) |
| 2 | **Per-job enrichment** → 18-field JSON | BATCH (dozens/run), concurrency ~10 | throughput; public job text (no PII) |
| 3 | **Fit judge** → 0–100 score + reason | BATCH (≤30/user/run), concurrency ~3 | throughput + light reasoning; **where the 429 bites** |
| 4 | **CV + cover-letter generation** | ~2–4k tokens out | **quality + PII**, user-facing (must not sound robotic) |

Key split: **#1 and #4 touch personal data (GDPR); #2 and #3 are public job text.**

---

## 3. The key finding: DeepSeek has no per-minute wall

**DeepSeek limits by concurrent connections (~2,500), NOT tokens-per-minute** (official: `api-docs.deepseek.com`). A batch of 30 at concurrency 3–10 is nowhere near 2,500 → **structurally removes the exact 429 pain**. And it's the cheapest quality option: **$0.14 in / $0.28 out per 1M** (V4-Flash).

⚠️ **Caveat — DeepSeek is China-hosted.** For UK/GDPR personal data (CVs, LinkedIn), **do NOT** route PII extraction (#1) or CV generation (#4) through it. Use it only for **public job text** (#2 enrichment, #3 judge) and non-PII cover-letter drafting.

---

## 4. Comparison (per 1M tokens, verified 2026-07-08)

| Provider | Cheap model in/out | Free tier | Batch-friendly? | JSON | Reliability |
|---|---|---|---|---|---|
| **DeepSeek** V4-Flash | **$0.14 / $0.28** | ~5M tok/30d (unconfirmed) | ✅ concurrency-based, **no TPM wall** | `json_object` best-effort | occasional load throttle; no big outages |
| **Gemini** 2.5 Flash-Lite | $0.10 / $0.40 | **1M TPM / ~15–30 RPM** | ✅ huge free TPM; RPM is the gate | **native `responseSchema`** | Google uptime |
| **Gemini** 2.5 Flash | $0.30 / $2.50 | same tier | ✅ | native, strong | same |
| **Groq** gpt-oss-20b | $0.075 / $0.30 | 30 RPM / 8K TPM (tight) | ❌ free / ✅ paid | token-level constrained (gpt-oss only) | rate-limit friction, not outages |
| **Cerebras** gpt-oss-120b | $0.35 / $0.75 | **5 RPM / 30K TPM ← the bug** | ❌ free / ✅ paid (1M TPM) | constrained (gpt-oss only) | frequent incidents logged |
| **Novita** llama-3.1-8b | **$0.02 / $0.05** | unclear | tiers unpublished | unconfirmed | thin track record |
| **Fireworks** | ~$0.20 / ~$0.90 | $1 credit, 10 RPM | ✅ paid (6,000 RPM) | **constrained decoding** (strong) | 99.8% claimed |
| **Together** | 8B $0.14 / 70B $1.04 | unclear | ⚠️ dynamic limits, risky new-acct | schema + regex | no public status page |
| **Mistral** small-4 | $0.15 / $0.60 | strict eval tier | ⚠️ hidden numbers | schema + tools | 99.58% 90d |
| **Grok** cheapest | $1.00 / $2.00 | $25 credit | ✅ 37 RPS / 10M TPM | schema + tools | 48h+ 429 lockout Apr 2026 |
| **Cloudflare** llama-8b | $0.282 / $0.827 | 10K neurons/day | ✅ 300 RPM | **best-effort only** | tied to CF-wide June 2026 outage |
| **Bedrock** Nova Micro | $0.035 / $0.14 | none | ⚠️ hidden quotas | Converse tools | recurring 429 complaints |

**JSON note:** only Cerebras / Groq / Fireworks offer true token-level constrained decoding (schema *cannot* be violated) — but on Groq/Cerebras only for gpt-oss models. Gemini/DeepSeek are "usually valid JSON," fine in practice.

---

## 5. The picks

- **Best FREE + reliable → Gemini 2.5 Flash-Lite.** The only free tier whose token budget (1M TPM) won't wall a batch; the gate is RPM (~15–30), which you control by pacing concurrency. Google uptime. *(Verify your key's live limit at `aistudio.google.com/rate-limit` — Google stopped publishing free limits and cut quotas in late 2025.)*
- **Best CHEAP + rock-solid paid → DeepSeek V4-Flash** ($0.14/$0.28). Concurrency-based limiting is the structural cure for the batch 429, at the lowest quality-grade price. *(Keep PII off it — see §3.)*
- **Best SINGLE provider (if you pick just one) → Gemini 2.5 Flash.** The only one-choice that is **GDPR-safe for the CV data AND competent at all 4 workloads AND reliable.** Not the absolute cheapest, but for "just one," safety + does-everything beats cheapest. DeepSeek is disqualified as *sole* provider because it can't legally hold the personal-data path.

---

## 6. Recommended chain (mapped to workloads)

| Workload | Primary → fallback |
|---|---|
| **Batch** — enrichment (#2) + judge (#3), *public text* | **DeepSeek V4-Flash** → Gemini 2.5 Flash-Lite → Groq gpt-oss-20b (paid dev tier) |
| **Quality + PII** — CV extraction (#1) + generation (#4) | **Gemini 2.5 Flash** (GDPR-safe) → Groq gpt-oss-120b → *(DeepSeek only for non-PII cover-letter drafting)* |

**If you insist on one model everywhere:** Gemini 2.5 Flash for all four.

---

## 7. Two concrete moves

1. **Drop Cerebras from the batch path** — its 5 RPM free cap *is* the 429. Keep it (if at all) only as a paid last-resort speed fallback.
2. **Use the *paid* Developer tier** of whatever fast provider you keep — Groq/Cerebras dev tiers unlock ~10× limits, free-with-a-card. **Free tiers ≠ production.**

---

## 8. Implementation notes

- **All the above are OpenAI-compatible** — point the existing SDK at their `base_url` + change the model name. They slot into the current `services/profile/llm_provider.py` fallback chain with minimal change.
- **GDPR split is code-level:** route the *extraction* + *CV-generation* calls to the Gemini branch; route *enrichment* + *judge* to DeepSeek. Don't let PII reach the China-hosted path.
- **Adapter trap:** proxy layers (e.g. LiteLLM) can silently downgrade `json_schema strict:true` → loose `json_object` while `finish_reason` still says "stop". If you use an adapter, **test that strict mode survives the hop.**
- **Verify prod first:** before changing anything, confirm what LLM keys are actually set on Railway and whether prod generation currently works — earlier failures were *assumed* from the batch 429s, not verified on the live tailor path.

---

## 9. One-line summary
> **Gemini 2.5 Flash if you pick one (GDPR-safe, does everything). DeepSeek for cheap batch of public job-text + Gemini for the PII/CV work if you split for cost. Either way: get off free tiers and drop Cerebras from batching — that per-minute wall is the 429.**
