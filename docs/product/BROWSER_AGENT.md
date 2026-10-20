# The Job360 browser agent
<!-- doc: LIVING | decided 2026-10-19 with the owner (VISION.md decision 29) -->

What the owner and Claude discussed and agreed on 2026-10-19, in plain words.
Which slices are built, and the switch that turns them on, is section 9 — the one place.

## 1. Where we started: bulk apply (already live)

Built and merged: #882, #891, #895.
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
- ChatGPT Atlas: retired (reported shut down on 9 August 2026).
- Grok, Meta: no browser agent found.

Sources (web research, 2026-10-19; review articles, not our own tests):
[Towards AI, the agentic browser wars](https://pub.towardsai.net/the-agentic-browser-wars-why-chatgpts-browser-died-in-under-a-year-while-others-are-just-getting-fce5c2d7e548) ·
[Tech Insider, Atlas shut down 9 Aug](https://tech-insider.org/ca/?p=1149) ·
[CoderCops, AI browser agents 2026](https://blog.codercops.com/blog/ai-browser-agents-comet-atlas-mariner-2026) ·
[EvolveAMZ, agentic browsers, July 2026](https://evolveamz.com/?p=12339) ·
[Composite, Comet review](https://composite.com/blog/perplexity-comet-reviews-pricing-alternatives) ·
[Opera, Neon announcement](https://press.opera.com/?p=3623). Re-check before relying on any of it: these products change monthly.

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
- The browser reaches only the public internet. Every connection Chromium makes (redirect hops, WebSockets, page scripts, loopback included) leaves through Job360's screening proxy, which checks the address and connects to that exact address.
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
| When to build | **Go** (owner, 2026-10-19): slices 1-3 built, off by default. |

## 9. Build plan (when the owner says go)

| Slice | What |
|---|---|
| 1 ✅ | **The `browser` MCP tool** (open, look, click, type, choose, tick, upload CV/letter, key, scroll, back, close), with the submit gate, password refusal, CAPTCHA and sign-in detection, private-address block, idle close and a cap on live browsers. **Off by default** behind one switch. |
| 2 ✅ | **Sign in once:** a live view on the Job360 site; the cookie is kept, encrypted, per site. |
| 3 ✅ | **Hand-off:** a job the cloud browser couldn't finish shows on Needs you with "finish it with your assistant's browser". |
| 4 🟡 | **Measure:** test real career sites (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) and record which work from the cloud. |

**Slice 1 (built 2026-10-19, off by default):**
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
- **Tests:** `backend/tests/test_browser_agent.py`. The safety rules and the off switch always run; the real-Chromium runs (fill a form, password refused, submit only through the gate, CAPTCHA page, private address, one browser per user) run where Playwright + Chromium are installed and skip otherwise (CI).
- **Egress proxy** (`services/browser_agent/egress.py`, added after review): Chromium's only way out. Each host is resolved once, every address screened by the URL-fetch SSRF guard, and the connection pinned to the address that passed, so a redirect to a private address, a WebSocket or a DNS change after the check gets a 403. Tested with a real redirect to loopback. WebRTC's UDP does not use the proxy, so Chromium launches with `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`: an egress guard that keeps a page's WebRTC traffic behind the same screen, not a disguise.

**Slice 2 (built 2026-10-19, off by default): sign in once.**
- **Where:** Settings → **Job sites** on the Job360 website. Type the job site's sign-in page and press Start.
- **How:** a live picture of your own cloud browser. Click on the picture to click there; type in the box below
  (hidden by default, because it may be a password), Enter / Tab / Backspace / Escape and scroll buttons.
  When you are signed in, press **"I'm signed in: keep me signed in"**.
- **What is kept:** only the cookies and site storage that belong to that site, encrypted (Fernet; the key is
  `BROWSER_SIGN_IN_KEY`, or derived from `SESSION_SECRET` so production needs nothing new). Never what you typed:
  your password goes straight to the site and is not logged, stored or sent back. Table `browser_sign_ins`
  (migration 0065), one row per (user, site); **Forget** deletes it; deleting the account deletes them all.
- **Who can use it:** only you, in a signed-in browser session. An assistant's token is refused (403), so no AI can
  drive the live view or type into it. The assistant's own `browser` tool simply starts already signed in.
- **Changing `SESSION_SECRET`** signs everyone out of every saved site (they sign in again). Nothing breaks.
- **Checked end to end** in a real browser against a local test site: the page signs in, keeps the site, lists it,
  the password is in neither the logs nor the database, and an assistant's new browser opens already signed in.

**Slice 3 (built 2026-10-19, off by default): hand-off to Needs you.**
- When the cloud browser stops on a CAPTCHA or a sign-in wall and the call names an `application_id`, Job360 records
  `blocked` itself (reason `captcha` or `login_needed`, step "Job360 browser", the site's host). That opens the usual
  Needs you ask, and `check_submit` holds the job until it is resolved.
- Recorded once per open block of the same reason, so repeated looks don't pile up events.
- On the application's page, a sign-in stop shows a **"Sign in once to <site> on Job sites"** button, so the next run starts signed in.
- The note tells the user how to finish: with their own assistant's browser when their computer is on (Claude in
  Chrome, Gemini in Chrome, ChatGPT agent), or by signing in once on Settings → Job sites.

**`key` action (2026-10-19):** ArrowDown, ArrowUp, Tab, Escape and Enter, for the suggestion lists real forms use
(Greenhouse location and school, Workday dropdowns): type, ArrowDown, Enter. Enter is refused outside a suggestion
list (`enter_may_submit`), because in an ordinary field it can submit the form and skip `check_submit`. The list
must be OPEN (`aria-expanded` true, or an option highlighted): a closed suggestion box in a form submits on Enter too.

**CAPTCHA vs. invisible reCAPTCHA:** every Greenhouse posting loads an invisible reCAPTCHA that asks nothing, so it
is not a stop. A reCAPTCHA image challenge or a tick-box widget is (freehire `atsapply/browser.go:167-234`).

**Real-site behaviour (2026-10-19):** after `open`, a click, a key or a scroll, the browser waits up to 3 seconds for
the page's own requests to finish, because single-page sites (React forms) draw their fields after the HTML arrives.
`scroll` (text `down` or `up`) loads questions that only appear further down. Both tested on a local single-page
form; the test fails when the wait is removed.

**Step 2, better page reading (2026-10-19, from the browser-agent research):**
- **Nearest the screen first:** when a page has more controls than fit (150), the ones nearest what is on screen are
  listed and `more_elements` says how many are above and below. Before, a long form's Submit could never be reached.
- **Hidden checkboxes and radios:** custom-styled ones (the real input is invisible, the label is the card the user
  clicks) are read and ticked through their label.
- **Shadow DOM:** inputs inside open shadow roots are read (SmartRecruiters builds its whole form this way).
- **Frames:** invisible frames are skipped, at most 20 are read, and each read has 3 seconds.
- **Untrusted text:** the page text sits inside a marker with a random code per read. A site that plants a fake end
  marker gets it removed, so its text cannot pose as instructions. Everything else the page sets is bounded too: the
  title (200 characters), the address (2,000), each control's tag, type, role and autocomplete (40), and the whole
  element list (60,000 characters as JSON; the rest count in `more_elements`). A `data-j360-ref` the page wrote itself
  (not Job360's shape, or shared by two controls) is replaced, and a ref found on two elements is refused, never guessed.
- **A page that did not load:** `look` on Chromium's own error page (`chrome-error://`) answers `load_failed`, never an
  empty `ok`.

**Step 3, typing and dropdowns that hold (2026-10-19):**
- **`type`** reads the value back. A suggestion box (combobox, autocomplete) is typed key by key so the site's
  suggestions appear (Skyvern). A plain field that a React form did not take gets the value set the way React
  expects, once (browser-use, freehire). Never a retry loop.
- **`choose`** works on custom dropdowns, not only `<select>`: it opens the list, finds the option wherever the
  site draws it (react-select, Workday's portal lists), clicks it and checks the field now shows it. It picks an
  exact match, or the only option that starts with or contains the text. Two candidates means no guess: it lists
  the options it saw. It looks in the field's own frame first, and in the list the field names as its own
  (`aria-controls`); two open lists with no owner named are refused (`two_lists_open`), never guessed between.
  A contenteditable box keeps what was typed: the React value setter is used only on inputs and text boxes.
- **`upload`** also works from an "Attach" button by catching the file picker. `upload_confirmed` says whether the
  file name shows on the page, because Greenhouse can take the file and still show an error (ApplYourself).
- **Never a control key through `type`:** typed as keys, a line break is Enter (it can submit the form and skip
  `check_submit`) and a tab moves to another field. `type` refuses them (`control_characters`); line breaks go
  only into a multi-line box, inserted as text. Option labels quoted in an error sit inside the untrusted marker.

**Step 4, site playbooks for Greenhouse, Lever and Ashby (2026-10-19, `sites.py`):**
- **`site`:** every read names the job site from the page's own address, or its frames (Greenhouse and iCIMS
  forms often sit in a frame on a company page), matching the end of the host name and never a piece of the
  URL, so `evilgreenhouse.io.example.com` is not Greenhouse. The sites and their tips: `browser_agent/sites.SITES`.
- **`site_warnings` (Lever):** a location typed but not picked from Lever's list (the site refuses those), and a
  cookie banner over the form. `choose` now also picks from Lever's location list.
- **Ashby Yes/No buttons:** the chosen one reads `pressed: true`.
- **Did it go?** `confirmation_seen` only on a positive confirmation ("Thank you for applying", Ashby's "Your
  application was successfully submitted.", Lever's `/thanks` page). `site_refused` when the site says it did not take
  it ("Your form needs corrections", "Missing entry for required field: X"); a refusal wins. After a submit the `next`
  line says which one: record the application, fix the fields, or neither (never click submit again). All from
  ApplYourself `src/apply/fill.py`; the recipe `get_recipe("ats")` has the same rows as BORROWED until a real run.

**Step 5, Workday, SmartRecruiters, iCIMS, Teamtailor and agency sites (2026-10-19):**
- **Workday pages:** `form_step` (for example `{current: 2, total: 7}`) from Workday's progress bar. Workday ignores
  values set by a script, so short answers in a Workday field are typed as real keys and the field is then left,
  which is when Workday saves and checks it. Long answers (over 200 characters) are filled.
- **`invalid: true`** on any field the site marks wrong or missing. Workday marks them only after Save and
  Continue; the tips say: fix them, Save again, and after 2 stuck Saves stop and record blocked.
- **iCIMS** forms inside a frame on the company page are named iCIMS; `login.icims.com` is not. **Teamtailor** is
  named, with tips on its hidden "Required" labels and on asking before the optional consent box.
  **SmartRecruiters** needed nothing new: its shadow-DOM fields were already read in step 2.
- **Agencies and unknown sites:** no reference covers agency forms, so only general rules apply.
  - `application_form` says whether the page looks like the form itself (a file box, or 3 or more labelled
    fields) or still the ad.
  - A field labelled "leave this blank" (a trap for bots) reads `leave_blank: true`, and `type`, `choose`, `tick`
    and `upload` refuse it. The label must say "this" or "the" field: "Middle name (leave blank)" is just optional.

**Step 6, proof and "did that do anything?" (2026-10-19):**
- **`page_changed`** after every click. The browser watches the page from just before the click until it settles:
  a new address, a new tab, a frame that navigated, any change to the page (inside open shadow roots too, and in
  the clicked control's own frame even past the 20-frame cap), or a change in the control itself (a tick box) counts. When unsure, it counts as changed. Nothing changed: `next` says not to click it again
  (nanobrowser's changed / none receipts).
- **Never blind:** a control whose clicks changed nothing twice in a row is refused (`no_effect`) until another
  action (an answer typed, a box ticked, a scroll or a new page) gives it its tries back.
- **Never twice:** after a final submit on a page, a second submit for the same application on the same page is
  refused (`already_submitted`; another job on the same form page goes to its own `check_submit`), unless the site said it did not take the first (`site_refused`). Even then `check_submit`
  holds a second submit until the user marks the first one "not sent" on Job360, so `next` says to ask them.
- **`repeat_warning`:** the same action with the same ref and text 3 times in a row (browser-use's loop nudge).
- **Proof:** when a gated submit lands on the site's confirmation (`confirmation_seen`), a screenshot of that page
  (the tab that shows it, a new one included) is stored as the application's proof (`proof_screenshot`, `via: browser`, the S7 store, at most 3 per
  application). `proof_saved` says whether it was kept; a failed save never undoes the application.

**Slice 4 tool ready, not run yet:** `python -m src.cli browser-probe <url> <url> ...` (add `--json` for rows). It opens
each page READ-ONLY in a fresh throwaway browser (no sign-ins, no typing, no clicking) and reports per site: `ok` (form
fields, file inputs, submit buttons found), `no_form` (an ad page), `captcha`, `login_required`, `blocked` or `error`.
Each row also names the playbook the assistant would get (`site`: greenhouse, lever, ashby, workday...), Workday's
page (`form_step`, like 2/7) and how many controls sit past the first read (`more_below`). "Is it the form?" is
look's own `application_form` (a file box or 3+ labelled fields), so a lone search box is `no_form`.
It needs the open internet, so run it on Railway once the switch is on, with a list of real apply links.

**Send starts my assistant (built 2026-10-19).** Before this, Send / Send all on Needs you only saved the yes,
and the work waited for "run 360". Now, if you saved a start link on **Settings → Connect your assistant**, the
website calls `POST /api/assistant-trigger/fire` once after Send (or after all of Send all), and Job360 starts your
assistant with the list of every job whose newest decision is your yes. **One start per click, never one per job**.
A 60-second cool-down, claimed in the database before the call, means two presses at once start one session. A yes
given inside that minute is not in the session that just started: the page says so, and **Start now** on the card
sends every job still waiting on your yes.
- **Claude routine (recommended, PC off):** make a routine on claude.ai/code/routines with the Job360 connector and
  the instructions the card gives you; add an **API trigger**, copy its URL and token into the card. Job360 calls
  `POST https://api.anthropic.com/v1/claude_code/routines/<trig_id>/fire` (`anthropic-version: 2023-06-01`,
  body `{"text": ...}`; Anthropic's docs mark this API as experimental, 30 starts per routine per hour). The routine
  applies with Job360's `browser` tool and your saved sign-ins, running `check_submit` (your yes shows as
  `user_approved`) before every final click.
- **Any other assistant:** any https webhook gets the same `{"text": ...}` and bearer token.
- **The ad's words are not instructions:** each job title and company in the text is one line of at most 120
  characters, inside a job-list marker with a random code per start, under a line saying they are ad text. The card's
  routine instructions say only Job360's fixed text is the instruction, and only the `application_id` is used.
- **No cloud:** the card also gives a `/loop 5m ...` command to leave running in Claude Code on your own computer;
  it applies with your own Chrome and sign-ins (computer on).
- **Safety:** set, changed and fired only from the website (an assistant's token gets 403, and no MCP tool reaches
  it); the token is encrypted (table `assistant_triggers`, migration 0066) and never shown again; https only,
  screened by the SSRF guard with the address pinned and no redirects followed; a failed start is shown, and your
  yes stays saved.

**To turn it on:** Railway, backend service, set `BROWSER_AGENT_ENABLED=1`, then redeploy. Then reconnect Job360 in the assistant (rev 9).

## 10. Open questions for later

- How many real career sites work from our cloud browser? Slice 4 measures it.
- A cloud routine needs a way to start from Job360's Needs you page; check what Claude routines allow.
- Hosting cost per active user once it's on.
- Slice 4 needs the open internet: run it from production once the switch is on.
- CI does not install Chromium, so the real-browser tests skip there; adding it is a `.github/` (owner-lane) change.
