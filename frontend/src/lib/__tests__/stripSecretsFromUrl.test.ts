import { describe, it, expect } from "vitest";
import { stripSecretsFromUrl } from "../stripSecretsFromUrl";

describe("stripSecretsFromUrl", () => {
  it("drops a token in the query string", () => {
    expect(stripSecretsFromUrl("https://job360.uk/auth/magic?token=abc&next=%2Fx")).toBe(
      "https://job360.uk/auth/magic",
    );
  });

  it("drops the hash", () => {
    expect(stripSecretsFromUrl("https://job360.uk/reset-password#token=abc")).toBe(
      "https://job360.uk/reset-password",
    );
  });

  it("redacts the token in file, proof and calendar paths", () => {
    expect(stripSecretsFromUrl("https://job360.uk/api/files/fake-file-token?x=1")).toBe(
      "https://job360.uk/api/files/[redacted]",
    );
    expect(stripSecretsFromUrl("https://job360.uk/api/proof/fake-proof-token")).toBe(
      "https://job360.uk/api/proof/[redacted]",
    );
    expect(stripSecretsFromUrl("https://job360.uk/api/calendar/fake-cal-token.ics")).toBe(
      "https://job360.uk/api/calendar/[redacted]",
    );
  });

  it("keeps ordinary paths, including application proof routes", () => {
    expect(stripSecretsFromUrl("https://job360.uk/api/applications/5/proof/screenshots/3")).toBe(
      "https://job360.uk/api/applications/5/proof/screenshots/3",
    );
  });

  it("handles relative URLs and garbage without throwing", () => {
    expect(stripSecretsFromUrl("/undo-email-change?token=abc")).toBe("/undo-email-change");
    expect(stripSecretsFromUrl("/api/files/tok?x=1#h")).toBe("/api/files/[redacted]");
    expect(stripSecretsFromUrl("not a url ?token=abc")).not.toContain("token=");
  });
});
