#!/usr/bin/env node
/**
 * DESIGN PASS — the loop that captures the HAPPY path.
 *
 * ── why this exists ───────────────────────────────────────────────────────────
 * Every other frontend check in this repo answers "did something BREAK?", and
 * that shape is why none of them can review a design:
 *
 *   ci.yml frontend job      tsc + eslint + next build.  Compiling is not looking.
 *   tests/e2e/*.spec.ts      backend mocked via route.fulfill; playwright.config
 *                            sets trace:"on-first-retry", so a PASSING spec
 *                            produces no image at all.
 *   tests/synthetic/         live-smoke.mjs screenshots inside its catch block
 *                            (live-smoke.mjs:125) — failures only.
 *   uptime.yml               HTTP 200.
 *   journey / product-health reads the DATABASE, never the screen.
 *
 * A page that loads, returns 200, and looks terrible is GREEN in all of them and
 * emits zero pixels. So this runner inverts the rule: it screenshots every route
 * UNCONDITIONALLY, pass or fail, and puts the images somewhere a human (or an
 * agent) can actually look at them.
 *
 * It is a CAMERA, not a gate. It has no assertions and never fails the build on
 * an opinion — the judgement happens by looking at the output.
 *
 * ── usage ─────────────────────────────────────────────────────────────────────
 *   node tests/design/design-pass.mjs
 *
 *   DESIGN_BASE_URL   frontend to walk        (default http://localhost:3100)
 *   DESIGN_API_URL    backend, for :id lookup (default http://localhost:8100)
 *   DESIGN_SESSION    a real job360_session cookie value; without it the authed
 *                     routes are captured as whatever an anonymous visitor sees
 *                     (normally the login redirect) and marked `authed: false`
 *   DESIGN_OUT        output dir              (default design-shots)
 *   DESIGN_ONLY       comma-separated route names to limit the run
 *   DESIGN_VIEWPORTS  comma-separated viewport names (default all)
 *   DESIGN_THEMES     comma-separated themes  (default dark,light)
 *
 * Output: <out>/<route>.<viewport>.<theme>.png, report.json, index.html
 */

import { chromium } from "@playwright/test";
import { mkdir, writeFile, rm } from "node:fs/promises";
import path from "node:path";
import { ROUTES, VIEWPORTS, THEMES } from "./routes.mjs";

const BASE = (process.env.DESIGN_BASE_URL || "http://localhost:3100").replace(/\/$/, "");
const API = (process.env.DESIGN_API_URL || "http://localhost:8100").replace(/\/$/, "");
const OUT = process.env.DESIGN_OUT || "design-shots";
const SESSION = process.env.DESIGN_SESSION || "";

const only = (process.env.DESIGN_ONLY || "").split(",").map((s) => s.trim()).filter(Boolean);
const viewports = filterByName(VIEWPORTS, process.env.DESIGN_VIEWPORTS);
const themes = process.env.DESIGN_THEMES
  ? process.env.DESIGN_THEMES.split(",").map((s) => s.trim()).filter(Boolean)
  : THEMES;

function filterByName(list, csv) {
  if (!csv) return list;
  const want = new Set(csv.split(",").map((s) => s.trim()).filter(Boolean));
  return list.filter((v) => want.has(v.name));
}

// ── setup ────────────────────────────────────────────────────────────────────

await rm(OUT, { recursive: true, force: true });
await mkdir(OUT, { recursive: true });

const browser = await chromium.launch();
const results = [];

// Resolve dynamic paths ONCE up front. A route that cannot resolve (empty
// catalog, no session) is recorded as skipped with the reason rather than
// silently vanishing from the sheet — an absent page and an unreviewed page
// look identical otherwise.
const cookieHeader = SESSION ? `job360_session=${SESSION}` : "";
const plan = [];
for (const route of ROUTES) {
  if (only.length && !only.includes(route.name)) continue;
  if (route.skip) {
    plan.push({ ...route, resolvedPath: null, skipReason: route.skip });
    continue;
  }
  let resolvedPath = route.path;
  if (route.dynamic) {
    try {
      resolvedPath = await route.resolve({ apiBase: API, cookieHeader });
    } catch (e) {
      resolvedPath = null;
      route.resolveError = String(e?.message || e);
    }
    if (!resolvedPath) {
      plan.push({
        ...route,
        resolvedPath: null,
        skipReason: route.resolveError
          ? `could not resolve a real id: ${route.resolveError}`
          : "could not resolve a real id (empty catalog or no session?)",
      });
      continue;
    }
  }
  plan.push({ ...route, resolvedPath });
}

