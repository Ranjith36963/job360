<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Spec: URL fetch on the web
Skills applied: `hard-rules` (M1 never source/rank, M2 store-not-do,
M5 MCP gate parity, #4 the suite runs offline, #12/#25 every per-user route scopes by
`user.id`, #16 lazy imports, #21 assert values not shapes). Status: shipped (PR #496). (The
`intent.md` this spec read from is deleted scaffolding, 2026-09-05 — git history holds it.)

## Measured starting point (2026-09-04, this tree)

- `backend/pyproject.toml` has **no HTML parser of any kind** — no `lxml`, no
  `beautifulsoup4`, no `readability-lxml`, no `selectolax`, no `trafilatura`. Every source
  that reads HTML today strips tags with `re` (`src/services/description_backfill.py:47`
  and the ATS scrapers). So "check what is already in requirements" answers: *nothing is*.
- `aiohttp>=3.14.1` is the house HTTP client; `aioresponses` mocks it and the whole suite
  is offline (`backend/tests/conftest.py:26-89`).
- The rate limiter is `src/services/auth/rate_limit.py::check_and_record(key,
  max_in_window, window_seconds)`; `routes/oauth.py:65-73` is the house call shape,
  including a **global** bucket keyed `"<name>:*"` beside the per-caller one.
- `routes/bring.py:38-39` already fixes the field caps this route must match:
  `_MAX_TEXT = 40_000`, `_MAX_FIELD = 300`, `apply_url` 2,000.
- `tests/test_route_auth_coverage.py:182` imports route modules by name from a
  `ROUTE_MODULES` dict — a route added inside `bring.py` is discovered with no edit there.
- `scripts/drill_registry.py:71` — `DRILL_TIMEOUT_S = 240`, enforced on Linux CI where the
  drills run (`ci.yml:200`, job `chain`, `timeout-minutes: 10`).

## Requirements

R1. **One new route: `POST /api/jobs/fetch-url`.** Declared in
    `backend/src/api/routes/bring.py` beside `bring_job`, `Depends(require_user)` (the
    same gate as `bring.py:86`; **not** `require_verified_user` — no LLM call is spent).
    Request `{url: str}` (max 2,000 chars, the same cap `apply_url` already has).
    Response is **always HTTP 200 with a closed `outcome`** except for auth (401), rate
    limit (429), the feature switch (404) and a malformed body (422). Reason: "the site
    refused us" is a *product* outcome the form must render a sentence for, not a
    transport error the browser's fetch wrapper turns into a red toast.

R2. **The response.**
    ```
    {
      outcome: "ok"|"ssrf_denied"|"invalid_url"|"unreachable"|"blocked"|"timeout"
               |"too_large"|"unsupported_content",
      message: str,                  # one plain sentence, per outcome, server-authored
      final_url: str,                # after redirects; "" when nothing was fetched
      redirects: int,
      title: str, company: str, location: str, description: str,   # "" when not found
      found: ["title","description"],        # which of the four we actually filled
      source_hint: "json_ld"|"meta"|"heuristic"|"",
      bytes_read: int, elapsed_ms: int
    }
    ```
    `found` exists so the form can say *"we got the title and the ad text — add the
    company"* instead of silently leaving a box empty. Rule #21's spirit: report the
    values you actually have, not the shape of the object.

