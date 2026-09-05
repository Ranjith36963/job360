// Loop 2 — LIVE production synthetic smoke test.
//
// A robot that walks the REAL deployed app corner-by-corner like a human and, on any
// failure, (1) screenshots it, (2) captures the browser console + failed network calls,
// and (3) logs the REASON into the server log system via POST /api/client-log — so a
// human can see WHAT broke and WHY, in the same logs, without watching it live.
//
//   Public corners (landing, login, register, legal)  → always run.
//   Authed corners (applications, CV upload→extraction, bring, pipeline, …)
//        → run only when the robot can LOG ITSELF IN as a dedicated synthetic
//          account, and only after the backend confirms that is whose account it is.
//          See session-gate.mjs for both gates and why they exist.
//
// Env:
//   SMOKE_BASE_URL  frontend URL   (default: live prod frontend)
//   SMOKE_API_URL   backend URL    (default: live prod backend)
//   SMOKE_EMAIL     synthetic account address  ← repo VARIABLE (not secret: it must stay
//                   readable in the log, or a refusal message reads "refused: ***")
//   SMOKE_PASSWORD  synthetic account password ← repo SECRET. Set ONCE; nothing expires.
//   SMOKE_SESSION   LEGACY fallback: a hand-pasted `job360_session` cookie. Still works,
//                   but it dies after 30 days (sessions.py:25) — the run now warns before
//                   that happens and tells you to switch to SMOKE_EMAIL/SMOKE_PASSWORD.
//   SMOKE_ALLOW_WRITES  "1"/"true" to run MUTATING corners (CV upload → extraction).
//        DEFAULT OFF → authed walk is READ-ONLY (only opens pages, never writes).
//   SMOKE_OUT       artifact dir   (default: smoke-artifacts)
//
// Exit code: non-zero if ANY corner failed (so CI goes red + emails the owner).

import { chromium } from "playwright";
import fs from "fs";
import path from "path";

import { acquireSession } from "./session-gate.mjs";

const FE = (process.env.SMOKE_BASE_URL || "https://frontend-production-c608f.up.railway.app").replace(/\/+$/, "");
const BE = (process.env.SMOKE_API_URL || "https://backend-production-80e8e.up.railway.app").replace(/\/+$/, "");
// READ-ONLY by default. Mutating corners (CV upload → extraction) run ONLY when this is
// explicitly on. Safe either way now: the authed walk cannot start at all unless the
// BACKEND confirms the session belongs to a synthetic account (session-gate.mjs).
const ALLOW_WRITES = /^(1|true|yes)$/i.test(process.env.SMOKE_ALLOW_WRITES || "");
const OUT = process.env.SMOKE_OUT || "smoke-artifacts";
fs.mkdirSync(OUT, { recursive: true });

const results = [];
let consoleErrors = [];
let serverErrors = [];

// ── Get a session BEFORE the browser opens ──────────────────────────────────
// Logging in at run time is the whole point of this file's existence: it removes
// the 30-day "paste a fresh cookie into a repo secret" chore, and a chore
// attached to a safety check is a countdown on that check.
//
// A REFUSED result never yields a cookie, so nothing below can accidentally walk
// production as somebody real.
const AUTH = await acquireSession({
  apiBase: BE,
  email: process.env.SMOKE_EMAIL || "",
  password: process.env.SMOKE_PASSWORD || "",
  pastedCookie: process.env.SMOKE_SESSION || "",
  // Scheduled runs demand a working synthetic login. Without this, deleting the
  // secret would turn the authed half OFF silently and the run would stay green.
  requireAuthed: /^(1|true|yes)$/i.test(process.env.SMOKE_REQUIRE_AUTHED || ""),
});
const SESSION = AUTH.status === "ok" ? AUTH.cookie : "";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 }, baseURL: FE });
if (SESSION) {
  await ctx.addCookies([{ name: "job360_session", value: SESSION, domain: new URL(FE).hostname, path: "/" }]);
}
const page = await ctx.newPage();

