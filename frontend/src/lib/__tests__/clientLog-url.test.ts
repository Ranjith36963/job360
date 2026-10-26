import { describe, it, expect, vi, afterEach } from "vitest";
import { reportClientLog } from "../clientLog";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("reportClientLog url", () => {
  it("sends no query string or hash", () => {
    window.history.replaceState(null, "", "/auth/magic?token=abc#frag");
    const fetchMock = vi.fn().mockResolvedValue({});
    vi.stubGlobal("fetch", fetchMock);
    reportClientLog({ message: "boom" });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.url).toContain("/auth/magic");
    expect(body.url).not.toContain("?");
    expect(body.url).not.toContain("token");
    expect(body.url).not.toContain("#");
  });
});