console.log(`design pass -> ${BASE}`);
console.log(
  `${plan.filter((r) => r.resolvedPath).length} routes x ${viewports.length} viewports x ${themes.length} themes` +
    `${SESSION ? "" : "   (no DESIGN_SESSION: authed routes will show the anonymous view)"}`,
);

// ── walk ─────────────────────────────────────────────────────────────────────

for (const route of plan) {
  if (!route.resolvedPath) {
    results.push({ route: route.name, path: route.path, skipped: route.skipReason, shots: [] });
    console.log(`  - ${route.name.padEnd(24)} SKIPPED (${route.skipReason})`);
    continue;
  }

  for (const viewport of viewports) {
    for (const theme of themes) {
      const shot = await capture(route, viewport, theme);
      results.push(shot);
      const flag = shot.error
        ? "ERROR"
        : [
            shot.redirected ? `-> ${shot.finalPath}` : "",
            shot.stillLoading ? "STILL LOADING after 30s" : "",
            shot.consoleErrors.length ? `${shot.consoleErrors.length} console` : "",
            shot.failedRequests.length ? `${shot.failedRequests.length} net` : "",
          ]
            .filter(Boolean)
            .join(" ") || "ok";
      console.log(`  - ${`${route.name} ${viewport.name}/${theme}`.padEnd(40)} ${flag}`);
    }
  }
}

await browser.close();

// ── report ───────────────────────────────────────────────────────────────────

const report = {
  baseUrl: BASE,
  authenticated: Boolean(SESSION),
  viewports: viewports.map((v) => v.name),
  themes,
  shots: results,
};
await writeFile(path.join(OUT, "report.json"), JSON.stringify(report, null, 2));
await writeFile(path.join(OUT, "index.html"), contactSheet(report));

const errored = results.filter((r) => r.error);
const noisy = results.filter((r) => r.consoleErrors?.length || r.failedRequests?.length);
console.log(`\nshots + report + contact sheet -> ${OUT}/`);
console.log(`captured ${results.filter((r) => r.file).length} images`);
if (errored.length) console.log(`${errored.length} page(s) failed to load`);
if (noisy.length) console.log(`${noisy.length} shot(s) had console or network errors`);
console.log(`open ${path.join(OUT, "index.html")} to see them all at once`);

// ── capture ──────────────────────────────────────────────────────────────────

