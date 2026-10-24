# The Job360 browser agent
<!-- doc: LIVING | decided 2026-10-19 with the owner (VISION.md decision 29) -->

What the owner and Claude discussed and agreed on 2026-10-19, in plain words.
Which slices are built, and the switch that turns them on, is section 9 — the one place.

## 1. Where we started: bulk apply (already live)

Built and merged: #882, #891, #895, #916 (the one "Batch auto apply" switch).
- **The batch** = a waiting line your AI works through, one job at a time.
- **In it:** AI hunts and your links both go straight in. Tags: AI hunt, Your link, Pasted ad.
- **Modes (pick one):** Straight away, Auto for a while (each lets your assistant send on its own, but only within your score line and the safety checks); Ask me each time = off.
- **Limits:** per day (blank = no limit); per company and per week, each with a blue "No limit" tick; the wait between applications in minutes and seconds (0 = no wait).
- **Needs you:** a login, CAPTCHA, missing answer or a form waiting for Submit lands there while the batch moves on.
- **Not yet confirmed:** a real batch on production (not checked here). Next: reconnect Job360 in the AI app, set the rules, "run 360 batch 3".

## 2. Testing: Claude Code vs Claude.ai

- **The same:** the same Job360 server, tools, recipes, safety checks and data for every assistant.
- **Different:** what the assistant around it can do (mainly the browser, and how long it keeps working).
- **Claude Code in the repo folder can see our code**, so it may "know" more than a real user's AI. To test like a user: a fresh Claude.ai chat, outside the repo.
- **Rule:** if Claude Code and Claude.ai behave differently, trust Claude.ai.

## 3. Who can drive a browser today

| Where | Claude in Chrome | Playwright | Computer use | Your logins | PC must be on |
|---|---|---|---|---|---|
| Claude Code (your PC) | yes | yes (Playwright MCP) | CLI: macOS only; Windows: desktop app | yes | yes |
| Claude desktop app | yes (connector) | yes (Playwright MCP) | yes (macOS, Windows; Pro or Max) | yes, via Claude in Chrome | yes |
| Claude.ai in a web tab | yes (extension) | no | no | yes | yes |
| Cloud session / routine | no | server browser only (Job360's `browser` tool) | no | only sites saved on Settings → Job sites | no |

**Each assistant's own browser works only for that assistant** (Opera Neon's MCP connector is the exception). Our ranking for
applying to jobs with real logins, from review articles (not tested by us):
1. **Claude in Chrome**: Claude only, paid plans. Your real Chrome and its sign-ins. One review rates it best at multi-step tasks.
2. **Gemini in Chrome**: Gemini only. The agent part (Auto Browse) needs paid Google AI Pro or Ultra and is US-only for now.
3. **Opera Neon**: paid. Only its "make" agents run in a cloud VM; since Mar 2026 outside AIs can drive your live Neon over MCP.
4. **Perplexity Comet**: free. Strong at research, weaker at long multi-step tasks like full forms.
5. **ChatGPT's cloud browser** (ChatGPT Work; OpenAI's help page says agent mode is no longer available, 20 Oct 2026): runs on OpenAI's cloud computer, so no sign-ins from your own browser. Some sites block it.
- ChatGPT Atlas: retired (reported shut down on 9 August 2026).
- Grok, Meta: no browser of their own (Meta's Muse and xAI's Grok Bot reportedly browse in the cloud; unchecked).

