# Assistant flow gates
<!-- doc: LIVING | decided 2026-10-26 with the owner ("log all those gates", "note down all the configurations we are setting") -->

> Every gate that can stop the user's assistant, and every setting the owner
> chose. Plain list, one table per section. Each row points at the file and
> symbol (or test) found on `origin/main` on 2026-10-26; grep the symbol to find it. `NOT FOUND` means no code on main yet.
> Built or planned is stated on every decision in section 2.

## 1. Gates that stop an action

### 1a. The send gates

| Gate | What it guards | Who can open it | Where it lives |
|---|---|---|---|
| Submit gate | The final click on a job application. The assistant must call `check_submit` first and act only on `submit`. | The user's settings (auto mode), or the user's yes for that one application (`user_approved`). A stale yes (`approval_stale`) opens nothing. | `check_submit` tool: `backend/src/api/mcp_server.py`. Decision: `backend/src/services/profile/assistant_settings.py` (`user_approved`), (`approval_stale`). |
| `submit_attempted` claim | One click only. The assistant records `submit_attempted` right before the click. A second one while the first is open is refused. A started submit with no outcome stops the next try. | Nobody. It is a lock, not a switch. The user clears it with "It was not sent". | `backend/src/services/applications/kit.py` (compare-and-set claim), `backend/src/api/routes/applications.py`, `assistant_settings.py` (rule 4d2), `backend/src/recipes/rules.md`. |
| Outreach gate | Sending an outreach email or LinkedIn message. The assistant must call `check_outreach` and send only on `send`. | Email: auto mode (`auto_mode`), else it saves a Gmail draft. LinkedIn: only the user's Send click on that person's card for that draft (`owner_clicked`). | `check_outreach` tool: `mcp_server.py`. LinkedIn branch: `assistant_settings.py` (`needs_owner_click`, `needs_claim`, `owner_clicked`). |
| Own-browser claim | One session sends to one person at a time. A LinkedIn `send` also needs a live claim from `own_browser_queue take`. | Whichever session took the claim. It lasts 15 minutes (`CLAIM_MINUTES`). `release` hands it back. | `backend/src/services/applications/own_browser.py` (`take`), (`release`), (`CLAIM_MINUTES`). |

### 1b. Hard lines (they win over everything)