// Only OUR origins count as a real failure. Third-party beacons (Sentry, PostHog,
// analytics) getting aborted/cancelled on navigation is normal noise — never fail on it.
const OWN_HOSTS = [new URL(FE).hostname, new URL(BE).hostname];
const isOwn = (url) => { try { return OWN_HOSTS.includes(new URL(url).hostname); } catch { return false; } };

page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 300)); });
page.on("requestfailed", (r) => {
  const err = r.failure()?.errorText || "failed";
  // ERR_ABORTED = the browser cancelled it (usually a navigation) — not a real error.
  if (err.includes("ERR_ABORTED")) return;
  if (!isOwn(r.url())) return; // ignore third-party (sentry/posthog/etc.)
  serverErrors.push(`${r.method()} ${r.url().split("?")[0]} — ${err}`);
});
page.on("response", (r) => {
  if (r.status() >= 500 && isOwn(r.url())) {
    serverErrors.push(`${r.request().method()} ${r.url().split("?")[0]} — HTTP ${r.status()}`);
  }
});

async function logReasonToServer(cornerName, reason) {
  // Push the failure reason into the SAME log system (data/logs/) the app uses, so
  // "why did it break" is visible in the logs — not just in this run's console.
  const payload = {
    level: "error",
    message: `[synthetic-smoke] FAIL @ ${cornerName}: ${reason}`.slice(0, 2000),
    url: page.url().slice(0, 500),
    context: JSON.stringify({
      corner: cornerName,
      consoleErrors: consoleErrors.slice(0, 5),
      serverErrors: serverErrors.slice(0, 5),
    }).slice(0, 1000),
  };
  try {
    await fetch(`${BE}/api/client-log`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(SESSION ? { Cookie: `job360_session=${SESSION}` } : {}) },
      body: JSON.stringify(payload),
    });
  } catch { /* logging must never break the run */ }
}

async function corner(name, fn) {
  consoleErrors = []; serverErrors = [];
  const t0 = Date.now();
  try {
    await fn();
    if (serverErrors.length) throw new Error(`server/network error: ${serverErrors[0]}`);
    results.push({ corner: name, ok: true, ms: Date.now() - t0 });
    console.log(`  OK    ${name} (${Date.now() - t0}ms)`);
  } catch (e) {
    const reason = String(e && e.message ? e.message : e).slice(0, 300);
    const shot = path.join(OUT, `FAIL-${name.replace(/[^a-z0-9]+/gi, "_")}.png`);
    try { await page.screenshot({ path: shot, fullPage: true }); } catch { /* ignore */ }
    results.push({ corner: name, ok: false, reason, consoleErrors: consoleErrors.slice(0, 5), serverErrors: serverErrors.slice(0, 5), screenshot: shot });
    await logReasonToServer(name, reason);
    console.log(`  FAIL  ${name} — ${reason}`);
  }
}

async function gotoOK(p) {
  const r = await page.goto(p, { waitUntil: "domcontentloaded", timeout: 30000 });
  if (!r || r.status() >= 400) throw new Error(`GET ${p} → HTTP ${r ? r.status() : "no response"}`);
  await page.waitForTimeout(1200);
}

// AN HTTP 200 IS NOT PROOF YOU ARE LOGGED IN.
//
// 2026-08-09 → 2026-08-11: the synthetic session cookie went stale. `middleware.ts`
// did exactly the right thing — it 307'd every protected path to
// `/login?next=…` — and because a redirect lands on a page that returns 200,
// `gotoOK` was happy. NINE authed corners reported OK for three days while the
// robot was staring at the sign-in card (proof: run 31492026104, the
// FAIL-profile screenshot is the login page, and report.json logs a 401).
// Only the ONE corner that asserted something specific went red, and it went red
// for an unrelated reason — so the alarm named the wrong thing.
//
// Rule: an authed corner must prove it is still authed. Landing on /login is a
// FAILURE, never a pass.
async function gotoAuthed(p) {
  await gotoOK(p);
  const landed = new URL(page.url()).pathname;
  if (landed.startsWith("/login") || landed.startsWith("/register")) {
    throw new Error(
      `bounced to ${landed} — the walk is NOT authenticated, so nothing past this ` +
      `point tests the logged-in app. The session gate passed just before this, so the ` +
      `session died mid-walk or the frontend middleware disagrees with the backend.`,
    );
  }
}
const seeText = (re, timeout = 15000) => page.getByText(re).first().waitFor({ state: "visible", timeout });