async function capture(route, viewport, theme) {
  const context = await browser.newContext({
    viewport: { width: viewport.width, height: viewport.height },
    // Real device pixel ratio on mobile — a 1x mobile shot hides the blurry-asset
    // and tap-target problems that only show at 2x.
    deviceScaleFactor: viewport.name === "mobile" ? 2 : 1,
    colorScheme: theme === "dark" ? "dark" : "light",
  });

  // next-themes persists to localStorage under `job360-theme` and the server
  // renders `class="dark"` by default (src/app/layout.tsx:59). Seeding storage
  // BEFORE the first paint is what makes the light-mode shot real rather than a
  // dark page that flips a frame later.
  await context.addInitScript((t) => {
    try {
      window.localStorage.setItem("job360-theme", t);
    } catch {
      /* storage can throw in some contexts; the colorScheme hint still applies */
    }
  }, theme);

  if (SESSION) {
    await context.addCookies([
      {
        name: "job360_session",
        value: SESSION,
        domain: new URL(BASE).hostname,
        path: "/",
      },
    ]);
  }

  const page = await context.newPage();
  const consoleErrors = [];
  const failedRequests = [];

  // Chrome logs a bare "Failed to load resource: ... 401" with NO url, so the
  // text alone cannot tell the benign logged-out /api/auth/me probe from a real
  // failure. The response handler below sees the url and records the genuinely
  // benign ones here, so this filter can drop the matching console line.
  const benignStatuses = new Set();
  page.on("console", (msg) => {
    if (msg.type() !== "error") return;
    const text = msg.text();
    if (isBenign(text)) return;
    const m = /status of (\d{3})/.exec(text);
    if (m && benignStatuses.has(Number(m[1]))) return;
    consoleErrors.push(text.slice(0, 300));
  });
  page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${String(err).slice(0, 300)}`));
  page.on("requestfailed", (req) =>
    failedRequests.push(`${req.method()} ${req.url().slice(0, 200)} — ${req.failure()?.errorText}`),
  );
  page.on("response", (res) => {
    if (res.status() < 400) return;
    if (isBenignResponse(res)) {
      benignStatuses.add(res.status());
      return;
    }
    failedRequests.push(`${res.status()} ${res.url().slice(0, 200)}`);
  });

  const file = `${route.name}.${viewport.name}.${theme}.png`;
  const record = {
    route: route.name,
    path: route.resolvedPath,
    viewport: viewport.name,
    theme,
    file,
    consoleErrors,
    failedRequests,
  };

  try {
    // NOT "networkidle". The signed-in pages keep a TanStack Query refetch and a
    // status poll running, so the network never goes quiet and every authed
    // route timed out with zero images — the exact pages worth reviewing.
    // "domcontentloaded" plus an explicit settle below is both faster and
    // survives a page that is legitimately never idle.
    const res = await page.goto(`${BASE}${route.resolvedPath}`, {
      waitUntil: "domcontentloaded",
      timeout: 60_000,
    });
    record.status = res?.status() ?? null;

    // Wait for the SKELETONS to clear, and wait on the signal this app actually
    // emits. An earlier version watched [aria-busy] — which nothing here sets —
    // so it never waited at all, and /profile was photographed mid-load as 18
    // grey blocks. That image reads exactly like a broken empty state, and it
    // is simply a screenshot taken too early: the same page resolves to the CV
    // uploader between 3s and 8s. A camera that fires early does not report a
    // slow page, it invents a bug.
    //
    // shadcn's Skeleton is a div.animate-pulse, so counting those is the honest
    // ready signal. Best-effort — a page with no skeletons proceeds immediately.
    await page
      .waitForFunction(() => document.querySelectorAll(".animate-pulse").length === 0, {
        timeout: 30_000,
      })
      .catch(() => {
        record.stillLoading = true;
      });

    // Let entry animations and skeleton->content swaps settle. Screenshotting a
    // page mid-transition produces a half-faded image that reads as a design bug
    // and is not one.
    await page.waitForTimeout(2000);

    // `next dev` paints its own chrome — the issue-count badge and the Turbopack
    // logo — pinned over the bottom corners of the page. Neither exists in the
    // deployed build, and they sit exactly where a mobile call to action does,
    // so leaving them in makes local shots disagree with production for reasons
    // that have nothing to do with the design.
    await page.addStyleTag({
      content: `nextjs-portal, [data-nextjs-toast], #__next-build-watcher { display: none !important; }`,
    });

    const finalPath = new URL(page.url()).pathname + new URL(page.url()).search;
    record.finalPath = finalPath;
    record.redirected = !finalPath.startsWith(route.resolvedPath);
    // The honest answer to "is this the page I asked for?". A route marked
    // auth:true that landed on /login was NOT reviewed, and the sheet says so
    // instead of showing a login screenshot under the dashboard's name.
    record.authed = route.auth ? !/^\/login/.test(finalPath) : null;

    await page.screenshot({ path: path.join(OUT, file), fullPage: true });

    // A SECOND shot at exactly one viewport — the first impression, and the only
    // honest way to read sticky/fixed chrome. A fullPage capture flattens a
    // fixed element into the scroll position it happened to occupy, which makes
    // a correctly-pinned banner look like it is stranded mid-document. Judge
    // placement from this one; judge flow and rhythm from the full-page one.
    record.foldFile = `${route.name}.${viewport.name}.${theme}.fold.png`;
    await page.screenshot({ path: path.join(OUT, record.foldFile), fullPage: false });
  } catch (e) {
    record.error = String(e?.message || e).slice(0, 400);
    // Still capture whatever is on screen — a broken page's pixels are the most
    // useful thing in the whole run.
    try {
      await page.screenshot({ path: path.join(OUT, file), fullPage: false });
    } catch {
      record.file = null;
    }
  }

  await context.close();
  return record;
}

// Noise that is CORRECT behaviour, documented in .claude/skills/verify-job360.
// Filtering it here keeps the report's error count meaningful; anything not on
// this list is reported verbatim.
function isBenign(text) {
  return (
    /hydration/i.test(text) ||
    /Warning: Extra attributes from the server/i.test(text) ||
    /Download the React DevTools/i.test(text)
  );
}

function isBenignResponse(res) {
  const url = res.url();
  // 401 on /api/auth/me when logged out is the auth check working.
  if (res.status() === 401 && /\/api\/auth\/me/.test(url)) return true;
  // 404 on /api/profile for a user with no profile yet means "not filled in".
  if (res.status() === 404 && /\/api\/profile/.test(url)) return true;
  return false;
}