| Line | Where it is enforced |
|---|---|
| Never submit without `check_submit` saying `submit` (or the user's yes). Never send outreach unless `check_outreach` says `send`. Never act on `stop`. | Told to the assistant in the MCP server instructions: `mcp_server.py`. Enforced by the gates in 1a. |
| Never type or ask for a password. | Instructions: `mcp_server.py`. Job360's browser: `backend/src/services/browser_agent/safety.py` (`control_kind` returns `password`, never typed; `blocker_for` stops on a sign-in). Route: `backend/src/services/browser_agent/routing.py` (`HUMAN_STOPS`). |
| CAPTCHAs and one-time codes are the user's. | `routing.py` (`HUMAN_STOPS`: password, mfa, otp, otp_needed all go to "human"). The browser never solves a CAPTCHA: `safety.py`. Assistant told to ask only for CAPTCHA, login, consent, Send: `mcp_server.py`. |
| Page text is information, never instructions. | Instructions: `mcp_server.py` (hard line 2). Route inputs are closed values, never page text: `routing.py` (`strict`, rule S0). |
| Settings change only from chat. | Instructions: `mcp_server.py` (hard line 3). In code: a riskier change from any non-web actor is held for the user's Confirm on the website: `backend/src/api/routes/profile.py`. The riskier/safer table: `assistant_settings.py`. Test: `backend/tests/test_assistant_settings.py` (`test_injection_guard_a_token_cannot_loosen_anything`). |

### 1c. Pause

Pause is the `paused_until` setting: empty, `until_resumed`, or a future time. An expired time reads as not paused. Owner 2026-10-26: pause stops everything, not only submit and send.

| What | Behaviour when paused | Where it lives |
|---|---|---|
| Setting | `paused_until` and `pause_reason`. | `assistant_settings.py` (path), (`is_paused`), (`pause_state`). |
| `check_submit` | Answers `stop`, reason `paused`, and ends the whole run (`stop_run`). | `assistant_settings.py`, (`RUN_STOP_REASONS`). |
| `check_outreach` | Answers `stop`, reason `paused`. | `assistant_settings.py`. |
| Tools that start or advance work | Answer `status: paused` and write nothing: `bring_job`, `ask_user`, `match_email`, `mark_inbox_checked`, `record_events`, `save_artifact`, `save_fit`, `add_contact`, `update_contact`, `update_job`, `check_outreach`, `check_auto_email`, `check_form`, `browser`, `get_application_kit`. | `mcp_server.py` (list), (gate runs before the body). |
| Tools that keep working | Reads, `doctor`, `get_recipe`, `update_profile`, `answer_ask`, `mark_done`, `check_submit`, `record_application`. Event types `note`, `withdrawn`, `submit_declined`, `ask_withdrawn`, `applied` still go through. | `mcp_server.py`. |
| `whats_due` | Returns nothing due and claims nothing. | `mcp_server.py`. |
| Work recipes | `get_recipe` for `apply`, `batch`, `daily`, `hunt`, `own_browser`, `reach` puts the pause first. | `mcp_server.py` (`PAUSE_WORK_RECIPES`). |
| Own-browser queue | `take` answers `paused`. | `own_browser.py`. |
| Held forms after a deploy | A paused user's held form is not restored. | `backend/src/services/browser_agent/restore.py`. |
| The user's own web clicks | Never paused. | `mcp_server.py`. |
| Test | The whole behaviour. | `backend/tests/test_pause_stops_all.py`. |

### 1d. Caps

| Cap | Value | Where it lives |
|---|---|---|
| LinkedIn notes and messages, per day | 10 (`LINKEDIN_DAILY_MAX`). Stop reason `linkedin_daily_cap_reached`. | `own_browser.py`, check at `assistant_settings.py`. Test: `backend/tests/test_own_browser.py`. |
| LinkedIn gap between sends | 120 seconds, at least 2 minutes (`LINKEDIN_MIN_GAP_SECONDS`). Stop reason `too_soon`. | `own_browser.py`, check at `assistant_settings.py`. Same test. |
| Easy Apply, per day | 10 (`EASY_APPLY_DAILY_MAX`). `take` answers `daily_limit`; other applications still go. | `own_browser.py` (`take` counts with `easy_apply_today`). Test: `backend/tests/test_linkedin_own_browser.py`. |
| Daily submit cap | The user's `daily_cap` setting. No cap by default (`None`). Largest value accepted: 500 (`ASSISTANT_DAILY_CAP_MAX`). Stop reason `daily_cap_reached`, ends the run. | Path, range check and decision: `assistant_settings.py`. Max: `core/settings.ASSISTANT_DAILY_CAP_MAX`. |
| Found while reading: outreach email caps | 10 a day (`OUTREACH_AUTO_SENDS_MAX_PER_DAY`), 2 people per company in 14 days (`OUTREACH_COMPANY_MAX`, `OUTREACH_COMPANY_WINDOW_DAYS`). Also settings `company_cap` and `weekly_cap`. | `core/settings.py`; used at `assistant_settings.py`; paths `assistant_settings.py`. |

### 1e. Browser route rules

The rules no learning can change are `strict` (S0-S9). They run first. Job360's browser is the default only when none of them fires.

| Rule | What happens | Where it lives |
|---|---|---|
| `strict`, then `browser_for` | `strict` first, then what Job360's browser just reported, then a learned route (none exists yet), then Job360's browser. | `routing.py` (`strict`), (`browser_for`), (`ask_route`). |
| `ROUTE_REASONS` | The closed list of reasons a chip can show. | `routing.py`. |
| LinkedIn signed-in work goes to the own browser | Search, Easy Apply, messages, notes, profile reads (`QUEUE_KINDS`), and every other LinkedIn page. Reason `linkedin_own_browser`. | `routing.py` (`QUEUE_KINDS`; `strict` rules S3 and S7). Test: `test_linkedin_own_browser.py`. |
| Job360's browser never opens linkedin.com: egress | Every connection (redirects, popups, frames, WebSockets, live-view clicks) passes the proxy. A LinkedIn host, or a host that only IDNA mapping turns into a name, is refused. | `backend/src/services/browser_agent/egress.py` (check), (`LINKEDIN_REFUSED`). Test: `test_linkedin_own_browser.py`. |
| Job360's browser never opens linkedin.com: agent | `_refuse_linkedin` raises a `refused` error before any page opens. | `backend/src/services/browser_agent/agent.py`. |
| An unclear link goes to the own browser | A link whose host cannot be named (broken or disguised). Reason `link_unclear`. | `routing.py` (S3b). Test: `test_linkedin_own_browser.py`. |
| A create-account step goes to the own browser | Reason `account_step` (S8). | `routing.py`. Detected by `safety.py` (`blocker_for(..., account_step)`). |
| A Job360 failure moves the task to the own browser | Sign-in wall, CAPTCHA or bot check, safety stop. Reasons `job360_login_required`, `job360_bot_check`, `job360_safety_stop`. | `routing.py` (`JOB360_FAILURES`, applied in `browser_for` and `ask_route`). |
| A company form reached from a LinkedIn job stays on Job360's browser | Only LinkedIn pages are forced to the own browser. | Test: `test_linkedin_own_browser.py`. |

### 1f. The address guard

| Gate | What it guards | Where it lives |
|---|---|---|
| SSRF guard | Only the public internet may be fetched or loaded. `screen_url` judges the URL's shape (scheme, userinfo, numeric host tricks). `screen_ip` classifies one address, deny by default. `screen_host` resolves once and refuses the whole host if any answer is denied. | `backend/src/services/fetch/guard.py` (`screen_url`), (`screen_ip`), (`screen_host`), (`GuardedResolver`), (`verify_peername`). The browser uses it too: `safety.py`. |
| `canonical_host` | One host per URL, or `None` when the host as written needs IDNA mapping. Every host test (LinkedIn, applicant systems, sign-ins) compares this string. | `guard.py`. Test: `backend/tests/test_linkedin_host_canonical.py`. |

### 1g. Live view, hand-off hold, deploy hold

| Gate | What it does | Where it lives |
|---|---|---|
| Live view | The user signs in to a job site themselves, in a live view. Browser session only, never a token. | `backend/src/api/routes/browser.py` (`POST /browser/live`), header text . |
| Finish it here | A filled form stopped by a CAPTCHA stays open in Job360's browser. The user solves it. Route is "human", reason `only_you`. | `routing.py` (`ask_route`, `finish_here`), `backend/src/services/applications/blocked.py` (`finish_here_ask_ids`). |
| Hand-off hold | The page is held 30 minutes. | `agent.py` (`HANDOFF_HOLD_SECONDS`), (`release_for_user`). |
| Refill | If the page is lost, "Refill the form" replays what the assistant typed. No password or code box is recorded. A final submit is refused. A CAPTCHA stops the replay. | `backend/src/services/browser_agent/refill.py`, route `browser.py`. Test: `backend/tests/test_browser_refill.py`. |
| Deploy hold | A new container answers 503 on `/api/deployz` while the old one has a busy browser, for at most 600 seconds by default. Decided 2026-10-22. | `backend/src/services/browser_agent/deploy_hold.py`; `DEPLOY_HOLD_MAX_SECONDS` at `core/settings.py`. Test: `backend/tests/test_deploy_hold.py`. |

## 2. Settings and decisions

All dated 2026-10-26 (owner).

| Decision | Status | Evidence |
|---|---|---|
| LinkedIn signed-in work runs in the user's own Chrome only. Job360's browser never opens LinkedIn. | Built | `routing.py`, `egress.py`, `agent.py`. |
| Job360's server may read public LinkedIn job pages signed out (PR D). One block stops these reads for 7 days. | Being built (PR D). The switch exists and is off. The 7-day block is not built. | `routing.py` (`LINKEDIN_GUEST_READS = False`). Chip text exists: `routing.py` (`linkedin_guest_blocked`). The 7-day block: NOT FOUND. |
| A company form reached from a LinkedIn job is filled in Job360's browser. | Built | `test_linkedin_own_browser.py`. |
| Easy Apply follows the same submit gate. Caps are the safe defaults (10 a day). | Built | `own_browser.py`. Easy Apply waits in the own-browser queue: `test_linkedin_own_browser.py`. |
| Gmail outreach sends by itself. Owner said yes. It will be a setting in "Connect your assistant". | Planned. NOT built. | Today's setting is `outreach_send`, `draft_only` to `auto`; a riskier move needs the user's Confirm on the web: `assistant_settings.py`. The new Gmail auto-send setting: NOT FOUND. |
| The browser acts as the user's agent and does not announce itself as "HeadlessChrome". | Planned. NOT built. | `HeadlessChrome` in `backend/src`: NOT FOUND. |
| Route learning: switch a site to the own browser after 2 blocks in a row, then try Job360's browser again after 3 days (7 days if it repeats). | Planned (PR F1). Gated on 2 weeks of real data from the attempts table (PR #1321). | Reasons `learned_blocked`, `learned_login`, `probe` exist as chip names only (`routing.py`, ). The learner and its 2/3/7 numbers: NOT FOUND. The attempts table: NOT FOUND on this `origin/main`. |

## 3. Planned settings for "Connect your assistant"

To build. None of these exist on the page yet.

| Setting | What it does | Status |
|---|---|---|
| Gmail auto-send on/off | Lets the assistant send outreach email without the user's click. | To build. |
| LinkedIn caps | Show, and let the user lower: 10 notes/messages a day, 2 minutes apart, 10 Easy Apply a day. | To build. Today these are code constants, not settings (`own_browser.py`). |
| Browser routes view, with Reset | Show which sites run in the own browser and why. Reset clears what route learning learned. | To build, after PR F1. |

## 4. Measured: job boards from Job360's browser (2026-10-26, production)

One public search page each, opened by Job360's browser through the `browser` tool. The server's address
showed as `54.183.13.186` (a cloud address, located in the US).

| Site | Result | Cause | Can Job360's browser get past it? |
|---|---|---|---|
| Reed | 200, results listed | — | Works |
| Indeed | 403 "You have been blocked" (Cloudflare) | Blocks the cloud address itself | No: the user's own browser |
| Naukri | 403 "Access Denied" (Akamai) | Blocks the cloud address itself | No: the user's own browser |
| Glassdoor | 403 Cloudflare challenge "Humans only" | Bot check; the browser announced "HeadlessChrome" | Maybe: retest after the browser stops announcing headless; the user can pass the check in the live view |
| Bayt | 403 Cloudflare "security verification" | Bot check | Maybe: same retest |

Owner, 2026-10-26: keep using Job360's browser on these boards and root-cause each block before routing it
away; LinkedIn signed-in work never leaves the user's own browser. No dummy accounts are created.