const AUTH_LABEL =
  AUTH.status === "ok"
    ? `(authed as ${AUTH.email} via ${AUTH.mode})`
    : AUTH.status === "refused"
      ? "(public only — THE SESSION GATE REFUSED, see the failure below)"
      : "(public only — no synthetic credentials configured)";
console.log(`\n=== LIVE SMOKE vs ${FE} ${AUTH_LABEL} ===`);
for (const w of AUTH.warnings) console.log(`  WARN  ${w}`);

// ── PUBLIC corners (always) ──
await corner("landing loads", async () => { await gotoOK("/"); await seeText(/job360/i); });
await corner("login page renders", async () => { await gotoOK("/login"); await page.locator("input").first().waitFor({ timeout: 15000 }); });
await corner("register page renders", async () => { await gotoOK("/register"); await page.locator("input").first().waitFor({ timeout: 15000 }); });
await corner("privacy page", async () => { await gotoOK("/privacy"); });
await corner("terms page", async () => { await gotoOK("/terms"); });
await corner("contact page", async () => { await gotoOK("/contact"); });

// ── AUTHED corners (need a verified synthetic session) ──
// ── RENDER-TRUTH corners ────────────────────────────────────────────────────
// The half of production that SQL cannot see.
//
// 2026-08-03: the dashboard rendered "Posted NaNw ago" for days. Nothing threw,
// Sentry logged zero events, and all six scheduled detectors stayed green —
// because every one of them queries the database, and the fault only exists
// once a browser has rendered it. The database was merely odd; the SCREEN was
// wrong. Only a real page load can tell the difference.
//
// PATTERN PRECISION IS THE WHOLE GAME. A poison scan that wakes the owner at
// 3am over a false positive dies of distrust within a month. So these patterns
// are chosen to be impossible in natural prose rather than merely suspicious:
// a data-engineering ad may well say "NaN handling" or "undefined behaviour",
// and neither matches. "NaNw ago" and "[object Object]" are only ever bugs.
const POISON = [
  /\bNaN\s*[smhdwy]?\s+ago\b/i,   // "NaNw ago"  <- the 2026-08-03 bug, exactly
  /Invalid Date/i,                 // Date parse failure rendered raw
  /\[object Object\]/,             // a toString() that should have been a field
  /[£$€]\s*NaN/i,                  // salary arithmetic on a non-number
  /\bNaN\s*[%kK](?![a-z])/i,               // a score or salary that became NaN
  /\bundefined\s+ago\b/i,          // the null-ish twin of the same bug
];

async function scanPoison(where) {
  const text = await page.evaluate(() => document.body.innerText || "");
  const hits = POISON.filter((re) => re.test(text))
    .map((re) => (text.match(re) || [""])[0].trim());
  if (hits.length) {
    throw new Error(
      `${where} rendered impossible text: ${JSON.stringify(hits.slice(0, 3))}. ` +
      `This throws no error and reaches no error tracker — it is only visible on screen.`
    );
  }
}