R3. **The outcome enum is closed, and a value exists only when the user's next action
    differs.** The issue named six; this spec ships **eight**, and says why rather than
    folding two real cases into a message that lies:
    | outcome | when | what the user does next |
    |---|---|---|
    | `ok` | we read an HTML page (fields may still be partly empty) | check the fields, submit |
    | `ssrf_denied` | the URL resolves to a denied address, at any hop | use a different link |
    | `invalid_url` | not http(s), unparseable, over-long, userinfo, deceptive IP literal, redirect to a non-web scheme | fix the link |
    | `unreachable` | DNS failure, connection refused, TLS failure, redirect loop / over the hop cap | check the link, or paste |
    | `blocked` | HTTP 4xx/5xx from the site (401/403/429 included — LinkedIn's real answer) | **paste** |
    | `timeout` | the per-request or the whole-journey budget expired | paste, or retry |
    | `too_large` | the decoded body passed the size cap | paste |
    | `unsupported_content` | the page is not HTML (PDF, image, JSON…) | paste |
    Folding `unsupported_content` into `blocked` would tell a user "the site refused us"
    when the site cooperated perfectly and handed us a PDF. Folding `unreachable` into
    `blocked` would send them to paste when the link is simply wrong. A wrong sentence is
    worse than an eighth enum value. **The enum lives in one place** —
    `src/services/fetch/outcomes.py` — and both the Pydantic `Literal` and the frontend
    copy map are pinned to it by frozen tests (items 37, 40).

R4. **The SSRF guard is its own module, and its decision function takes no I/O.**
    `backend/src/services/fetch/guard.py`:
    ```python
    DENY_NETS_V4: tuple[ipaddress.IPv4Network, ...]     # module data, mutable by the drill
    DENY_NETS_V6: tuple[ipaddress.IPv6Network, ...]
    def screen_ip(ip: ipaddress._BaseAddress) -> Optional[str]   # None = allowed, else the reason
    def screen_url(raw: str) -> UrlVerdict                        # scheme/host/shape only, NO DNS
    async def screen_host(host: str, port: int, *, resolve: ResolveFn) -> HostVerdict
    class GuardedResolver(aiohttp.abc.AbstractResolver)           # the anti-TOCTOU device
    ```
    `resolve` is **injected** (default: `aiohttp.resolver.DefaultResolver().resolve`), so
    every unit test and the drill run with a dict-backed fake resolver — no DNS, offline
    suite intact (rule #4). A `clock` callable is injected the same way for the
    whole-journey budget, so no test sleeps.

R5. **No TOCTOU, by construction: the guard IS the resolver.** The fetcher builds
    `aiohttp.TCPConnector(resolver=GuardedResolver(...), family=socket.AF_UNSPEC)`.
    `GuardedResolver.resolve()` performs the **only** name resolution of the hop, screens
    every address it got back, and returns only approved ones. aiohttp then connects to
    exactly those addresses. There is no window between "checked" and "connected" because
    there is no second lookup to rebind.
    - `family=AF_UNSPEC` so **A and AAAA both come back and both are screened**. Screening
      only the family you happen to prefer is how a v6 record smuggles you into `::1`.
    - **If any returned address is denied, the whole host is denied** — we do not filter
      down to the good ones. A host answering with one public and one private address is a
      rebinding attempt, not a multi-homed server we should help.
    - `hostname` is still what aiohttp uses for TLS SNI and certificate verification, so
      HTTPS is not weakened by connecting "by IP".
    - **Belt to those braces:** after the response headers arrive, the fetcher reads the
      real peer from `resp.connection.transport.get_extra_info("peername")` and denies
      unless it is one of the addresses the resolver approved *for this hop*. This proves
      what we connected to from the socket, not from our own bookkeeping.

R6. **Redirects are followed by hand.** `allow_redirects=False`; up to
    `URL_FETCH_MAX_REDIRECTS` (5) hops. **Every hop re-enters the full screen** — R4's
    `screen_url`, then a fresh `screen_host` with a fresh resolution and a fresh
    `GuardedResolver`. A relative `Location` is resolved against the current URL and then
    screened like any other. A redirect to a non-http(s) scheme is `invalid_url`; over the
    cap, or a cycle, is `unreachable`. No cookie jar is carried across hops
    (`ClientSession(cookie_jar=aiohttp.DummyCookieJar())`) and no header we set is
    forwarded to a different host by accident, because each hop is a new request we build.

R7. **Caps are enforced while streaming, and the budgets are separate because the attacks
    are separate.**
    - **Size.** A `Content-Length` that is present and over `URL_FETCH_MAX_BYTES` (2 MiB)
      refuses before a byte is read — the shape `oauth.py:77-82` already uses. A lying or
      absent header still hits the streaming cap: `resp.content.iter_chunked(65536)` into a
      `bytearray`, aborting the moment the accumulator passes the cap → `too_large`. The
      cap is on **decoded** bytes, so a gzip bomb is caught by the same line.
    - **Time, three budgets.** `URL_FETCH_TIMEOUT_S` (10) per request via
      `aiohttp.ClientTimeout(total=…)`; `URL_FETCH_TOTAL_BUDGET_S` (20) across *all* hops,
      checked against the injected clock before each hop (5 hops × 10 s is not a 10 s
      bound, and that arithmetic is how a "timeout" becomes a minute);
      `URL_FETCH_EXTRACT_BUDGET_S` (3) for parsing, because a slow-loris body and a parser
      bomb are different attacks that need different ceilings.
    - **Content type.** The `Content-Type` header's media type must be in
      `URL_FETCH_ALLOWED_CONTENT_TYPES` (`text/html`, `application/xhtml+xml`) →
      otherwise `unsupported_content`, and the body is **not** read. We never sniff: a
      server that says `application/pdf` gets believed, and a server that lies about
      `text/html` gets a bounded, tag-stripping, script-free parse anyway.

R8. **Extraction: a three-rung ladder, standard library only.**
    1. **JSON-LD `schema.org/JobPosting`** — every `<script type="application/ld+json">`
       block is parsed with `json.loads` (and every `@graph` member walked); the first
       object whose `@type` is or contains `JobPosting` gives `title`,
       `hiringOrganization.name`, `jobLocation.address.addressLocality` (+ region/country)
       and `description` (itself HTML → tag-stripped). `source_hint: "json_ld"`.
    2. **Meta tags** — `og:title` / `<title>` for the title, `og:site_name` for the
       company, `og:description` for a short description. `source_hint: "meta"`.
    3. **A light readability heuristic** — an `html.parser.HTMLParser` subclass that drops
       `script/style/noscript/svg/nav/header/footer/aside/form/iframe/template`, keeps
       text per block container with a depth stack, and returns the container subtree with
       the most text that is not mostly link text. `source_hint: "heuristic"`.
    Rungs are tried in order and **merged, not replaced**: rung 2 may fill a company that
    rung 1 left empty. `description` is capped at `_MAX_TEXT` (40,000 — the same constant
    `BringJobRequest` uses, imported, never re-typed), the others at `_MAX_FIELD` (300).

    **Why no dependency — and why not readability-lxml.** Three reasons, in order of
    weight:
    (a) **JSON-LD is the extractor that actually works here, and readability cannot do
    it.** Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Indeed and LinkedIn all
    emit `JobPosting` JSON-LD; that gives structured *title/company/location*, which is
    three of the four fields. Readability returns one blob of main text — it would fill
    `description` and leave the other three empty. The right primary extractor for this
    slice is a 40-line JSON walk.
    (b) **`readability-lxml` drags in `lxml`, a C parser we would then feed
    attacker-controlled bytes** — a larger and less inspectable security surface than the
    one this slice exists to close. `html.parser` is pure Python; its worst case is CPU,
    which R7 already bounds twice (2 MiB in, 3 s to parse).
    (c) Nothing in `pyproject.toml` parses HTML today (measured above), so this is a *new*
    dependency in the prod image, not a reuse.
    **Reversible on evidence:** if the heuristic misses on real company pages, the failure
    mode is `ok` with a thin `description` and the user pastes — the fallback decision 16
    already made. Swapping rung 3 for a library later changes one function.

R9. **Rate limited per user, plus a global budget.** Via
    `src/services/auth/rate_limit.check_and_record`, the `oauth.py:65-73` shape:
    - `url_fetch:{user.id}` — `URL_FETCH_MAX_PER_MINUTE` (6) / 60 s, and
      `URL_FETCH_MAX_PER_HOUR` (60) / 3600 s.
    - `url_fetch:*` — `URL_FETCH_MAX_PER_HOUR_GLOBAL` (2000) / 3600 s.
    **Per user, never per IP:** behind the Next.js rewrite every browser shares the proxy
    address unless `JOB360_TRUST_PROXY=1` is set on the backend service — the trap the
    OAuth slice documented — so an IP key throttles everybody at once. The **global**
    bucket is not redundant: it is what stops a handful of accounts turning Job360 into an
    open relay / scanner for the whole internet, which is the SSRF-adjacent abuse that
    survives a perfect per-address deny list.

R10. **No MCP tool, stated and pinned.** `fetch_url` is deliberately absent from
     `src/api/mcp_server.py` and from `tests/test_mcp_gate_parity.py::TOOL_ROUTES`.
     VISION rule 5: *could Claude Code do this with its own tools? Then expose a store
     tool, not a do tool.* An agent has fetch; giving it ours would make Job360 a proxy for
     an authenticated agent and would put this SSRF surface behind a token instead of a
     browser session. A frozen test (item 34) asserts no MCP tool names this route, so the
     rule is enforced by CI and not by this paragraph.

R11. **The feature is a switch.** `URL_FETCH_ENABLED` (default **true**). Off → the route
     answers **404**, and the frontend's link box is hidden by
     `NEXT_PUBLIC_URL_FETCH_ENABLED`. This is the emergency stop: if the fetcher is ever
     implicated in an incident, the owner sets one env var and the surface is gone without
     a code deploy. **Trap for the PR body:** `NEXT_PUBLIC_*` is inlined at build time, so
     hiding the box is a redeploy — the backend 404 is the control that takes effect on a
     restart, and it is the one that matters.

R12. **The web form.** `frontend/src/app/bring/page.tsx`:
     - A link input + *Fetch* button above the existing fields. Submitting the link with
       Enter fetches; it never submits the form.
     - On `ok`: the four fields are filled, the ones in `found` get a "filled from the
       link — check it" marker, `apply_url` is set to `final_url`, focus moves to the
       title. **The form is not submitted.**
     - On anything else: the outcome's `message` is shown inline (not a toast — a toast
       disappears and this is an instruction), the link is **kept** in `apply_url`, the
       paste box is focused and left empty.
     - The paste box is never disabled and never cleared by a fetch that failed.
     - Nothing from the server is rendered as HTML anywhere on this path — no
       `dangerouslySetInnerHTML`, ever (S10).

## Security guardrails (mandatory section — this is the main event)

This is Job360's SECOND outbound-to-an-arbitrary-URL surface, not the only
one — `services/channels/ssrf_guard.py` guards the first (user-supplied
webhook URLs, checked at create time AND again at send time for DNS
rebinding). The two guards keep separate deny lists with separate call sites;
a new private/reserved range found in one is not automatically denied by the
other, so both need checking when either changes.

### The attack tree, and the answer to each

**A1 — SSRF to cloud metadata.** `169.254.169.254` (AWS/GCP/Azure IMDS),
`fd00:ec2::254` (AWS IMDSv6), `metadata.google.internal`, `100.100.100.200` (Alibaba),
`192.0.0.192` (Oracle).
*Answer:* `169.254.0.0/16` and `fd00::/8` are inside the deny nets of A2, so the addresses
are refused whatever name points at them — the deny list is on the **resolved address**,
never on the hostname, so `metadata.google.internal` needs no special case and a new
provider's magic IP needs no code change if it sits in an already-denied net. The three
metadata addresses are additionally named as their own entries **for the error message and
the drill**, so a regression is reported as "cloud metadata" and not as "some private IP".

**A2 — SSRF to internal services** (the DB on 5433, Redis, the Railway private network,
`localhost:8000` — us).
*Answer:* deny by network, both families, checked on every resolved address of every hop.
```
v4: 0.0.0.0/8  10/8  100.64/10  127/8  169.254/16  172.16/12  192.0.0.0/24
    192.0.2/24 192.168/16  198.18/15  198.51.100/24  203.0.113/24  224/4  240/4
    255.255.255.255/32
v6: ::/128  ::1/128  fc00::/7  fe80::/10  ff00::/8  2001:db8::/32
    64:ff9b::/96 (NAT64)  2002::/16 (6to4)  100::/64 (discard)
```
Implemented as `ipaddress` networks plus the standard-library predicates
(`is_private`, `is_loopback`, `is_link_local`, `is_reserved`, `is_multicast`,
`is_unspecified`) — **both**, because the predicates catch what a hand-typed list forgets
and the list catches what the predicates call "global" (CGNAT `100.64/10` is `is_private`
in modern CPython but was not always; NAT64 and 6to4 are `is_global`). Belt and braces on
purpose: this is the one list where being wrong is a breach.

**A3 — DNS rebinding (the classic TOCTOU).** Resolve → public → we approve → resolve again
at connect time → private.
*Answer:* R5. The guard **is** the resolver, so there is no second resolution to rebind,
and the socket's real peer is re-checked after connect. Frozen tests 8 and 9 simulate a
rebinding resolver (public on call 1, private on call 2) and a peername mismatch.

**A4 — Redirect laundering.** A public URL 302s to `http://169.254.169.254/`, or chains
through several public hosts first, or downgrades https→http, or redirects to
`file:///etc/passwd`.
*Answer:* R6. `allow_redirects=False`; every hop is screened from scratch, scheme first.
The screen is not "is this the same host as before" — it is the whole R4/R5 screen again.
Non-web schemes are `invalid_url`; the hop cap and cycles are `unreachable`.

**A5 — Huge body, and decompression bombs.** A 10 GB response; or 2 KB of gzip that
inflates to 4 GB.
*Answer:* R7. Declared `Content-Length` over the cap refuses before reading. The streaming
accumulator caps **decoded** bytes at `URL_FETCH_MAX_BYTES`, so the bomb is stopped by the
same check that stops the honest large file. Frozen tests 24, 25, 26.

**A6 — Slow loris.** Headers arrive, then one byte per 30 s forever.
*Answer:* R7's `ClientTimeout(total=…)` covers the whole request including the body, and
`URL_FETCH_TOTAL_BUDGET_S` covers the whole journey across hops. Frozen test 27.

**A7 — Content-type lies, and parser bombs.** `Content-Type: text/html` on a 2 MiB file of
nested `<div>`s; or a PDF served as HTML.
*Answer:* the type check refuses non-HTML before reading (R7). For a body that *is*
declared HTML, `html.parser` never executes anything, never fetches a subresource, and
runs under `URL_FETCH_EXTRACT_BUDGET_S` with a nesting-depth ceiling
(`URL_FETCH_MAX_HTML_DEPTH`, 200) — past which extraction stops and returns what it has.
Frozen tests 28, 29.

**A8 — Unicode / IDN / alternate-encoding confusion.** `http://①②⑦.⓪.⓪.①/`,
`http://2130706433/`, `http://0177.0.0.1/`, `http://0x7f000001/`,
`http://user@evil.com@10.0.0.1/`, a host with an empty label, a trailing dot,
a full-width colon.
*Answer:* the URL is normalised **once, before anything else**, and the normalised form is
what both the screen and the request use:
- `urlsplit`; scheme must be exactly `http` or `https` (lowercased); no userinfo (`@` in
  netloc) at all → `invalid_url`, because "which side of the second `@` is the host" is a
  question no two parsers answer identically;
- host lowercased, trailing dot stripped, IDNA-encoded to its A-label (`idna` codec);
  non-ASCII that survives encoding, an empty label, or a label starting/ending with `-`
  → `invalid_url`;
- **if the host parses as an IP under `ipaddress`, screen it as an IP.** If it does *not*
  parse but is composed only of digits, dots and `0x`/`0` prefixes, **refuse it as
  `invalid_url` and never hand it to a resolver** — `inet_aton` accepts decimal, octal and
  hex forms that `ipaddress` rejects, and that gap is precisely how `2130706433` becomes
  `127.0.0.1` after our check has passed.
Frozen tests 10–13.

**A9 — Job360 as an open proxy, port scanner, or timing oracle.** Point us at 65,535
ports of a host and time the answers; or use us to reach a third party from our IP.
*Answer:* three parts, and one honest residual.
- The per-user and **global** rate limits (R9) bound the volume — the global bucket is the
  one that matters here.
- A denied address returns `ssrf_denied` **before any socket is opened**, so an internal
  scan learns nothing it did not already know ("that address is private").
- We are still, for public addresses, a fetcher with our IP. That is inherent to the
  feature. **Residual, stated:** an attacker can use us to fetch public URLs at the rate
  limit, and can time public hosts. Not mitigated further; the volume ceiling is the
  control, and the audit log records host + outcome for every call so abuse is visible.

**A10 — Hostile page content: stored XSS and prompt injection.**
*Answer:* the response carries **extracted text only — never raw HTML**. Tags are
stripped, entities unescaped exactly once, and a frozen test (item 35) asserts no `<`
survives into any returned field from a fixture full of markup. The frontend renders every
field in a plain text node; `dangerouslySetInnerHTML` appears nowhere on this path.
*Prompt injection is pre-existing and unchanged:* a fetched description reaches the web
tailor's LLM exactly as a pasted one does today. This slice does not add that exposure and
does not claim to fix it — saying otherwise would be the more dangerous statement.

**A11 — Outbound credential leakage.** Our session cookie, an `Authorization` header, or a
`Referer` naming an internal URL, reaching a stranger's server.
*Answer:* the fetch uses its own `ClientSession` with `DummyCookieJar`, sends exactly
`User-Agent` and `Accept`, and sends no `Referer`, no `Authorization`, no cookie, on any
hop. The user's session never touches the outbound request. `auth=None` explicitly.

**A12 — Log injection and secret leakage through the URL.** A job link can carry a token
in its query string; a URL can carry a newline.
*Answer:* we log **scheme + host + path only, query string dropped**, through the existing
log-injection filter (which sits on the handlers, not the logger — the trap the security
round already paid for). The audit event is `url_fetched` with
`{host, outcome, bytes, ms, redirects, user_id}` — never the query, never the body, never
the extracted text.

### Numbered guardrails

S1. **Auth on the route.** `Depends(require_user)`; `tests/test_route_auth_coverage.py`
    discovers it automatically from `bring.py` and fails if it is unprotected. No entry is
    added to `PUBLIC_ROUTES`.
S2. **Nothing is stored, and nothing is read per-user.** The route touches no table, so
    there is no `user_id` scoping question — and that is itself the guardrail: a route
    that persisted the fetched page would need one, so it does not persist it.
S3. **Deny-by-default on the address, never on the name.** Any address the guard cannot
    classify is denied. A hostname is never allow-listed; the decision is always made on
    the resolved IP.
S4. **All-or-nothing per host** (R5): one denied address denies the host.
S5. **The resolver is the check** (R5) and the socket peer is re-verified (R5 belt).
S6. **Every hop is a fresh full screen** (R6).
S7. **Three independent budgets** — bytes, per-request time, whole-journey time — plus a
    parse budget and a nesting ceiling (R7, A7).
S8. **Two rate-limit buckets, per user and global** (R9). Per user, not per IP, for the
    proxy reason.
S9. **`URL_FETCH_ALLOW_NETS` — the escape hatch, deliberately loud.** A self-hosted
    deployment may legitimately need to reach an internal careers page. The parameter
    exists (constraint 6: anything that varies is a parameter), **defaults to empty**, is
    checked *after* the deny list as an explicit exception, and **logs a WARNING naming
    the net every single time it lets something through**. A frozen test asserts the
    default changes nothing (item 15). It is not a convenience; it is a documented hole
    with an alarm on it.
S10. **Extracted text is text.** No raw HTML in the response, no HTML rendering in the
     form (A10).
S11. **No outbound credentials, no cookie jar, no Referer** (A11).
S12. **The audit log carries no bodies and no query strings** (A12).
S13. **The kill switch is server-side.** `URL_FETCH_ENABLED=false` 404s the route on a
     restart; the frontend flag only hides the button and needs a rebuild (R11).
S14. **The guard declares a drill that can go red** — `scripts/ssrf_drill.py`, run in
     `ci.yml`'s `chain` job, declared `drilled` in `scripts/drill_registry.py`. See below.

## The SSRF drill (`scripts/ssrf_drill.py`)

The registry LAW: *a guard is trusted because someone has watched it go RED.* A pytest
file is not enough — `drill_registry.discover()` only knows about scripts a workflow
invokes, and the ten dead guards in its docstring were all "correct-looking code in the
wrong position". So the guard gets a script that **breaks it on purpose and demands each
break is caught**.

How it mutates: it copies `guard.py` / `fetcher.py` / `extract.py` into a temp dir, applies
**one anchored text mutation per case**, imports the mutated copy under a fresh module
name, and re-runs the attack. Anchored, and this matters: **if an anchor is not found the
drill fails loudly** rather than applying zero mutations and reporting a pass — that is
exactly the "sed range whose anchor had been deleted" failure the registry's own docstring
names. Every case also asserts the attack is **blocked** against the unmutated module
first, so a case can never pass because the attack never worked.

| # | mutation | attack that must now land |
|---|---|---|
| 1 | drop `DENY_NETS_V6` | `http://[::1]/` and `http://[fd00::1]/` |
| 2 | drop the v4-mapped-v6 unwrap | `::ffff:169.254.169.254` |
| 3 | make the redirect loop screen only the first hop | public → 302 → `10.0.0.1` |
| 4 | filter denied addresses instead of denying the host | a host resolving to 1 public + 1 private |
| 5 | remove the streaming size cap | a 100 MB body is read whole |
| 6 | remove the scheme check | `file:///etc/passwd` accepted |
| 7 | remove the content-type check | a PDF parsed as HTML |
| 8 | remove the peername re-check | resolver approves A, socket lands on `127.0.0.1` |
| 9 | remove the deceptive-IP-literal refusal | `http://2130706433/` reaches a resolver |
| 10 | **NEGATIVE CONTROL** — no mutation | a public host stays allowed; the drill fails if the guard denies it |

Case 10 is held to the same standard as the breaks. A guard that denies everything passes
cases 1–9 and is useless; the negative control is what makes the other nine mean something.

**Offline and fast.** No DNS, no socket, no network: the resolver is injected and the
responses are fakes. Budget: the drill prints its own elapsed time and the target is under
5 s, well inside `DRILL_TIMEOUT_S = 240` (`drill_registry.py:71`) — the
`check_workflow_slack_wiring` entry is the cautionary tale of a drill that could not
finish on Windows, so the wall-clock claim is measured on Linux CI, where `--run-drills`
runs, and not asserted from a local run.

**Registry entry** (`scripts/drill_registry.py`, alphabetical position):
```python
"scripts/ssrf_drill.py": Guard(
    status="drilled",
    # The guard on the one route that makes outbound requests to a URL a
    # stranger chose. Ten mutations, each a real bypass, plus a negative
    # control: a guard that denies every host passes all ten breaks and is
    # useless. Offline — the resolver is injected, no DNS, no sockets.
    drill=[sys.executable, "scripts/ssrf_drill.py", "--drill"],
),
```
and the `ci.yml` step, in the `chain` job beside the others:
```yaml
- name: The SSRF guard can still fail
  run: python scripts/ssrf_drill.py --drill
```
**Both land in the same PR.** Splitting them is how a workflow ends up invoking a script
no registry knows about (`drill_registry.py:156-163`), and the entry-before-wiring order
produces a STALE ENTRY instead.

## Settings — every cap a parameter (`backend/src/core/settings.py`)

```
URL_FETCH_ENABLED                = _env_flag("URL_FETCH_ENABLED", True)
URL_FETCH_MAX_BYTES              = 2 * 1024 * 1024
URL_FETCH_TIMEOUT_S              = 10       # per request
URL_FETCH_TOTAL_BUDGET_S         = 20       # the whole journey, all hops
URL_FETCH_EXTRACT_BUDGET_S       = 3        # parsing only
URL_FETCH_MAX_REDIRECTS          = 5
URL_FETCH_MAX_HTML_DEPTH         = 200
URL_FETCH_MAX_PER_MINUTE         = 6        # per user
URL_FETCH_MAX_PER_HOUR           = 60       # per user
URL_FETCH_MAX_PER_HOUR_GLOBAL    = 2000     # all users — the open-relay ceiling
URL_FETCH_ALLOWED_CONTENT_TYPES  = _env_list(..., ("text/html", "application/xhtml+xml"))
URL_FETCH_USER_AGENT             = "Job360/1.0 (+https://job360.uk/bot; user-initiated)"
URL_FETCH_EXTRA_DENY_NETS        = _env_list(..., ())   # add a net without a deploy
URL_FETCH_ALLOW_NETS             = _env_list(..., ())   # S9 — loud, empty by default
```
Read through the existing `_env_flag` (`settings.py:418`) / `_env_list` (`:426`) /
`int(os.getenv(...))` house style. Frontend: `NEXT_PUBLIC_URL_FETCH_ENABLED`.

## Frozen tests

**`backend/tests/test_url_fetch_guard.py`** — pure unit, injected resolver, no DB, no
network, no DNS:
1. `test_aws_metadata_v4_is_denied` — `169.254.169.254`, and the reason names metadata.
2. `test_aws_metadata_v6_is_denied` — `fd00:ec2::254`.
3. `test_every_private_v4_net_is_denied` — parametrised: `10.0.0.1`, `172.16.0.1`,
   `192.168.1.1`, `127.0.0.1`, `0.0.0.0`, `169.254.0.1`, `100.64.0.1`, `192.0.0.1`,
   `198.18.0.1`, `224.0.0.1`, `240.0.0.1`, `255.255.255.255`.
4. `test_every_reserved_v6_net_is_denied` — `::1`, `::`, `fc00::1`, `fe80::1`, `ff02::1`,
   `2001:db8::1`, `64:ff9b::7f00:1`, `2002:7f00:1::`.
5. `test_v4_mapped_v6_is_unwrapped_then_denied` — `::ffff:169.254.169.254`,
   `::ffff:10.0.0.1`.
6. **NEGATIVE CONTROL** `test_a_public_v4_and_v6_are_allowed` — `93.184.216.34`,
   `2606:2800:220:1:248:1893:25c8:1946`. A guard that denies everything is not a guard.
7. `test_a_host_with_one_public_and_one_private_address_is_denied_whole` (S4).
8. `test_dns_rebinding_cannot_win` — the fake resolver answers public then private; assert
   exactly **one** resolver call per hop and that the connection used the screened address.
9. `test_a_peername_the_resolver_never_approved_is_ssrf_denied` (R5 belt).
10. `test_non_web_schemes_are_invalid_url` — `file:`, `ftp:`, `gopher:`, `javascript:`,
    `data:`, `//no-scheme`.
11. `test_deceptive_ip_literals_never_reach_the_resolver` — `2130706433`, `0177.0.0.1`,
    `0x7f000001`; assert the resolver was called **zero** times.
12. `test_userinfo_in_the_url_is_invalid_url` — `http://user:p@evil.com@10.0.0.1/`.
13. `test_idn_is_screened_on_the_a_label` — a Cyrillic host is IDNA-encoded before the
    screen; an empty label / trailing-dot / leading-hyphen host is `invalid_url`.
14. `test_extra_deny_nets_parameter_adds_a_net` — and the default adds nothing.
15. `test_allow_nets_is_empty_by_default_and_changes_nothing` (S9), plus: set it and the
    WARNING is emitted naming the net.

**`backend/tests/test_url_fetch.py`** — route + fetcher, `aioresponses`, offline:
16. `test_json_ld_page_fills_all_four_fields` — fixture
    `tests/fixtures/url_fetch/greenhouse_jobposting.html`; `source_hint == "json_ld"`,
    `found` lists all four (#21: assert the values, not that the keys exist).
17. `test_a_plain_company_page_falls_back_to_the_heuristic` — fixture
    `plain_company_page.html`; `source_hint == "heuristic"`, `company == ""` and `"company"`
    absent from `found`.
18. `test_two_public_redirects_are_followed` — `redirects == 2`, `final_url` is the last.
19. `test_a_redirect_to_a_private_address_is_ssrf_denied` — and no body was read.
20. `test_over_the_redirect_cap_and_a_cycle_are_unreachable`.
21. `test_a_redirect_to_a_non_web_scheme_is_invalid_url`.
22. `test_a_403_is_blocked_and_the_message_says_paste` — LinkedIn's real answer.
23. `test_a_429_is_blocked`.
24. `test_an_oversized_content_length_is_refused_before_reading` — assert zero body reads.
25. `test_a_lying_content_length_is_caught_by_the_streaming_cap` — bytes read never exceed
    the cap plus one chunk.
26. `test_a_gzip_bomb_is_too_large` — the cap is on decoded bytes.
27. `test_a_slow_body_is_timeout` — injected clock, no real sleep.
28. `test_a_pdf_is_unsupported_content` — and the body is not read.
29. `test_a_deeply_nested_html_bomb_does_not_hang` — stops at the depth ceiling / extract
    budget and still answers.
30. `test_anonymous_is_401`.
31. `test_rate_limited_per_user_not_per_ip` — over the minute cap → 429; **a second user
    is unaffected**, which is what proves the key is the user.
32. `test_the_global_budget_stops_a_fresh_user` (R9).
33. `test_the_route_404s_when_url_fetch_is_disabled` (R11).
34. `test_no_mcp_tool_fetches_a_url` — `fetch_url` is not among `mcp_server`'s tool names
    and has no `TOOL_ROUTES` row (VISION rule 5, R10).
35. `test_no_raw_html_survives_into_the_response` — a markup-heavy fixture; no `<` in any
    returned field.
36. `test_the_audit_record_carries_the_host_and_not_the_query_or_the_body` (A12/S12).
37. `test_the_outcome_enum_is_closed_and_single_sourced` — the Pydantic `Literal` equals
    `outcomes.OUTCOMES` exactly, and every value has a non-empty message.

**Playwright `frontend/tests/e2e/bring-url.spec.ts`** — house style: fake the
`job360_session` cookie, `page.route` the API, assert the DOM (as
`tests/e2e/feed-visibility.spec.ts:21`):
38. Paste a URL → *Fetch* → the mocked `ok` response fills all four fields, `apply_url`
    holds `final_url`, and **the form was not submitted** (no `POST /jobs/bring` seen).
39. A mocked `blocked` response shows the paste-fallback sentence inline, keeps the link
    in the link field, and leaves the paste box focused and empty.
40. Every outcome value renders a distinct, non-empty message — drives the copy map and
    fails if a new enum value is added without one.

**`scripts/ssrf_drill.py --drill`** — the ten cases above; run by `ci.yml` and by
`drill_registry.py --run-drills`.

## Done when
> A LinkedIn link, an Indeed link, a Workday link and a company careers link are each
> pasted into `/bring`: each one either fills the form or falls back to paste with a clear
> sentence — and `python scripts/ssrf_drill.py --drill` can be made to go RED by removing
> any one of the guard's controls.

## Flagged concerns
C1. **We cannot prove the bot walls from CI.** Every test here is offline against fixtures,
    so "LinkedIn returns 403" is an assumption about the live world until the verifier
    drives a real browser at a real link. The done-when is closed by the verifier and by
    the owner in production, never by a green suite.
C2. **The heuristic rung is the weakest part** and it is the one with no external
    reference implementation. Mitigated by the ladder (JSON-LD carries the boards) and by
    the fallback (thin description → the user pastes). If it turns out to be bad in the
    wild, replacing rung 3 with a library is a one-function change — spec R8 was written
    so that stays true.
C3. **`URL_FETCH_ALLOW_NETS` is a hole with an alarm on it** (S9). It exists because the
    parameter rule demands it. If the owner would rather it did not exist at all, deleting
    it costs one constant and one test.
C4. **A9's residual is real:** we remain a fetcher of public URLs on Job360's IP, bounded
    only by the rate limits. Stated, not solved.
C5. **`NEXT_PUBLIC_URL_FETCH_ENABLED` is build-time** — hiding the button is a redeploy.
    The backend 404 is the control that actually stops the surface (R11).
C6. **Windows full-suite flake** (psycopg exit 139 on a second back-to-back run) —
    targeted gate locally, Linux CI is the verdict.
