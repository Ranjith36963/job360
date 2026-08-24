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
 * and assert the audit notices. Run it after touching layout-audit.mjs.
 *
 *   DESIGN_SESSION=<cookie> node tests/design/self-check.mjs
 */

import { chromium } from "@playwright/test";
import { installMocks } from "./mock-data.mjs";

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

const measure = () =>
  page.evaluate(() => {
    const cards = [...document.querySelectorAll('div[role="link"][aria-label^="Job:"]')];
    let clipped = 0;
    for (const c of cards) {
      const cr = c.getBoundingClientRect();
      for (const el of c.querySelectorAll("*")) {
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.right - cr.right > 3) clipped++;
      }
    }
    const rows = {};
    for (const c of cards) {
      const r = c.getBoundingClientRect();
      const k = Math.round((r.top + window.scrollY) / 8) * 8;
      (rows[k] ||= []).push(Math.round(r.height));
    }
    const ragged = Object.values(rows).filter((h) => new Set(h).size > 1).length;
    return { cards: cards.length, clipped, ragged };
  });

const healthy = await measure();

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
const broken = await measure();

await browser.close();

const results = [
  {
    name: "CLIPPED_BY_CONTAINER notices controls clipped out of a card",
    ok: healthy.clipped === 0 && broken.clipped > 0,
    detail: `healthy=${healthy.clipped} broken=${broken.clipped}`,
  },
  {
    name: "ragged card heights reappear without h-full",
    ok: healthy.ragged === 0 && broken.ragged > 0,
    detail: `healthy=${healthy.ragged} broken=${broken.ragged}`,
  },
];

let failed = 0;
for (const r of results) {
  console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}  (${r.detail})`);
  if (!r.ok) failed++;
}
console.log(`\n${results.length - failed}/${results.length} self-checks passed (${healthy.cards} cards)`);
process.exit(failed ? 1 : 0);
