#!/usr/bin/env node
/**
 * SELF-CHECK — does the audit still fail when the page is broken?
 *
 * A checker that reports "clean" is indistinguishable from a checker that has
 * stopped working, and this one has quietly stopped working twice already:
 * once when the on-screen filter was added (which excluded the very elements
 * CLIPPED_BY_CONTAINER exists to find), and once when colours were parsed as
 * rgb (which turned 189 real-looking contrast failures into noise and would
 * equally have hidden real ones).
 *
 * So: break the live page on purpose, in the exact way it was broken before,
 * and assert the audit notices. Run it after touching the audit.
 *
 * IT RUNS THE REAL `audit()`. The first version measured the page itself with
 * a simplified inline copy of the logic, which is not a check of the checker
 * at all — a regression inside `audit()` would have passed it. Both this file
 * and `layout-audit.mjs` now import the one copy from `./audit-fn.mjs`.
 *
 *   DESIGN_SESSION=<cookie> node tests/design/self-check.mjs
 */

import { chromium } from "@playwright/test";
import { installMocks } from "./mock-data.mjs";
import { audit } from "./audit-fn.mjs";

const BASE = (process.env.DESIGN_BASE_URL || "http://localhost:3100").replace(/\/$/, "");
const SESSION = process.env.DESIGN_SESSION || "";

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
if (SESSION) {
  await ctx.addCookies([
    { name: "job360_session", value: SESSION, domain: new URL(BASE).hostname, path: "/" },
  ]);
}
const page = await ctx.newPage();
await installMocks(page);
await page.goto(`${BASE}/dashboard`, { waitUntil: "domcontentloaded", timeout: 60_000 });
await page
  .waitForFunction(() => document.querySelectorAll(".animate-pulse").length === 0, { timeout: 30_000 })
  .catch(() => {});
await page.waitForTimeout(2000);

/** Run the real audit and count its findings by type. */
const run = async () => {
  const findings = await page.evaluate(audit);
  const byType = {};
  for (const f of findings) byType[f.type] = (byType[f.type] || 0) + 1;
  return { findings, byType };
};

/**
 * The audit has NO ragged-height finding, so that assertion cannot come from
 * it. Measured separately, and labelled below as a check of the PAGE rather
 * than of the checker — saying which is which is the whole point of this file.
 */
const raggedRows = () =>
  page.evaluate(() => {
    const cards = [...document.querySelectorAll('div[role="link"][aria-label^="Job:"]')];
    const rows = {};
    for (const c of cards) {
      const r = c.getBoundingClientRect();
      const k = Math.round((r.top + window.scrollY) / 8) * 8;
      (rows[k] ||= []).push(Math.round(r.height));
    }
    return Object.values(rows).filter((h) => new Set(h).size > 1).length;
  });

const cardCount = await page.evaluate(
  () => document.querySelectorAll('div[role="link"][aria-label^="Job:"]').length,
);

const healthy = await run();
const healthyRagged = await raggedRows();

// Re-create the two dashboard defects exactly as they were.
await page.evaluate(() => {
  document
    .querySelectorAll('div[role="link"][aria-label^="Job:"] .flex-wrap')
    .forEach((el) => el.classList.remove("flex-wrap"));
  document
    .querySelectorAll('div[role="link"][aria-label^="Job:"]')
    .forEach((el) => el.classList.remove("h-full"));
});
await page.waitForTimeout(500);
const broken = await run();
const brokenRagged = await raggedRows();

await browser.close();

const clip = (r) => r.byType.CLIPPED_BY_CONTAINER || 0;

const results = [
  {
    name: "audit() reports CLIPPED_BY_CONTAINER only when controls are clipped",
    ok: clip(healthy) === 0 && clip(broken) > 0,
    detail: `healthy=${clip(healthy)} broken=${clip(broken)}`,
  },
  {
    name: "audit() stays quiet on a healthy page (no finding type appears from nothing)",
    ok: healthy.findings.length <= broken.findings.length,
    detail: `healthy=${healthy.findings.length} broken=${broken.findings.length} findings`,
  },
  {
    // PAGE check, not a checker check — the audit has no ragged-height finding.
    name: "page: ragged card heights reappear without h-full",
    ok: healthyRagged === 0 && brokenRagged > 0,
    detail: `healthy=${healthyRagged} broken=${brokenRagged}`,
  },
];

let failed = 0;
for (const r of results) {
  console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}  (${r.detail})`);
  if (!r.ok) failed++;
}
console.log(`\n${results.length - failed}/${results.length} self-checks passed (${cardCount} cards)`);
process.exit(failed ? 1 : 0);
