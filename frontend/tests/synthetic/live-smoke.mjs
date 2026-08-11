// Loop 2 — LIVE production synthetic smoke test.
//
// A robot that walks the REAL deployed app corner-by-corner like a human and, on any
// failure, (1) screenshots it, (2) captures the browser console + failed network calls,
// and (3) logs the REASON into the server log system via POST /api/client-log — so a
// human can see WHAT broke and WHY, in the same logs, without watching it live.
//
//   Public corners (landing, login, register, legal)  → always run.
//   Authed corners (dashboard, CV upload→extraction, jobs, tailor, pipeline, …)
//        → run only when SMOKE_SESSION is set to a VERIFIED synthetic account's
//          `job360_session` cookie (prod email-verification can't be automated in CI).
//
// Env:
//   SMOKE_BASE_URL  frontend URL   (default: live prod frontend)
//   SMOKE_API_URL   backend URL    (default: live prod backend)
//   SMOKE_SESSION   verified synthetic account session cookie (enables authed corners)
//   SMOKE_ALLOW_WRITES  "1"/"true" to run MUTATING corners (CV upload → extraction).
//        DEFAULT OFF → authed walk is READ-ONLY (only opens pages, never writes). Turn
//        on ONLY for a throwaway synthetic account — NEVER a real user's cookie, or the
//        6-hourly run would overwrite that user's profile + burn an LLM call each time.
//   SMOKE_OUT       artifact dir   (default: smoke-artifacts)
//
// Exit code: non-zero if ANY corner failed (so CI goes red + emails the owner).

import { chromium } from "playwright";
import fs from "fs";
import path from "path";

const FE = (process.env.SMOKE_BASE_URL || "https://frontend-production-c608f.up.railway.app").replace(/\/+$/, "");
const BE = (process.env.SMOKE_API_URL || "https://backend-production-80e8e.up.railway.app").replace(/\/+$/, "");
const SESSION = process.env.SMOKE_SESSION || "";
// READ-ONLY by default. Mutating corners (CV upload → extraction) run ONLY when this is
// explicitly on — so pointing SMOKE_SESSION at a real account can never overwrite it.
const ALLOW_WRITES = /^(1|true|yes)$/i.test(process.env.SMOKE_ALLOW_WRITES || "");
const OUT = process.env.SMOKE_OUT || "smoke-artifacts";
fs.mkdirSync(OUT, { recursive: true });

const results = [];
let consoleErrors = [];
let serverErrors = [];

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
      `point tests the logged-in app. Refresh the SMOKE_SESSION secret.`,
    );
  }
}
const seeText = (re, timeout = 15000) => page.getByText(re).first().waitFor({ state: "visible", timeout });

