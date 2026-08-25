# The verification methodology (source material)
<!-- doc: LIVING -->

This skill is built on a short talk about how an agent should verify its own work.
The four slides are saved alongside this file as PNGs. Their content, in text:

## Slide 1 — "Loops make the world go around"

A task like *"Make the signup button work"* becomes a tight loop, not a one-shot:

```
Write code → Build and run app → Click the button → See it fail
   ↑                                                     ↓
Hot reload the app ← Fix code ← Read logs ←──────────────┘
```

…and only once the button actually works do you **Screenshot success → Open PR**.

The point: you don't trust that code works because it compiles. You run it, drive it,
watch it fail, read the logs, fix, and loop until you *see* it work.

## Slide 2 — "Verification comes in many flavors"

| Flavor | What it means |
|---|---|
| **UX** | Drive a real browser, screenshot, iterate |
| **Backend** | Run the service, hit the route, check the DB, read the logs |
| **E2E** | Deploy to staging, replay production traffic |

## Slide 3 — "What verification looks like in practice"

|  | FRONTEND / UX | BACKEND |
|---|---|---|
| **Run it** | One-command dev server | `make dev` / `docker compose up` |
| **Drive it** | Claude-in-Chrome / Playwright | Curl the route, hit health endpoints |
| **Prove it** | Screenshot before / after | Query the DB — did the row land? Read the logs — did the path run? Replay prod traffic |
| **Unblock it** | Auth: dummy auth. State: seed scripts for known state | Structured logs Claude can grep. Add log lines to prove the path ran |

## Slide 4 — "Example verification skill"

The principles for writing the skill itself:
- **Don't be too prescriptive** — give the loop, not a rigid script.
- **Make it self-improving** — when you hit a blocker and solve it, write the solution back into the skill.
- **Mention the tools** you want it to use.

The example skill body was roughly:
```
1. pnpm run dev (frontend + backend)
2. wait for localhost:3000 health check
3. Use Claude Chrome mcp to open localhost:3000
4. Test all relevant features of the code we've touched so far and $ARGUMENTS
5. If needed, correlate behavior with logging

If you run into blockers, find a solution and update this skill for the future.
```

Pro tip from the talk: add a **stop hook** that asks Claude to run verification if it hasn't already.
```
