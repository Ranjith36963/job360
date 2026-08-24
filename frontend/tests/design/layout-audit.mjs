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
import { writeFile } from "node:fs/promises";
import { ROUTES } from "./routes.mjs";
import { installMocks } from "./mock-data.mjs";

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
// Everything below runs INSIDE the page.
// ─────────────────────────────────────────────────────────────────────────────

function audit() {
  const out = [];
  const MIN_TAP = 44; // WCAG 2.2 target size (minimum) / Apple HIG
  const OVERLAP_TOLERANCE = 3; // sub-pixel rounding and 1px borders are not bugs

  const describe = (el) => {
    const id = el.id ? `#${el.id}` : "";
    const cls = String(el.className || "")
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 3)
      .join(".");
    const text = (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 40);
    return `${el.tagName.toLowerCase()}${id}${cls ? "." + cls : ""}${text ? ` "${text}"` : ""}`;
  };

  const isVisible = (el, cs, r) =>
    r.width > 0 &&
    r.height > 0 &&
    cs.visibility !== "hidden" &&
    cs.display !== "none" &&
    Number(cs.opacity) > 0.05;

  const all = [...document.querySelectorAll("body *")];
  const meta = new Map();
  for (const el of all) {
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    meta.set(el, { cs, r, visible: isVisible(el, cs, r) });
  }

  // ── OVERFLOW ──────────────────────────────────────────────────────────────
  // A page that scrolls sideways on a phone is always a bug. Name the widest
  // offenders so the cause is actionable, not just "something is too wide".
  const docW = document.documentElement.clientWidth;
  if (document.documentElement.scrollWidth > docW + 1) {
    out.push({
      type: "OVERFLOW",
      detail: `page scrolls horizontally: content ${document.documentElement.scrollWidth}px vs viewport ${docW}px`,
    });
    // Only elements that can actually CAUSE the scrollbar. A decorative glow
    // deliberately hung off the edge inside an overflow-hidden parent is
    // clipped, contributes nothing, and reporting it buries the one element
    // that is genuinely too wide. Same for the internals of an <svg>, which
    // inherit their parent's clipping.
    const isClippedByAncestor = (el) => {
      let cur = el.parentElement;
      while (cur && cur !== document.body) {
        const cs = getComputedStyle(cur);
        if (/hidden|clip|auto|scroll/.test(cs.overflowX)) return true;
        cur = cur.parentElement;
      }
      return false;
    };
    for (const el of all) {
      const { r, visible } = meta.get(el);
      if (!visible) continue;
      if (el.ownerSVGElement) continue; // report the <svg>, not every <path>
      if (isClippedByAncestor(el)) continue;
      if (r.right > docW + 1 && r.width <= docW * 1.5 && r.width > 20) {
        out.push({
          type: "OVERFLOW",
          element: describe(el),
          detail: `extends ${Math.round(r.right - docW)}px past the right edge`,
        });
      }
    }
  }

  // ── Candidates for overlap / tap / clip ───────────────────────────────────
  // Only elements that carry their OWN text, plus form controls. Comparing
  // every div to every div reports thousands of legitimate nestings and
  // decorative layers; what a user actually notices is two pieces of readable
  // content colliding.
  const INTERACTIVE = "a,button,input,select,textarea,[role=button],[role=link],[role=checkbox]";
  const ownsText = (el) =>
    [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1);

  // Things that are on screen but are NOT the product, and would otherwise
  // dominate the report: Next's dev overlay and the TanStack Query devtools
  // launcher (.tsqd-*), neither of which ships; and .sr-only text, which is
  // deliberately clipped to a 1px box for screen readers and so trips the
  // CLIPPED check every time.
  const isDevChrome = (el) =>
    el.closest("nextjs-portal, [data-nextjs-toast], #__next-build-watcher") !== null ||
    /(^|\s|\.)tsqd-/.test(String(el.className || "")) ||
    el.closest('[class*="tsqd-"]') !== null;
  const isScreenReaderOnly = (el) => {
    const { cs, r } = meta.get(el);
    return el.classList.contains("sr-only") || cs.clip === "rect(0px, 0px, 0px, 0px)" || r.width <= 1 || r.height <= 1;
  };

  const content = all.filter((el) => {
    const { cs, visible } = meta.get(el);
    if (!visible) return false;
    if (cs.pointerEvents === "none") return false; // decoration, not content
    if (el.closest("[aria-hidden=true]")) return false;
    if (isDevChrome(el) || isScreenReaderOnly(el)) return false;
    return ownsText(el) || el.matches(INTERACTIVE);
  });

  // ── COVERED ───────────────────────────────────────────────────────────────
  // The consent banner is position:fixed, so it sits on top of whatever is
  // beneath it. Anything INTERACTIVE underneath is unreachable until the
  // banner is dismissed - which is exactly how the landing page's only call to
  // action became untappable on a phone.
  const fixedBars = all.filter((el) => {
    const { cs, r, visible } = meta.get(el);
    return (
      visible &&
      cs.position === "fixed" &&
      cs.pointerEvents !== "none" &&
      r.height > 40 &&
      !isDevChrome(el)
    );
  });
  //
  // Measured as UNREACHABLE, not merely "under the bar right now". A bottom-
  // fixed bar always covers whatever occupies the bottom strip at the current
  // scroll position, and scrolling frees it — reporting those would flag a
  // handful of innocent buttons on every page and drown the real case. The
  // real case is a control in the last bar-height of the DOCUMENT, which no
  // amount of scrolling can lift clear.
  const docH = document.documentElement.scrollHeight;
  const scrollY = window.scrollY;
  for (const bar of fixedBars) {
    const br = meta.get(bar).r;
    for (const el of content) {
      if (bar.contains(el) || el.contains(bar)) continue;
      if (!el.matches(INTERACTIVE)) continue;
      const r = meta.get(el).r;
      const ox = Math.min(r.right, br.right) - Math.max(r.left, br.left);
      const oy = Math.min(r.bottom, br.bottom) - Math.max(r.top, br.top);
      if (ox <= OVERLAP_TOLERANCE || oy <= OVERLAP_TOLERANCE) continue;
      // Absolute document position of the element's bottom edge.
      const docBottom = r.bottom + scrollY;
      const reachable = docBottom < docH - br.height - OVERLAP_TOLERANCE;
      if (reachable) continue;
      out.push({
        type: "COVERED",
        element: describe(el),
        detail: `permanently under the fixed ${describe(bar).slice(0, 30)} — cannot be scrolled clear`,
      });
    }
  }

  // ── OVERLAP ───────────────────────────────────────────────────────────────
  // Text colliding with text. Ancestors, descendants and fixed bars excluded
  // (the last are reported as COVERED above).
  //
  // Two exclusions keep this honest:
  //  - anything INSIDE a fixed bar. Those elements are not themselves
  //    position:fixed, so they leaked in and re-reported every COVERED case as
  //    a second, confusing OVERLAP against the consent banner's own paragraph.
  //  - inline elements. getBoundingClientRect on an inline span that wraps
  //    across lines returns the UNION of its line boxes, a rectangle covering
  //    text it does not actually touch. That is how "EXPERIENCE Data Engineer"
  //    appeared to collide with "EDUCATION BSc" inside one flowing paragraph.
  const insideFixed = (el) => fixedBars.some((bar) => bar.contains(el));
  const flow = content.filter((el) => {
    const { cs } = meta.get(el);
    if (cs.position === "fixed" || insideFixed(el)) return false;
    if (cs.display === "inline" && el.getClientRects().length > 1) return false;
    return true;
  });
  for (let i = 0; i < flow.length; i++) {
    for (let j = i + 1; j < flow.length; j++) {
      const a = flow[i];
      const b = flow[j];
      if (a.contains(b) || b.contains(a)) continue;
      const ra = meta.get(a).r;
      const rb = meta.get(b).r;
      const ox = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
      const oy = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
      if (ox > OVERLAP_TOLERANCE && oy > OVERLAP_TOLERANCE) {
        out.push({
          type: "OVERLAP",
          element: describe(a),
          other: describe(b),
          detail: `boxes intersect by ${Math.round(ox)}x${Math.round(oy)}px`,
        });
      }
    }
  }

  // ── TAP_TARGET ────────────────────────────────────────────────────────────
  // Only meaningful on a touch-sized viewport; a mouse can hit a 16px icon.
  if (window.innerWidth <= 768) {
    for (const el of content) {
      if (!el.matches(INTERACTIVE)) continue;
      if (isDevChrome(el)) continue;
      const r = meta.get(el).r;
      // An inline link inside a paragraph is not a "target" in this sense.
      const inProse = el.tagName === "A" && meta.get(el).cs.display === "inline";
      if (inProse) continue;
      // Checkboxes and radios are judged against WCAG 2.5.8 (24px), not 2.5.5
      // (44px). Their label is the large target; the box itself only has to be
      // hittable. Holding them to 44px reported a correctly-sized 24px control
      // as a defect.
      const isSmallControl = el.matches('input[type="checkbox"], input[type="radio"]');
      const min = isSmallControl ? 24 : MIN_TAP;
      if (r.width < min || r.height < min) {
        out.push({
          type: "TAP_TARGET",
          element: describe(el),
          detail: `${Math.round(r.width)}x${Math.round(r.height)}px, below the ${min}px minimum`,
        });
      }
    }
  }

  // ── CLIPPED ───────────────────────────────────────────────────────────────
  // Text its own container cuts off, with no ellipsis to admit it.
  for (const el of content) {
    const { cs } = meta.get(el);
    if (cs.overflow === "visible" && cs.overflowX === "visible") continue;
    if (cs.textOverflow === "ellipsis") continue; // deliberate truncation
    if (el.scrollWidth > el.clientWidth + 2 && el.clientWidth > 0) {
      out.push({
        type: "CLIPPED",
        element: describe(el),
        detail: `text is ${el.scrollWidth}px inside a ${el.clientWidth}px box`,
      });
    }
  }

  // ── CLIPPED_BY_CONTAINER ──────────────────────────────────────────────────
  // Content cut off by an ANCESTOR's overflow:hidden, not by its own box.
  //
  // This class of defect walked straight past the first version of this audit,
  // which reported /dashboard as clean while a "Details" button and the source
  // label were both being sliced off every job card. The CLIPPED check above
  // only looks at an element against its OWN width; here the element is a
  // perfectly happy size and it is the card around it that hides it. Because
  // the card is overflow:hidden rather than auto, the content cannot even be
  // scrolled to — it is simply gone, with no visual hint that it exists.
  for (const el of content) {
    let cur = el.parentElement;
    while (cur && cur !== document.body) {
      const ccs = getComputedStyle(cur);
      if (ccs.overflow === "hidden" || ccs.overflowX === "hidden") {
        const cr = cur.getBoundingClientRect();
        const r = meta.get(el).r;
        const overRight = r.right - cr.right;
        const overBottom = r.bottom - cr.bottom;
        if (overRight > OVERLAP_TOLERANCE || overBottom > OVERLAP_TOLERANCE) {
          out.push({
            type: "CLIPPED_BY_CONTAINER",
            element: describe(el),
            other: describe(cur).slice(0, 45),
            detail: `cut off by ${Math.round(Math.max(overRight, overBottom))}px — the container is overflow:hidden, so it cannot be scrolled to`,
          });
        }
        break; // the nearest clipping ancestor is the one that matters
      }
      cur = cur.parentElement;
    }
  }

  // ── CONTRAST ──────────────────────────────────────────────────────────────
  // Walks up for the first opaque background rather than assuming white, so a
  // dark theme is measured honestly. WCAG AA is 4.5:1 for body text.
  // Resolve ANY css colour to sRGB bytes by painting it.
  //
  // The naive version - match the digits in the string and call them r,g,b -
  // is wrong in this codebase and silently so. The palette is authored in
  // oklch, and Chrome hands those back from getComputedStyle as `lab(...)` /
  // `oklab(...)`. Reading `lab(0.317 -0.156 0.118)` as rgb gives near-black for
  // a mid-tone, and near-black for white text too, so every ratio collapses.
  // The first run of this audit reported 189 contrast failures whose ratios
  // took only three distinct values - including white-on-black nav text at
  // "1.48:1", which is really about 19:1. All 189 were fake.
  //
  // A 1x1 canvas does the colour-space conversion for us, whatever the input
  // notation, so this stays correct for lab/oklch/color-mix/named colours.
  const cvs = document.createElement("canvas");
  cvs.width = cvs.height = 1;
  const cctx = cvs.getContext("2d", { willReadFrequently: true });
  const parse = (c) => {
    if (!c || c === "transparent") return { r: 0, g: 0, b: 0, a: 0 };
    cctx.clearRect(0, 0, 1, 1);
    cctx.fillStyle = "#000";
    cctx.fillStyle = c; // invalid values leave the previous fillStyle in place
    cctx.clearRect(0, 0, 1, 1);
    cctx.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = cctx.getImageData(0, 0, 1, 1).data;
    return { r, g, b, a: a / 255 };
  };
  const lum = ({ r, g, b }) => {
    const f = (v) => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const bgOf = (el) => {
    let cur = el;
    while (cur && cur !== document.documentElement) {
      const c = parse(getComputedStyle(cur).backgroundColor);
      if (c && c.a > 0.9) return c;
      cur = cur.parentElement;
    }
    return parse(getComputedStyle(document.body).backgroundColor) || { r: 0, g: 0, b: 0, a: 1 };
  };
  for (const el of content) {
    if (!ownsText(el)) continue;
    const { cs, r } = meta.get(el);
    const size = parseFloat(cs.fontSize);
    if (r.height < 6) continue;
    const fg = parse(cs.color);
    if (!fg || fg.a < 0.95) continue;
    const bg = bgOf(el);
    const L1 = lum(fg);
    const L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
    // 3:1 is the AA bar for large text (>=18.66px bold or >=24px).
    const large = size >= 24 || (size >= 18.66 && Number(cs.fontWeight) >= 700);
    const need = large ? 3 : 4.5;
    if (ratio < need) {
      out.push({
        type: "CONTRAST",
        element: describe(el),
        detail: `${ratio.toFixed(2)}:1 at ${Math.round(size)}px, needs ${need}:1`,
      });
    }
  }

  // Collapse duplicates - the same defect on 40 identical chips is one problem.
  const seen = new Set();
  return out.filter((f) => {
    const k = `${f.type}|${f.element || ""}|${f.other || ""}|${f.detail}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
