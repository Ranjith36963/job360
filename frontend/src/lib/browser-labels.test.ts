import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  BROWSER_LABELS, JOB360_BROWSER_LABEL, LINKEDIN_OWN_BROWSER_LABEL, LINKEDIN_OWN_BROWSER_REASON, OWN_BROWSER_LABEL,
  PACE_TEXT, RUN_NOW_DONE, RUN_OWN_BROWSER_TASKS,
} from "./browser-labels";

const root = process.cwd(); // vitest runs from frontend/

describe("browser-labels", () => {
  it("equals the server's routing.LABELS, so a chip and a heading cannot drift", () => {
    const py = readFileSync(join(root, "../backend/src/services/browser_agent/routing.py"), "utf-8");
    const block = /LABELS: dict\[str, str\] = \{([\s\S]*?)\}/.exec(py)?.[1] ?? "";
    const server = Object.fromEntries([...block.matchAll(/"(\w+)":\s*"([^"]*)"/g)].map((m) => [m[1], m[2]]));
    expect(server).toEqual(BROWSER_LABELS);
    expect(OWN_BROWSER_LABEL).toBe("Needs your own browser: turn your PC on");
  });

  it("the LinkedIn chip equals the server's routing.LINKEDIN_LABEL and does not read as broken", () => {
    const py = readFileSync(join(root, "../backend/src/services/browser_agent/routing.py"), "utf-8");
    expect(/^LINKEDIN_LABEL = "([^"]*)"$/m.exec(py)?.[1]).toBe(LINKEDIN_OWN_BROWSER_LABEL);
    expect(py).toContain(`"${LINKEDIN_OWN_BROWSER_REASON}"`);
    expect(LINKEDIN_OWN_BROWSER_LABEL).not.toMatch(/turn your PC on|chrome|claude/i);
  });

  it("names no assistant or browser product", () => {
    for (const t of [...Object.values(BROWSER_LABELS), JOB360_BROWSER_LABEL, PACE_TEXT, RUN_NOW_DONE, RUN_OWN_BROWSER_TASKS]) {
      expect(t).not.toMatch(/chrome|claude/i);
    }
  });
});
