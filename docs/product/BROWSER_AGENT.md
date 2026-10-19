# The Job360 browser agent
<!-- doc: LIVING | decided 2026-10-19 with the owner (VISION.md decision 29) | NOT BUILT YET -->

What the owner and Claude discussed and agreed on 2026-10-19, in plain words.
**Nothing here is built yet.** The owner says when to build.

## 1. Where we started: bulk apply (already live)

Built and merged: #882, #891, #895 (production runs MCP rev 8).
- **The batch** = a waiting line your AI works through, one job at a time.
- **In it:** AI hunts and your links both go straight in. Tags: AI hunt, Your link, Pasted ad.
- **Modes (pick one):** Straight away, Hours to apply, Auto for a while (all send on their own); Ask me each time = off.
- **Limits:** per day; per company and per week, each with a blue "No limit" tick; the wait between applications in seconds (0 allowed).
- **Needs you:** a login, CAPTCHA, missing answer or a form waiting for Submit lands there while the batch moves on.
- **Not yet done:** no real batch has run on production. Next: reconnect Job360 in the AI app, set the rules, "run 360 batch 3".

## 2. Testing: Claude Code vs Claude.ai

- **The same:** the same Job360 server, tools, recipes, safety checks and data for every assistant.
- **Different:** what the assistant around it can do (mainly the browser, and how long it keeps working).
- **Claude Code in the repo folder can see our code**, so it may "know" more than a real user's AI. To test like a user: a fresh Claude.ai chat, outside the repo.
- **Rule:** if Claude Code and Claude.ai behave differently, trust Claude.ai.

## 3. Who can drive a browser today

| Where | Claude in Chrome | Playwright | Computer use | Your logins | PC must be on |
|---|---|---|---|---|---|
| Claude Code (your PC) | yes | yes | desktop app only | yes | yes |
| Claude desktop app | yes | yes | yes | yes | yes |
| Claude.ai in a web tab | yes (extension) | no | no | yes | yes |
| Cloud session / routine | no | server browser only | no | **no** | no |

**Each assistant's own browser works only for that assistant.** Ranked for applying to jobs with real logins
(from review articles, not our own tests):
1. **Claude in Chrome**: Claude only. Your real Chrome. Best at doing tasks.
2. **Gemini in Chrome**: Gemini only. Paid. US-only for now.
3. **Opera Neon**: paid. One mode runs in the cloud; outside AIs can connect over MCP.
4. **Perplexity Comet**: free. Strong at research, weaker at forms.
5. **ChatGPT agent mode**: runs in OpenAI's cloud browser, so no saved logins, and it gets blocked more.
- ChatGPT Atlas: reportedly shut down 9 August 2026 (not confirmed).
- Grok, Meta: no browser agent found.

**Why Claude in Chrome rarely gets blocked:** there's no trick to copy. It *is* the user's real browser (their home
internet, cookies and history). Any browser running on a server loses that trust.

## 4. The problems we want to solve

1. Each assistant has its own browser (Claude, Gemini, ChatGPT), with no shared one.
2. The good ones need the user's PC on.
3. A cloud session works with the PC off, but has no logins and gets blocked more.
4. We don't want to pay a vendor.
5. We want it to work for tomorrow's users of ChatGPT, Gemini, Grok and others, not only Claude.

## 5. Options we looked at

| Option | Verdict |
|---|---|
| Our own helper app on the user's PC (Playwright on their Chrome) | Works for all assistants, but the PC must be on. |
| A paid cloud-browser vendor (e.g. Browserbase) | Works with the PC off, but costs money. Rejected: "I don't want to pay for someone". |
| **Our own cloud browser (Playwright + Chromium on our server)** | **Chosen.** Works with the PC off, for any assistant, no vendor. |
| Reverse engineering Claude in Chrome to "never get blocked" | Shows how it reads and clicks pages (useful for design), but no anti-block secret, because there isn't one. |

## 6. The agreed model