Sources (web research, 2026-10-19; the Claude.ai web-tab row above is not yet confirmed):
[Towards AI, the agentic browser wars](https://pub.towardsai.net/the-agentic-browser-wars-why-chatgpts-browser-died-in-under-a-year-while-others-are-just-getting-fce5c2d7e548) ·
[Tech Insider, Atlas shut down 9 Aug](https://tech-insider.org/ca/?p=1149) ·
[CoderCops, AI browser agents (undated; still describes Atlas as live)](https://blog.codercops.com/blog/ai-browser-agents-comet-atlas-mariner-2026) ·
[EvolveAMZ, agentic browsers, July 2026](https://evolveamz.com/?p=12339) ·
[Composite (a rival's) Comet review, Feb 2026](https://composite.com/blog/perplexity-comet-reviews-pricing-alternatives) ·
[Opera, Neon announcement, May 2025](https://press.opera.com/?p=3623) · [Opera, Neon MCP connector, Mar 2026](https://press.opera.com/?p=3885). Re-check before relying on any of it: these products change monthly.

**Why Claude in Chrome is blocked less (reviewers' view, not measured by us):** there's no trick to copy. It *is* the user's real
browser (their home internet, cookies and history). A browser on a server, ours included, does not have that trust.

## 4. The problems we want to solve

1. Each assistant has its own browser (Claude, Gemini, ChatGPT), with no shared one.
2. The good ones need the user's PC on.
3. A cloud session works with the PC off, but has none of the user's logins (until they sign in once on Job sites) and may be blocked more (slice 4 measures it).
4. We don't want to pay a vendor.
5. We want it to work for tomorrow's users of ChatGPT, Gemini, Grok and others, not only Claude.

## 5. Options we looked at

| Option | Verdict |
|---|---|
| Our own helper app on the user's PC (Playwright on their Chrome) | Works for all assistants, but the PC must be on. |
| A paid cloud-browser vendor (e.g. Browserbase) | Works with the PC off, but costs money. Rejected: "I don't want to pay for someone" (owner, 2026-10-19 chat; not in VISION 29). |
| **Our own cloud browser (Playwright + Chromium on our server)** | **Chosen.** Works with the PC off, for any assistant, no vendor. |
| Studying Claude in Chrome to "never get blocked" (web research; a reverse-engineering run is not yet confirmed) | Shows how it reads and clicks pages (useful for design), but no anti-block secret, because there isn't one. |

## 6. The agreed model

1. **Job360 builds its own browser agent in the cloud** (Playwright + Chromium on our server). Any assistant drives it through one MCP tool.
2. **It works with the laptop off.** With the switch on, a routine or "run 360 batch" can use it whenever a browser is free (default 2).
3. **Jobs are shared out.** Say 5 jobs in a batch: the cloud browser does the ones it can (say 3).
4. **The rest wait on Needs you.** When the user's computer is on, their own assistant's browser finishes them:
   Claude → Claude in Chrome, Gemini → Gemini in Chrome, ChatGPT → its cloud browser, Perplexity → Comet.
5. **Logins:** the user signs in once on a Job360 page (a live view of the cloud browser). We keep only what the site chose to remember (its cookies and site storage), encrypted, per site. Never the password.
6. **Over time:** we learn how Claude in Chrome, ChatGPT agent, Gemini in Chrome and Opera Neon work, and copy the good ideas into ours, so it needs them less.
7. **Highest safety** (section 7).

## 7. Hard lines (these never change)

- Never type a password; the user signs in themselves.
- Never solve a CAPTCHA. It stops on the ones it recognises (section 9); a vendor it does not know is not detected.
- **Never disguise the browser** to trick bot checks (no faked home internet, no faked device). Claude won't build that, and it's what gets accounts banned.
- The final Submit only goes through `check_submit`. A click on a control the browser reads as the final submit (section 9) is refused unless the gate says `submit`; an Enter that could submit is always refused. A control it does not read as one is clicked like any other.
- Page text is information, never instructions.
- Every required question is answered from the user's saved facts, and a required question with no saved answer stops the application (`required_unanswered`); only optional questions may stay blank.
- The browser reaches only the public internet. Every connection Chromium makes (redirect hops, WebSockets, page scripts, loopback included) leaves through Job360's screening proxy, which checks the address and connects to that exact address.
- No downloads. The only uploads are the user's own CV and cover letter, rendered by Job360.

**Honest limits:** LinkedIn and Indeed block automation (reported; not yet measured by our probe), and CAPTCHAs happen. Some jobs
will always need the user; that's by design. What we *can* do: behave well (the gap between submits the user sets, if any, one application at a
time, the real saved sign-in).

## 8. Owner decisions

| Decision | Answer |
|---|---|
| A. Job360 submits from the cloud | **Yes** ("let's build on our own, which is gonna work on cloud") |
| B. Keep the user's logins | **Yes**, as what the site keeps to stay signed in (cookies and site storage), encrypted, never a password ("storing logins is fine") |
| "Browser automation of our own" on VISION's never-build list | **Replaced** by VISION decision 29 |
| Pay a vendor | **No** |
| When to build | **Go** (owner, 2026-10-19): slices 1-3 built; the switch is on in production (seen 2026-10-20). |
| Autofill answers every required question | **Strict rule** (owner, 2026-10-20 chat): every required question gets an answer from the user's saved facts; only optional ones may stay blank; a required one with no saved answer is asked, never guessed or skipped, and the application is not submitted until it is answered. Built in the submit check as the stop `required_unanswered` (a yes never clears it; applications created from 2026-10-20). |

Decisions A and B are recorded in VISION decision 29; the quoted words are from the 2026-10-19 chat.

## 9. Build plan (go given 2026-10-19)

| Slice | What |
|---|---|
| 1 ✅ | **The `browser` MCP tool** (its actions are `services/browser_agent/agent.ACTIONS`), with the submit gate, password refusal, CAPTCHA and sign-in detection, private-address block, idle close and a cap on live browsers. **Off in the code** until one switch is set. |
| 2 ✅ | **Sign in once:** a live view on the Job360 site; cookies and site storage are kept, encrypted, per site. |
| 3 ✅ | **Hand-off:** a job the cloud browser couldn't finish shows on Needs you with "finish it with your assistant's browser". |
| 4 🟡 | **Measure:** test real career sites (Greenhouse, Lever, Ashby, Workday, SmartRecruiters) and record which work from the cloud. |

**Slice 1 (built 2026-10-19, off by default):**
- **Code:**
  - `backend/src/services/browser_agent/`:
    - `safety.py`: pure rules, testable offline (password box: by its type, autocomplete or a label in many languages, "Passwort" included (seen live after #969: refused); final-submit button; CAPTCHA / sign-in wall; the URL screen reusing `services/fetch/guard.py`)
    - `page_js.py`: reads a page and tags each control with a `ref`
    - `agent.py`: the browser pool (one Chromium; an isolated context per job, `agent.browser_key`, plus one scratch browser per user for calls with no `application_id` and one for their live sign-in view), plus the actions
  - `backend/src/api/routes/browser.py`: `POST /api/browser`, the route the MCP tool calls (rule M5)
  - The MCP tool `browser` in `mcp_server.py`, always listed (it answers `off` until the switch is on).
- **Final submit:**
  - The tool runs `check_submit` itself and records `submit_attempted` first.
  - It clicks only on `submit`. Which asks the user's Send on Needs you turns into a `submit`, and which ones no yes ever clears, is the ordered rule list in `services/profile/assistant_settings.may_submit`'s docstring.
  - What counts as the final submit (`safety.control_kind`): any button that can submit its form itself (type submit or image, or a `<button>` with no type, that has a form owner) unless its name is a step ("Next", "Save and continue") or it is a pressed-or-not toggle (`aria-pressed`, Ashby's Yes / No), so a name we do not know ("Done") goes through the gate; "send / apply ... application" beats a step word ("Save and send application"); a link, tab, switch or option named with the whole word "Submit" ("Submitted applications" is a tab), or "Send application" once the page holds an answer; and "Apply", "Send", "Finish", "Complete" or "Confirm" (or the common EU verbs: absenden, envoyer, postuler, enviar, verzenden...) on a button in a form, or on any button once the page holds an answer, inside the `<form>` around a shadow root's host too. An answer is a typed value (not in a search or password box), a chosen file, a picked option, a ticked box or an `aria-checked` / `aria-pressed` choice, inside open shadow roots too, in the button's own frame; a box, option or switch the page already held when the first look read it (a cookie switch) is not one.
- **Switch and limits:**
  - `BROWSER_AGENT_ENABLED=1` on Railway is the one switch: the Docker build installs Chromium and the browser answers. It is on in production (seen live 2026-10-20); the code default stays off.
  - `BROWSER_AGENT_MAX_SESSIONS` (default 2, all users together), `BROWSER_AGENT_IDLE_SECONDS` (default 600).
  - `BROWSER_AGENT_MAX_PER_USER` (default 3, 2026-10-24): a new browser past a cap closes that user's least recently used one that is not held for them; when all are held it answers `busy` (`browsers_held`). Another user's browser is never closed.
  - `BROWSER_AGENT_MAX_LIVE` (default 2): the live sign-in views' own cap, so they never take an assistant's browser.
  - A started Chromium takes about 250 MB, and each browser adds about 100-250 MB (measured locally on test pages, 2026-10-20; real career sites not yet measured).
  - An idle browser is closed by a sweep the app runs every 60 s while the switch is on, not only on the next request.
  - One browser per job (2026-10-24): a call naming another `application_id` gets its own browser, so the first job's filled form stays; a call naming a job with no browser yet takes over the user's scratch browser. One assistant session at a time per browser: a call with another `session` label on that job's browser gets `busy` (`browser_in_use`) until the first one closes it or it idles out. A call with no label is not refused.
  - A hung page never holds the browser: the frame-read, per-action and `fill` limits are `services/browser_agent/agent.FRAME_READ_TIMEOUT_S` / `ACT_TIMEOUT_S` / `FILL_TIMEOUT_S` (a breach answers `error`, `timeout`).
- **Playbooks:** the `batch` recipe gets a "which browser" line (your own browser first, Job360's when you have none or the user is away); the `forms` recipe points to the tool.
- **Tests:** `backend/tests/test_browser_agent.py` and the `test_browser_*` files beside it. The safety rules and the off switch always run; the real-Chromium runs run where Playwright + Chromium are installed and skip otherwise. CI's offline-suite installs both (since 2026-10-20), so they run on every PR, and the skip ratchet (`scripts/test_integrity.py`) is red if they ever skip there.
- **Egress proxy** (`services/browser_agent/egress.py`, added after review): Chromium's only way out. Each host is resolved once, every address screened by the URL-fetch SSRF guard, and the connection pinned to the address that passed, so a redirect to a private address, a WebSocket or a DNS change after the check gets a 403. Tested with a real redirect to loopback. WebRTC's UDP does not use the proxy, so Chromium launches with `--force-webrtc-ip-handling-policy=disable_non_proxied_udp`: an egress guard that keeps a page's WebRTC traffic behind the same screen, not a disguise.

**Slice 2 (built 2026-10-19, off by default): sign in once.**
- **Where:** Settings → **Job sites** on the Job360 website. Type the job site's sign-in page and press Start.
- **How:** a live picture of your own cloud browser. Click on the picture to click there; type in the box below
  (hidden by default, because it may be a password), Enter / Tab / Backspace / Escape and scroll buttons.
  When you are signed in, press **"I'm signed in: keep me signed in"**.
- **What is kept:** only the cookies and site storage of the site you started on, and of the site the sign-in ended on if it moved (one row each), encrypted (Fernet; the key is
  `BROWSER_SIGN_IN_KEY`, or derived from `SESSION_SECRET` so production needs nothing new). Never what you typed:
  Job360 never records it: it goes straight to the site and is not logged, stored or sent back. Table `browser_sign_ins`
  (migration 0065), one row per (user, site); **Forget** deletes it; deleting the account deletes them all.
- **Who can use it:** only you, in a signed-in browser session. An assistant's token is refused (403), so no assistant
  connected to Job360 can drive the live view or type into it (an assistant in your own browser acts as you). The live view is its own browser, not the one the assistant's `browser` tool
  drives: the assistant cannot see your sign-in, and Start or Cancel never touches its half-filled form. Live views
  count toward their own `BROWSER_AGENT_MAX_LIVE`, never toward `BROWSER_AGENT_MAX_SESSIONS`. The assistant's own
  `browser` tool starts already signed in, and Keep hands the new sign-in's cookies (not site storage) to its browser if it is already
  open, so "sign in, then try again" works at once.
- **How long it stays open:** the picture refreshes every 1.5 s while the tab is shown, and stops after Keep. The
  refresh does not keep the browser open: it closes after `BROWSER_AGENT_IDLE_SECONDS` without a step of yours (click, typing, key,
  scroll), and 15 minutes after the last Start at the latest.
- **Changing `SESSION_SECRET`** (when `BROWSER_SIGN_IN_KEY` is not set) signs everyone out of every saved site. The sites stay listed on Job sites until signed in again or forgotten. Nothing crashes.
- **Checked once by hand** (#902, a local test site): the page signs in, keeps the site, lists it, the password is in neither the logs nor the database, the assistant's new browser opens signed in, and its token is refused by the live view. A real-Chromium test re-checks sign-in, Keep and the signed-in new browser.

**Slice 3 (built 2026-10-19, off by default): hand-off to Needs you.**
- When the cloud browser stops on a CAPTCHA or a sign-in wall and the call names an `application_id`, Job360 records
  `blocked` itself (reason `captcha` or `login_needed`, step "Job360 browser", the site's host). That opens the usual
  Needs you ask, and `check_submit` holds the job until it is resolved.
- Recorded once per open block of the same reason, so repeated looks don't pile up events.
- On the application's page, a sign-in stop shows a **"Sign in once to <site> on Job sites"** button, so the next run starts signed in.
- The note tells the user how to finish: with their own assistant's browser when their computer is on (Claude in
  Chrome, Gemini in Chrome, ChatGPT agent), or by signing in once on Settings → Job sites. The note names only those three, and OpenAI has retired agent mode.
- **Finish it here (2026-10-23):** on a CAPTCHA stop, the filled form is kept open for 30 minutes past the idle sweep
  (`agent.hold_for_user`), and the application's page shows **"Finish it here"**. The user solves the check themselves
  on a live picture of the assistant's own browser (`POST /api/browser/live` with `mode: finish`: shot, click, type,
  key, scroll, done; no start, no keep). Website session only, like the sign-in view: a token gets 403. Done puts the
  browser back on its idle clock; then "I submitted it" records the receipt. Job360 still never solves a CAPTCHA.
  Since 2026-10-24 `form_swept` holds that job's browser the same 30 minutes (it waits on the user's yes); the finish
  view names the `application_id` whose browser it drives, Ready to send offers it too, and Needs you shows `held_seconds`.

**`key` action (2026-10-19; Enter rule 2026-10-20):** ArrowDown, ArrowUp, Tab, Escape and Enter,
for the suggestion lists real forms use: type, ArrowDown, Enter. Enter is refused outside a suggestion
list (`enter_may_submit`), because in an ordinary field it can submit the form and skip `check_submit`. An option
must be highlighted (focus on an option, or `aria-activedescendant` naming one): a closed suggestion box, or an open
list that shows "No options", submits on Enter too. And if a site lets an allowed Enter through to its form anyway,
the submit (a `form.submit()` call too) is stopped before the page sees it and the key is refused (`enter_may_submit`). A form sent by the site's own fetch is not caught.

**CAPTCHA vs. invisible reCAPTCHA:** Greenhouse postings load an invisible reCAPTCHA that asks nothing (freehire
`atsapply/browser.go:205-207`), so it is not a stop. Job360 tells it apart by `size=invisible` in its frame address (our own rule,
not yet confirmed on a real Greenhouse run). A reCAPTCHA image challenge or a tick-box widget is a stop.
Only a challenge frame the user can see counts: Ashby and Lever were seen loading theirs hidden off the page
until it asks something (2026-10-20); that waiting frame is no stop.
What stops it: frames of the known vendors (hCaptcha, Turnstile, Arkose, GeeTest, AWS WAF and more) and text like "verify you are
human" or "type the characters you see". Seen live after #969: GeeTest and "type the characters you see" pages stop as CAPTCHA.
Once a page shows a CAPTCHA, every action but `look`, `back`, `open` and `close` is refused (`needs_you`, reason
`captcha`), and nothing inside a CAPTCHA's own frame is ever clicked, typed or keyed (keys go to the focused frame, and that is checked): the cloud browser never ticks
"I'm not a robot" itself.
Cloudflare Turnstile is told by its host (`challenges.cloudflare.com`), not the word: `jobs.ashbyhq.com/turnstile` is an employer.

**Real-site behaviour (2026-10-19):** after `open`, a click, a key or a scroll, the browser waits up to 3 seconds for
the page's own requests to finish, because single-page sites (React forms) draw their fields after the HTML arrives.
`scroll` (text `down` or `up`) loads questions that only appear further down. Both tested on a local single-page form.

**Step 2, better page reading (2026-10-19, from the browser-agent research):**
- **Nearest the screen first:** when a page has more controls than fit (150), the ones nearest what is on screen are
  listed and `more_elements` says how many are above and below.
- **Hidden checkboxes and radios:** custom-styled ones (the real input is invisible, the label is the card the user
  clicks) are read and ticked through their label.
- **Shadow DOM:** inputs inside open shadow roots (up to 7 levels) are read (SmartRecruiters builds its form this way).
- **Frames:** invisible frames are skipped, at most 20 are read, and each read has 3 seconds. Inside a frame,
  "nearest the screen" means nearest the part the user sees after the OUTER page scrolls, so a tall auto-sized
  frame (it never scrolls itself) shows its later questions as you scroll; its submit button is always listed.
- **One ref, one control:** a form section the site copies copies the ref too; each read gives the copy its own
  ref, and an action on a ref that matches two controls is refused (`ref_not_found`: look again), never sent to
  the first one.
- **Untrusted text:** the page text sits inside a marker with a random code per read. A site that plants a fake marker gets
  it removed, so its text cannot break out of the block. Control names and the title come outside it, under a note. Everything else the page sets is bounded too: the
  title (200 characters), the address (2,000), each control's tag, type, role and autocomplete (40), and the element list (60,000 characters as JSON; the rest count in `more_elements`). A `data-j360-ref` the page wrote itself
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
  site newly drew the file name on the page (not just the file box holding it, and not a name already on the page),
  because Greenhouse can take the file and still show an error (ApplYourself). A new "failed / error / try again /
  too large / not supported" line next to the field makes it false and comes back as `upload_error` (page text).
  The CV goes to the page as an in-memory file, not a temp path, and `upload_confirmed` is read after waiting up to about 10 s for the site to show the file name.
- **Never a control key through `type`:** typed as keys, a line break is Enter (it can submit the form and skip
  `check_submit`) and a tab moves to another field. `type` refuses them (`control_characters`); a line break (`\n` only; `\r` is refused) goes
  only into a multi-line `<textarea>`, inserted as text. Option labels quoted in an error sit inside the untrusted marker.

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
- **Workday's sign-in step** (title "Sign In", or "Create Account/Sign In" as the current step) is `needs_you`
  (`login_required`) even before a password box shows. A "Sign In" button in the header of another Workday page is not.
- **`invalid: true`** on any field the site marks `aria-invalid="true"` (wrong or missing). Workday marks them only after Save and
  Continue; the tips say: fix them, Save again, and after 2 stuck Saves stop and record blocked.
- **iCIMS** forms inside a frame on the company page are named iCIMS; `login.icims.com` is not. **Teamtailor** is
  named, with tips on its hidden "Required" labels and on asking before the optional consent box.
  **SmartRecruiters** is named (smartrecruiters.com, smartr.me) with one tip; its shadow-DOM fields were already read in step 2.
- **Agencies and unknown sites:** no reference covers agency forms, so only general rules apply.
  - `application_form` says whether the page looks like the form itself (a named file box, or 3 or more named
    fields, not all of them dropdowns; a name can come from a label, aria label, placeholder or name) or still the ad. A job list's filter dropdowns and links are not a form.
  - A field labelled "leave this blank" (a trap for bots) reads `leave_blank: true`, and `type` (with text), `choose`,
    `tick` (on), `upload` and a `click` on the field refuse it. The label must say "leave this/the (field/box) blank/empty" or "do not fill this/in": "Middle name (leave blank)" is just optional.

**Step 6, proof and "did that do anything?" (2026-10-19):**
- **`page_changed`** after every click. The browser watches the page from just before the click until it settles:
  a new address, a new tab, a frame that navigated, any change in the first 20 frames (open shadow roots included, up to 7 levels) or
  in the clicked control's own frame even past that cap, or a change in the control itself (a tick box) counts. When a frame or the control cannot be read, it counts as changed. Nothing changed: `next` says not to click it again
  (nanobrowser's changed / none receipts).
- **Never blind:** a control whose clicks changed nothing twice in a row is refused (`no_effect`) until another
  action (an answer typed, a box ticked, a scroll or a new page) gives it its tries back.
- **Never twice:** after a final submit on a page, a second submit for the same application on the same page is
  refused (`already_submitted`; another job on the same form page goes to its own `check_submit`), unless the site said it did not take the first (`site_refused`). Even then `check_submit`
  holds a second submit until the user marks the first one "not sent" on Job360, so `next` says to ask them.
- **`repeat_warning`:** the same action with the same ref, text and `url` (open's address) 3 calls in a row (`look` and `scroll` never count) (browser-use's loop nudge).
- **Proof:** when a gated submit lands on the site's confirmation (`confirmation_seen`), a screenshot of that page
  (the tab that shows it, a new one included) is stored as the application's proof (`proof_screenshot`, `via: browser`, the S7 store, at most 3 per
  application). `proof_saved` says whether it was kept; a failed save never undoes the application.

**Slice 4 tool ready, not run yet:** `python -m src.cli browser-probe <url> <url> ...` (add `--json` for rows). It opens
each page READ-ONLY in a fresh throwaway browser (no sign-ins, no typing, no clicking) and reports per site: `ok` (look says it
is the application form; the row counts its fields, file inputs and submit buttons), `no_form` (an ad page), `captcha`, `login_required`, `blocked` or `error`.
Each row also names the playbook the assistant would get (`site`: greenhouse, lever, ashby, workday...), Workday's
page (`form_step`, like 2/7) and how many controls sit past the first read (`more_below`).
It needs the open internet, so run it on Railway (the switch is on there), with a list of real apply links. Not yet confirmed: whether it has ever been run there.
From #966 (seen live after the deploy, 2026-10-20): an Ashby job board is not read as a form; Workday's sign-in step is caught.

**Send starts my assistant (merged 2026-10-20, #913).** Before this, Send / Send all on Needs you only saved the yes,
and the work waited for "run 360". Now, if you saved a start link on **Settings → Connect your assistant**, the
website calls `POST /api/assistant-trigger/fire` once after Send (or once after all of Send all), and Job360 starts your
assistant with the list of every still-open job whose newest send decision is your yes. **One start per click, never one per job**.
A 60-second cool-down, claimed in the database before the call, means two presses at once start one session. A yes
given inside that minute is not in the session that just started: the page says so, and **Start now** on the card
sends every job still waiting on your yes. Each start gets its own session label (`send-` and 8 hex characters), so a second start never shares the first one's browser or kit hold.
- **Claude routine (recommended, PC off):** make a routine on claude.ai/code/routines with the Job360 connector and
  the instructions the card gives you; add an **API trigger**, copy its URL and token into the card. Job360 calls
  `POST https://api.anthropic.com/v1/claude_code/routines/<trig_id>/fire` (`anthropic-version: 2023-06-01`,
  `anthropic-beta: experimental-cc-routine-2026-04-01`, body `{"text": ...}`; Anthropic's docs mark this API as experimental, 30 starts per routine per hour). The routine
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
  yes stays saved. An unexpected error while saving answers a plain 500 with the token already cleared, Sentry scrubs every depth of a frame's variables, and FastAPI's and Starlette's own frames (one holds the raw request body) lose their variables, so the token cannot reach an error report.

**To turn it on:** Railway, backend service, set `BROWSER_AGENT_ENABLED=1`, then redeploy (done on production, seen 2026-10-20). The tool is already listed; a client whose `tools_rev` differs from `mcp_server.tools_rev()` must reconnect.

## 10. Open questions for later

- How many real career sites work from our cloud browser? Slice 4 measures it, from production.
- Send starting a cloud routine is built (#913); not yet confirmed: a real routine run started from Send on production.
- Hosting cost per active user: not yet confirmed.