console.log(`\n=== LIVE SMOKE vs ${FE} ${SESSION ? "(authed)" : "(public only — no SMOKE_SESSION)"} ===`);

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
  ["dashboard loads", async () => { await gotoAuthed("/dashboard"); await page.waitForTimeout(2500); }],

  ["dashboard renders no impossible text", async () => {
    await gotoAuthed("/dashboard");
    await page.waitForTimeout(2500);
    await scanPoison("dashboard");
  }],

  ["dashboard counts agree with each other", async () => {
    // TWO USER-VISIBLE NUMBERS ON ONE SCREEN MUST NOT CONTRADICT.
    //
    // 2026-08-03: the header said "4078 jobs matched your profile" while the
    // All tab said 100. Both the DB and the API were CORRECT — `total` is
    // computed before the page slice (jobs.py) — but the time-bucket tabs are
    // derived client-side from a second query that inherits the API's default
    // limit=100. So the screen contradicted itself while every backend check
    // passed. No SQL invariant can ever catch this shape; only reading the
    // rendered page can.
    await gotoAuthed("/dashboard");
    await page.waitForTimeout(2500);
    const text = await page.evaluate(() => document.body.innerText || "");

    const header = text.match(/([\d,]+)\s+jobs?\s+matched/i);
    const allTab = text.match(/\bAll\s+([\d,]+)/i);
    if (!header || !allTab) return; // empty feed / layout change — not this check's business

    const num = (s) => parseInt(s.replace(/,/g, ""), 10);
    const total = num(header[1]);
    const shown = num(allTab[1]);

    // A cap is fine — SAYING one number and SHOWING another without telling the
    // user is not. Either they agree, or the page admits it is truncating.
    const admitsTruncation = /showing|first\s+\d|of\s+[\d,]+|see all|show all/i.test(text);
    if (total !== shown && !admitsTruncation) {
      throw new Error(
        `header claims ${total} matches but the All tab shows ${shown}, and nothing on ` +
        `the page tells the user it is truncated. One of these numbers is lying to them.`
      );
    }
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
  ["jobs / search", async () => { await gotoAuthed("/jobs"); await page.waitForTimeout(2500); }],
  ["pipeline (kanban)", async () => { await gotoAuthed("/pipeline"); await page.waitForTimeout(1500); }],
  ["channels", async () => { await gotoAuthed("/channels"); await page.waitForTimeout(1500); }],
  ["settings", async () => { await gotoAuthed("/settings"); await page.waitForTimeout(1500); }],
  ["notifications", async () => { await gotoAuthed("/notifications"); await page.waitForTimeout(1500); }],
];

// ── The session gate ────────────────────────────────────────────────────────
// Ask the backend whether SMOKE_SESSION is still a real session BEFORE walking
// nine authed corners with it. A dead cookie is a CONFIG problem with a one-line
// fix (refresh the secret); letting it leak into the corners turns it into nine
// misleading results and buries the actual cause. One honest red beats a puzzle.
async function sessionIsLive() {
  try {
    const r = await fetch(`${BE}/api/auth/me`, {
      headers: { Cookie: `job360_session=${SESSION}` },
    });
    return { ok: r.ok, status: r.status };
  } catch (e) {
    return { ok: false, status: `unreachable (${String(e && e.message ? e.message : e).slice(0, 80)})` };
  }
}

if (SESSION) {
  const gate = await sessionIsLive();
  if (gate.ok) {
    for (const [name, fn] of AUTHED) await corner(name, fn);
  } else {
    const reason =
      `SMOKE_SESSION is not a valid session — GET ${BE}/api/auth/me returned ${gate.status}. ` +
      `The cookie is set but dead (expired or revoked), so every protected page would ` +
      `redirect to /login and each corner would "pass" against the sign-in card. ` +
      `FIX: log in as the synthetic account, copy its fresh job360_session cookie, and ` +
      `update the SMOKE_SESSION repo secret. This is NOT a production outage.`;
    results.push({ corner: "synthetic session is valid (SMOKE_SESSION)", ok: false, reason });
    console.log(`  FAIL  synthetic session is valid (SMOKE_SESSION) — ${reason}`);
    for (const [name] of AUTHED) {
      results.push({ corner: name, ok: null, reason: "BLOCKED — SMOKE_SESSION is dead; not walked (a pass here would be a lie)" });
      console.log(`  SKIP  ${name} — blocked by a dead SMOKE_SESSION`);
    }
  }
} else {
  for (const [name] of AUTHED) {
    results.push({ corner: name, ok: null, reason: "SKIPPED — set SMOKE_SESSION (a verified synthetic account cookie) to walk authed corners" });
    console.log(`  SKIP  ${name} — no SMOKE_SESSION`);
  }
}

await browser.close();

const failed = results.filter((r) => r.ok === false);
const passed = results.filter((r) => r.ok === true);
const skipped = results.filter((r) => r.ok === null);
fs.writeFileSync(path.join(OUT, "report.json"), JSON.stringify({ ts: new Date().toISOString(), frontend: FE, backend: BE, authed: !!SESSION, results }, null, 2));

console.log(`\n=== RESULT: ${passed.length} ok · ${failed.length} FAIL · ${skipped.length} skipped ===`);
for (const f of failed) console.log(`  ✗ ${f.corner}: ${f.reason}`);
console.log(`report + screenshots → ${OUT}/`);
process.exit(failed.length ? 1 : 0);