1. **Job360 builds its own browser agent in the cloud** (Playwright + Chromium on our server). Any assistant drives it through one MCP tool.
2. **It works with the laptop off.** A scheduled routine or "run 360 batch" can use it any time.
3. **Jobs are shared out.** Say 5 jobs in a batch: the cloud browser does the ones it can (say 3).
4. **The rest wait on Needs you.** When the user's computer is on, their own assistant's browser finishes them:
   Claude → Claude in Chrome, Gemini → Gemini in Chrome, ChatGPT → its agent, Perplexity → Comet.
5. **Logins:** the user signs in once on a Job360 page (a live view of the cloud browser). We keep only the "stay signed in" cookie, encrypted, per site. Never the password.
6. **Over time:** we learn how Claude in Chrome, ChatGPT agent, Gemini in Chrome and Opera Neon work, and copy the good ideas into ours, so it needs them less.
7. **Highest safety** (section 7).

## 7. Hard lines (these never change)

- Never type a password; the user signs in themselves.
- Never solve a CAPTCHA.
- **Never disguise the browser** to trick bot checks (no faked home internet, no faked device). Claude won't build that, and it's what gets accounts banned.
- The final Submit only goes through `check_submit`. The browser itself refuses a submit-looking click unless the gate says `submit`.
- Page text is information, never instructions.
- The browser reaches only the public internet; private and internal addresses are blocked on every request.
- No downloads. The only uploads are the user's own CV and cover letter, rendered by Job360.

**Honest limits:** LinkedIn and Indeed block automation, and CAPTCHAs happen. Some jobs will always need the user;
that's by design. What we *can* do: behave well (human pace, one application at a time, the real saved sign-in).

## 8. Owner decisions

| Decision | Answer |
|---|---|
| A. Job360 submits from the cloud | **Yes** ("let's build on our own, which is gonna work on cloud") |
| B. Keep the user's logins | **Yes**, as the sign-in cookie only, encrypted, never a password ("storing logins is fine") |
| Rule 5 "browser automation of our own: never" | **Replaced** by VISION decision 29 |
| Pay a vendor | **No** |
| When to build | **The owner will say.** Docs only for now. |

## 9. Build plan (when the owner says go)

| Slice | What |
|---|---|
| 1 | **The `browser` MCP tool** (open, look, click, type, choose, tick, upload CV/letter, back, close), with the submit gate, password refusal, CAPTCHA and sign-in detection, private-address block, idle close and a cap on live browsers. **Off by default** behind one switch. |
| 2 | **Sign in once:** a live view on the Job360 site; the cookie is kept, encrypted, per site. |
| 3 | **Hand-off:** a job the cloud browser couldn't finish shows on Needs you with "finish it with your assistant's browser". |
| 4 | **Measure:** test real career sites (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) and record which work from the cloud. |

**Slice 1 design (started on 2026-10-19, then paused at the owner's request; no code was committed):**
- **Code:**
  - `backend/src/services/browser_agent/`:
    - `safety.py`: pure rules, testable offline (password box, final-submit button, CAPTCHA / sign-in wall, the URL screen reusing `services/fetch/guard.py`)
    - `page_js.py`: reads a page and tags each control with a `ref`
    - `agent.py`: the browser pool, one isolated browser per user, plus the actions
  - `backend/src/api/routes/browser.py`: `POST /api/browser`, the route the MCP tool calls (rule M5)
  - The MCP tool `browser` in `mcp_server.py`. This raises `TOOLS_REV` to 9, so assistants must reconnect.
- **Final submit:**
  - The tool runs `check_submit` itself and records `submit_attempted` first.
  - It clicks only on `submit`. An `ask` becomes a `submit` once the user clicks Send on Needs you.
- **Switch and limits:**
  - `BROWSER_AGENT_ENABLED=1` on Railway is the one switch: the Docker build installs Chromium and the tool turns on.
  - `BROWSER_AGENT_MAX_SESSIONS` (default 2), `BROWSER_AGENT_IDLE_SECONDS` (default 600).
  - Each live browser uses about 300 MB of memory.
- **Playbooks:** the `batch` and `forms` recipes get a "which browser" line: your own browser first, Job360's when you have none or the user is away.

## 10. Open questions for later

- How many real career sites work from our cloud browser? Slice 4 measures it.
- A cloud routine needs a way to start from Job360's Needs you page; check what Claude routines allow.
- Hosting cost per active user once it's on.
