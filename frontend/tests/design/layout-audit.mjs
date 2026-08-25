#!/usr/bin/env node
/**
 * LAYOUT AUDIT — turns "it looks cramped" into numbers.
 *
 * design-pass.mjs is a camera: it shows what a page looks like, and judging it
 * is then a matter of opinion and of whatever I happen to notice. This is the
 * opposite tool. It reads the live geometry out of the DOM and reports, with
 * pixel values, the classes of defect that are objectively wrong no matter what
 * anyone's taste is:
 *
 *   OVERLAP        two pieces of content sitting on top of each other
 *   OVERFLOW       anything forcing the page to scroll sideways
 *   COVERED        a control trapped underneath the fixed consent banner
 *   TAP_TARGET     an interactive element too small to hit on a phone
 *   CLIPPED        text cut off by its own container
 *   CONTRAST       body text too faint against what is actually behind it
 *
 * Every finding names the element and the amount, so a fix can be verified by
 * the number going away rather than by squinting at a before/after PNG.
 *
 * It deliberately does NOT judge beauty, hierarchy, or whether a layout is a
 * good idea. Those stay a human call, informed by design-pass.mjs screenshots.
 *
 * Usage:
 *   node tests/design/layout-audit.mjs
 *   DESIGN_SESSION=<cookie> DESIGN_ONLY=profile,dashboard node tests/design/layout-audit.mjs
 *
 * Env mirrors design-pass.mjs so both tools take the same arguments.
 */

import { chromium } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { ROUTES } from "./routes.mjs";
import { installMocks } from "./mock-data.mjs";
import { audit } from "./audit-fn.mjs";

const BASE = (process.env.DESIGN_BASE_URL || "http://localhost:3100").replace(/\/$/, "");
const SESSION = process.env.DESIGN_SESSION || "";
// DESIGN_MOCK=1 serves deterministic fixtures for the authed endpoints.
const MOCK = process.env.DESIGN_MOCK === "1";
const OUT = process.env.DESIGN_AUDIT_OUT || "design-shots-local/layout-audit.json";
const only = (process.env.DESIGN_ONLY || "").split(",").map((s) => s.trim()).filter(Boolean);

// Widths chosen to catch the transitions, not to be exhaustive: the smallest
// phone we care about, the tablet breakpoint, and a laptop.
const WIDTHS = (process.env.DESIGN_WIDTHS || "390,768,1440")
  .split(",")
  .map((w) => Number(w.trim()))
  .filter(Boolean);

// Create the output directory BEFORE the walk, not just before the write.
// design-shots*/ is gitignored, so on a fresh clone or a clean CI checkout it
// does not exist — and writeFile at the very end would then throw ENOENT after
// up to 20 routes x 3 widths of work, discarding every finding. Failing in the
// first second on a bad DESIGN_AUDIT_OUT is the useful behaviour.
await mkdir(path.dirname(path.resolve(OUT)), { recursive: true });

const browser = await chromium.launch();
const findings = [];

for (const route of ROUTES) {
  if (route.skip || route.dynamic) continue;
  if (only.length && !only.includes(route.name)) continue;

  for (const width of WIDTHS) {
    // Emulate a real touch device at phone/tablet widths. Without this the
    // headless browser reports `pointer: fine`, so any fix written as
    // `@media (pointer: coarse)` — which is how touch sizing SHOULD be
    // expressed, since desktop density is deliberate — would never apply here
    // and the audit would keep reporting targets that are actually fixed.
    const touch = width <= 768;
    const ctx = await browser.newContext({
      viewport: { width, height: 900 },
      colorScheme: "dark",
      hasTouch: touch,
      isMobile: touch,
      deviceScaleFactor: touch ? 2 : 1,
    });
    if (SESSION) {
      await ctx.addCookies([
        { name: "job360_session", value: SESSION, domain: new URL(BASE).hostname, path: "/" },
      ]);
    }
    const page = await ctx.newPage();
  if (MOCK) await installMocks(page);

    try {
      await page.goto(`${BASE}${route.path}`, { waitUntil: "domcontentloaded", timeout: 60_000 });
      await page
        .waitForFunction(() => document.querySelectorAll(".animate-pulse").length === 0, {
          timeout: 30_000,
        })
        .catch(() => {});
      await page.waitForTimeout(1500);
      // The dev overlay is not part of the product; it would report as a
      // covering element on every single page.
      await page.addStyleTag({
        content: "nextjs-portal,[data-nextjs-toast]{display:none!important}",
      });

      const result = await page.evaluate(audit);
      const landed = new URL(page.url()).pathname;
      for (const f of result) {
        findings.push({ route: route.name, path: landed, width, ...f });
      }
      console.log(
        `  ${`${route.name} @${width}`.padEnd(30)} ${result.length ? `${result.length} finding(s)` : "clean"}`,
      );
    } catch (e) {
      findings.push({
        route: route.name,
        width,
        type: "LOAD_FAILED",
        detail: String(e?.message || e).slice(0, 200),
      });
      console.log(`  ${`${route.name} @${width}`.padEnd(30)} LOAD FAILED`);
    }

    await ctx.close();
  }
}

await browser.close();

await writeFile(OUT, JSON.stringify({ baseUrl: BASE, widths: WIDTHS, findings }, null, 2));

const byType = {};
for (const f of findings) byType[f.type] = (byType[f.type] || 0) + 1;
console.log(`\n${findings.length} finding(s) -> ${OUT}`);
for (const [t, n] of Object.entries(byType).sort((a, b) => b[1] - a[1])) {
  console.log(`  ${t.padEnd(12)} ${n}`);
}

// ─────────────────────────────────────────────────────────────────────────────
// The audit function itself lives in ./audit-fn.mjs so `self-check.mjs` can run
// the SAME code this script runs. See the header there for why that matters.
// ─────────────────────────────────────────────────────────────────────────────
