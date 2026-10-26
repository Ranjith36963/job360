import { describe, it, expect } from "vitest";
import { scrubSentryEvent } from "../sentryScrub";

describe("scrubSentryEvent", () => {
  it("removes tokens from request, breadcrumbs, transaction name and spans", () => {
    const event = scrubSentryEvent({
      transaction: "/auth/magic?token=abc",
      request: {
        url: "https://job360.uk/auth/magic?token=abc",
        query_string: "token=abc",
        headers: { Referer: "https://job360.uk/reset-password?token=abc", Accept: "*/*" },
      },
      breadcrumbs: [
        { category: "navigation", data: { from: "/a?token=abc", to: "/undo-email-change?token=abc" } },
        { category: "fetch", data: { url: "/api/files/fake-tok?x=1", method: "GET" } },
      ],
      spans: [{ description: "GET /api/proof/fake-tok", data: { "http.url": "/x?token=abc" } }],
    });
    const dump = JSON.stringify(event);
    expect(dump).not.toContain("abc");
    expect(dump).not.toContain("fake-tok");
    expect(event.request?.url).toBe("https://job360.uk/auth/magic");
    expect(event.request?.query_string).toBeUndefined();
    expect(event.request?.headers?.Accept).toBe("*/*");
    expect(event.breadcrumbs?.[1].data?.method).toBe("GET");
  });

  it("passes events without these fields through", () => {
    expect(scrubSentryEvent({ message: "x" })).toEqual({ message: "x" });
  });
});