// ── contact sheet ────────────────────────────────────────────────────────────
// One scrollable page showing every shot side by side. Reviewing 22 routes by
// opening 88 PNGs one at a time is how inconsistency survives: spacing and
// colour drift are only visible when the pages sit next to each other.

function contactSheet(report) {
  const esc = (s) =>
    String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  const byRoute = new Map();
  for (const shot of report.shots) {
    if (!byRoute.has(shot.route)) byRoute.set(shot.route, []);
    byRoute.get(shot.route).push(shot);
  }

  const sections = [...byRoute.entries()]
    .map(([name, shots]) => {
      const skipped = shots.find((s) => s.skipped);
      if (skipped) {
        return `<section><h2>${esc(name)} <span class="skip">skipped — ${esc(skipped.skipped)}</span></h2></section>`;
      }
      const cards = shots
        .map((s) => {
          const problems = [
            s.error ? `<li class="bad">load failed: ${esc(s.error)}</li>` : "",
            s.stillLoading ? `<li class="bad">still showing skeletons after 30s — this shot is a loading state, not the page</li>` : "",
            s.authed === false ? `<li class="bad">NOT the authed page — landed on ${esc(s.finalPath)}</li>` : "",
            s.redirected && s.authed !== false ? `<li class="warn">redirected to ${esc(s.finalPath)}</li>` : "",
            ...(s.consoleErrors || []).map((e) => `<li class="warn">console: ${esc(e)}</li>`),
            ...(s.failedRequests || []).map((e) => `<li class="warn">network: ${esc(e)}</li>`),
          ]
            .filter(Boolean)
            .join("");
          return `<figure>
  <figcaption>${esc(s.viewport)} / ${esc(s.theme)} <span class="path">${esc(s.path)}</span></figcaption>
  ${s.foldFile ? `<a href="${esc(s.foldFile)}" target="_blank"><img class="fold" src="${esc(s.foldFile)}" loading="lazy" alt="${esc(name)} ${esc(s.viewport)} above the fold"></a><div class="lbl">above the fold</div>` : ""}
  ${s.file ? `<a href="${esc(s.file)}" target="_blank"><img src="${esc(s.file)}" loading="lazy" alt="${esc(name)} ${esc(s.viewport)} ${esc(s.theme)}"></a><div class="lbl">full page</div>` : `<div class="noshot">no image captured</div>`}
  ${problems ? `<ul class="problems">${problems}</ul>` : ""}
</figure>`;
        })
        .join("\n");
      return `<section><h2>${esc(name)}</h2><div class="row">${cards}</div></section>`;
    })
    .join("\n");

  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Job360 design pass</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; padding: 24px; background: #0b0d10; color: #e6e8eb;
         font: 14px/1.5 ui-sans-serif, system-ui, sans-serif; }
  h1 { font-size: 20px; margin: 0 0 4px; }
  .meta { color: #8b949e; margin-bottom: 28px; }
  section { border-top: 1px solid #21262d; padding: 20px 0; }
  h2 { font-size: 15px; margin: 0 0 12px; font-family: ui-monospace, monospace; }
  .skip { color: #8b949e; font-weight: 400; font-family: inherit; }
  .row { display: flex; gap: 20px; overflow-x: auto; padding-bottom: 8px; align-items: flex-start; }
  figure { margin: 0; flex: 0 0 auto; width: 380px; }
  figcaption { color: #8b949e; font-size: 12px; margin-bottom: 6px; display: flex;
               justify-content: space-between; gap: 8px; }
  .path { font-family: ui-monospace, monospace; color: #6e7681; }
  img { width: 100%; border: 1px solid #21262d; border-radius: 6px; display: block;
        background: #fff; }
  .fold { border-color: #2f81f7; }
  .lbl { color: #6e7681; font-size: 11px; margin: 4px 0 12px; text-transform: uppercase;
         letter-spacing: .06em; }
  .noshot { padding: 40px; text-align: center; border: 1px dashed #30363d; border-radius: 6px;
            color: #6e7681; }
  .problems { list-style: none; padding: 8px 0 0; margin: 0; font-size: 12px; }
  .problems li { padding: 2px 0; font-family: ui-monospace, monospace; word-break: break-all; }
  .bad { color: #ff7b72; }
  .warn { color: #d29922; }
</style></head><body>
<h1>Job360 design pass</h1>
<div class="meta">${esc(report.baseUrl)} &middot; ${report.authenticated ? "authenticated" : "ANONYMOUS — authed routes show the logged-out view"} &middot; ${esc(report.viewports.join(", "))} &middot; ${esc(report.themes.join(", "))}</div>
${sections}
</body></html>`;
}
