# Maintenance Journal (append-only)

## 2026-06-10 ~21:50 — bootstrap (manual, by the orchestrator)

- Created `/maintain` skill + this backlog/journal pair; loop armed every 2h.
- Context for future iterations: branch `fix/per-user-search-and-scoring-gate` carries the funnel→judge matcher (commits a925f42..d801f78 + 76f6ca7 compat fix). Live-verified: 18/18 jobs judged in 89.8s for demo user e34aeb69e9bf4680bd143e1f3756140a; verdicts persisted to user_feed; API returns llm_* fields ranked by COALESCE; canonical suite 1281 passed/3 skipped; frontend 64/64.
- Source-health evidence for P1 items came from run_uuid 0656b8c0-d333-4e5d-9133-ec8ed17928d9 (2026-06-10 21:37).
- Gemini free tier is quota-dead (429); provider chain degrades to Groq/Cerebras — expected, not a bug.
- Live verify finding (added as backlog P1 #0): dashboard client sort uses match_score and overrides the server's judge ranking; badge itself renders correctly (red "AI: Poor fit · 20" on intern cards). Screenshot: test-artifacts/matcher-badge-demo-user.png.
- Measured cost of one judged search: 18 LLM calls, 89.8s wall (concurrency 3, Groq/Cerebras), zero failures.
