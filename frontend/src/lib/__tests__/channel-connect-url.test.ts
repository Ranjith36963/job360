import { describe, expect, it } from "vitest";

import { channelConnectUrl } from "../api";

// Regression guard for the bug found during live verification: the Connect
// Slack/Discord buttons used a RELATIVE "/api/..." path in window.location.href,
// which hit the frontend origin (:3000) and 404'd instead of the backend (:8000).
// channelConnectUrl must always return an ABSOLUTE backend URL.
describe("channelConnectUrl", () => {
  it("returns an absolute backend URL for slack (never a relative /api path)", () => {
    const url = channelConnectUrl("slack");
    expect(url).toMatch(/^https?:\/\//); // absolute (scheme + host)
    expect(url.startsWith("/api/")).toBe(false); // NOT relative
    expect(url).toContain("/api/settings/channels/connect/slack");
  });

  it("returns an absolute backend URL for discord", () => {
    const url = channelConnectUrl("discord");
    expect(url).toMatch(/^https?:\/\//);
    expect(url).toContain("/api/settings/channels/connect/discord");
  });
});
