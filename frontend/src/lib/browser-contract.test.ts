import { beforeEach, describe, expect, it, vi } from "vitest";

const requestJson = vi.fn();
vi.mock("@/lib/api", () => ({ requestJson: (...a: unknown[]) => requestJson(...a) }));

import {
  askBrowser, browserLabel, effectiveAskBrowser, contactSendBrowser, decideOwnBrowserSend, needsOwnBrowser,
  ownBrowserQueueCount, ownBrowserTask, readyBrowser, runOwnBrowserNow,
} from "./browser-contract";

const lab = (kind: unknown) => ({ kind, label: "L", reason: "R" });

beforeEach(() => requestJson.mockReset().mockResolvedValue({}));

describe("browser labels", () => {
  it.each(["job360", "own_browser", "human"])("parses kind %s on all three objects", (kind) => {
    expect(askBrowser({ browser: lab(kind) } as never)).toEqual(lab(kind));
    expect(readyBrowser({ browser: lab(kind) } as never)).toEqual(lab(kind));
    expect(contactSendBrowser({ send_browser: lab(kind) } as never)).toEqual(lab(kind));
  });

  it("returns null when absent or malformed", () => {
    expect(askBrowser({} as never)).toBeNull();
    expect(browserLabel({ browser: lab("robot") }, "browser")).toBeNull();
    expect(browserLabel({ browser: { kind: "human", label: 5, reason: "" } }, "browser")).toBeNull();
    expect(browserLabel({ browser: "own_browser" }, "browser")).toBeNull();
    expect(browserLabel(null, "browser")).toBeNull();
  });

  it("needsOwnBrowser reads browser.kind", () => {
    expect(needsOwnBrowser({ browser: lab("own_browser") } as never)).toBe(true);
    expect(needsOwnBrowser({ browser: lab("human") } as never)).toBe(false);
    expect(needsOwnBrowser({} as never)).toBe(false);
  });
});

describe("own_browser_task", () => {
  it("parses a good task and rejects a bad state or id", () => {
    const t = { state: "queued", message_id: 9, approved_at: "2026-10-25T10:00:00Z", claim_until: null };
    expect(ownBrowserTask({ own_browser_task: t } as never)).toEqual(t);
    expect(ownBrowserTask({ own_browser_task: { ...t, state: "x" } } as never)).toBeNull();
    expect(ownBrowserTask({ own_browser_task: { ...t, message_id: "9" } } as never)).toBeNull();
    expect(ownBrowserTask({} as never)).toBeNull();
  });
});

describe("calls", () => {
  it("posts approve and decline bodies to the contact path", async () => {
    await decideOwnBrowserSend(4, 77, "approve");
    expect(requestJson).toHaveBeenLastCalledWith("/api/contacts/4/own-browser-send", "POST", { message_id: 77, decision: "approve" });
    await decideOwnBrowserSend(4, 77, "decline");
    expect(requestJson).toHaveBeenLastCalledWith("/api/contacts/4/own-browser-send", "POST", { message_id: 77, decision: "decline" });
  });

  it("posts run-now with no body", async () => {
    await runOwnBrowserNow();
    expect(requestJson).toHaveBeenCalledWith("/api/own-browser/run-now", "POST");
  });

  it.each([
    [{ items: [1, 2] }, 2], [{ count: 4 }, 4], [[1, 2, 3], 3], [{ nope: 1 }, null], ["x", null],
  ])("reads the queue shape %j as %s", async (shape, expected) => {
    requestJson.mockResolvedValue(shape);
    expect(await ownBrowserQueueCount()).toBe(expected);
    expect(requestJson).toHaveBeenCalledWith("/api/own-browser/queue", "GET");
  });

  it("returns null when the queue fails", async () => {
    requestJson.mockImplementation(async (path: string) => { if (path) throw new Error("boom"); return {}; });
    expect(await ownBrowserQueueCount()).toBeNull();
  });
});

describe("effectiveAskBrowser", () => {
  const plain = { question: "q" } as unknown as import("@/lib/api").Ask;
  const labelled = { browser: { kind: "own_browser", label: "Your Chrome", reason: "r" } } as unknown as import("@/lib/api").Ask;

  it.each(["captcha", "login", "upload", "safety"])("defaults an unlabelled %s to You (human)", (kind) => {
    expect(effectiveAskBrowser(plain, kind)).toEqual({ kind: "human", label: "You", reason: "" });
  });

  it("lets a backend label win", () => {
    expect(effectiveAskBrowser(labelled, "captcha")?.kind).toBe("own_browser");
  });

  it("ignores a malformed label and keeps the default", () => {
    const bad = { browser: { kind: "mystery", label: "X" } } as unknown as import("@/lib/api").Ask;
    expect(effectiveAskBrowser(bad, "login")?.label).toBe("You");
  });

  it("gives no default to other kinds", () => {
    for (const k of ["question", "yes_no", "structured", "outreach"]) expect(effectiveAskBrowser(plain, k)).toBeNull();
  });
});
