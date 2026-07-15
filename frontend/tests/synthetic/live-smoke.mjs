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
const AUTHED = [
  ["dashboard loads", async () => { await gotoOK("/dashboard"); await page.waitForTimeout(2500); }],
  [ALLOW_WRITES ? "profile + CV upload → extraction" : "profile page renders (read-only)", async () => {
    await gotoOK("/profile");
    if (!ALLOW_WRITES) {
      // READ-ONLY: just prove the profile page renders. No upload → no profile overwrite,
      // no LLM call. Safe to point at a real user's account.
      await page.locator("input, button").first().waitFor({ state: "visible", timeout: 15000 });
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
  ["jobs / search", async () => { await gotoOK("/jobs"); await page.waitForTimeout(2500); }],
  ["pipeline (kanban)", async () => { await gotoOK("/pipeline"); await page.waitForTimeout(1500); }],
  ["channels", async () => { await gotoOK("/channels"); await page.waitForTimeout(1500); }],
  ["settings", async () => { await gotoOK("/settings"); await page.waitForTimeout(1500); }],
  ["notifications", async () => { await gotoOK("/notifications"); await page.waitForTimeout(1500); }],
];

if (SESSION) {
  for (const [name, fn] of AUTHED) await corner(name, fn);
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
