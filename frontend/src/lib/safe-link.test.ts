import { describe, expect, it } from "vitest";
import { safeHref } from "./safe-link";

describe("safeHref", () => {
  it.each([
    ["https://linkedin.com/in/ranjith-ai-engineer", "https://linkedin.com/in/ranjith-ai-engineer"],
    ["http://example.com/portfolio", "http://example.com/portfolio"],
    ["linkedin.com/in/ranjith-ai-engineer", "https://linkedin.com/in/ranjith-ai-engineer"],
    ["github.com/Ranjith36963", "https://github.com/Ranjith36963"],
    ["  https://ranjith36963.github.io  ", "https://ranjith36963.github.io/"],
    ["www.example.com", "https://www.example.com/"],
  ])("links %s", (value, href) => {
    expect(safeHref(value)).toBe(href);
  });

  it.each([
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "mailto:sam@example.com",
    "sam@example.com",
    "/applications/7",
    "//evil.example.com",
    "https://user:pass@example.com",
    "https://localhost/x",
    "Yes",
    "3.5",
    "e.g.",
    "Next.js",
    "Node.js",
    "resume.pdf",
    "github.com",
    "See linkedin.com/in/x for more",
    "",
    null,
  ])("leaves %s as plain text", (value) => {
    expect(safeHref(value)).toBeNull();
  });
});

import { linkedinProfileHref } from "./safe-link";
describe("linkedinProfileHref", () => {
  it("accepts only linkedin.com over http(s)", () => {
    expect(linkedinProfileHref("https://www.linkedin.com/in/a")).toBe("https://www.linkedin.com/in/a");
    expect(linkedinProfileHref("linkedin.com/in/a")).toBe("https://linkedin.com/in/a");
    expect(linkedinProfileHref("https://evil.example/linkedin.com/")).toBeNull();
    expect(linkedinProfileHref("https://notlinkedin.com/in/a")).toBeNull();
    expect(linkedinProfileHref("javascript:alert(1)")).toBeNull();
    expect(linkedinProfileHref("")).toBeNull();
  });
});