const AUTHED = [
  // The seeker's home since slice 5 (#483): the application list. `/dashboard`
  // and `/jobs` went with the sourcing era — a corner that walks a deleted
  // page turns this monitor permanently red, and a red nobody trusts is a
  // dead monitor.
  ["applications loads", async () => { await gotoAuthed("/applications"); await page.waitForTimeout(2500); }],

  ["applications renders no impossible text", async () => {
    await gotoAuthed("/applications");
    await page.waitForTimeout(2500);
    await scanPoison("applications");
  }],

  [ALLOW_WRITES ? "profile + CV upload → extraction" : "profile page renders (read-only)", async () => {
    await gotoAuthed("/profile");
    if (!ALLOW_WRITES) {
      // READ-ONLY: just prove the profile page renders. No upload → no profile overwrite,
      // no LLM call. Safe to point at a real user's account.
      //
      // `:visible` is load-bearing. `locator("input, button").first()` picks the first
      // match in DOM ORDER, and the first button in the header is the mobile-nav
      // sheet-trigger — permanently hidden at this 1440px viewport. The locator then
      // waits 15s for an element that can never be visible, so the corner failed even
      // on a perfectly healthy page. Filtering in the selector makes `.first()` mean
      // "the first control a human can actually see".
      await page.locator("input:visible, button:visible").first().waitFor({ state: "visible", timeout: 15000 });
      return;
    }
    // MUTATING (opt-in, synthetic accounts only): upload a sample CV and wait for extraction.
    const cv = path.resolve(new URL("./sample-cv.pdf", import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"));
    const fileInput = page.locator('input[type="file"]').first();
    await fileInput.waitFor({ state: "attached", timeout: 15000 });
    await fileInput.setInputFiles(cv);
    // extraction is a real LLM call on prod — wait, then assert a skill from the sample CV shows up
    await page.getByText(/python|airflow|data engineer/i).first().waitFor({ timeout: 90000 });
  }],
  ["bring a job (form renders)", async () => { await gotoAuthed("/bring"); await page.waitForTimeout(1500); }],
  ["pipeline (kanban)", async () => { await gotoAuthed("/pipeline"); await page.waitForTimeout(1500); }],
  ["channels", async () => { await gotoAuthed("/channels"); await page.waitForTimeout(1500); }],
  ["settings", async () => { await gotoAuthed("/settings"); await page.waitForTimeout(1500); }],
  ["notifications", async () => { await gotoAuthed("/notifications"); await page.waitForTimeout(1500); }],
];

// ── The session gate (decided in session-gate.mjs, reported here) ───────────
// Two questions, both answered BEFORE walking nine authed corners:
//   is this session alive?  (PR #313 — a dead cookie made nine corners lie)
//   whose account is it?    (new — the backend names the owner; refuse a human)
// Either way the failure is CONFIG, not an outage, and it says so — one honest
// red beats a puzzle, and a red that blames production cries wolf.
const GATE_CORNER = "synthetic session is live AND belongs to a robot account";

if (AUTH.status === "ok") {
  for (const [name, fn] of AUTHED) await corner(name, fn);
  // A pasted cookie still works, but it is on a countdown. Surface that as a
  // real (non-fatal) result so the owner is told BEFORE it dies, not after.
  for (const w of AUTH.warnings) {
    results.push({ corner: "session will not expire on a clock", ok: null, reason: w });
  }
} else if (AUTH.status === "refused") {
  results.push({ corner: GATE_CORNER, ok: false, reason: AUTH.reason });
  console.log(`  FAIL  ${GATE_CORNER} — ${AUTH.reason}`);
  for (const [name] of AUTHED) {
    results.push({ corner: name, ok: null, reason: "BLOCKED — the session gate refused; not walked (a pass here would be a lie)" });
    console.log(`  SKIP  ${name} — blocked by the session gate`);
  }
} else {
  for (const [name] of AUTHED) {
    results.push({ corner: name, ok: null, reason: AUTH.reason });
    console.log(`  SKIP  ${name} — no synthetic credentials configured`);
  }
}

await browser.close();

const failed = results.filter((r) => r.ok === false);
const passed = results.filter((r) => r.ok === true);
const skipped = results.filter((r) => r.ok === null);
fs.writeFileSync(
  path.join(OUT, "report.json"),
  JSON.stringify(
    {
      ts: new Date().toISOString(),
      frontend: FE,
      backend: BE,
      authed: !!SESSION,
      // HOW the session was obtained, and WHO the backend says it belongs to —
      // never the cookie itself. `mode: "login"` is the no-expiry path.
      sessionMode: AUTH.mode,
      sessionOwner: AUTH.email,
      warnings: AUTH.warnings,
      results,
    },
    null,
    2,
  ),
);

console.log(`\n=== RESULT: ${passed.length} ok · ${failed.length} FAIL · ${skipped.length} skipped ===`);
for (const f of failed) console.log(`  ✗ ${f.corner}: ${f.reason}`);
for (const w of AUTH.warnings) console.log(`  ! ${w}`);
console.log(`report + screenshots → ${OUT}/`);
process.exit(failed.length ? 1 : 0);
