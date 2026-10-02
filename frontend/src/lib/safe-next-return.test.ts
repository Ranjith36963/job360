import { describe, it, expect, beforeEach } from "vitest";
import { isConsentPath, resolvePostLoginPath } from "./safe-next";

function setCookie(v: string) {
  document.cookie = `j360_next=${encodeURIComponent(v)}; Path=/`;
}
const cookieLeft = () => document.cookie.includes("j360_next=");

beforeEach(() => {
  document.cookie = "j360_next=; Path=/; Max-Age=0";
});

describe("resolvePostLoginPath()", () => {
  it("uses the consent cookie when next is absent, and clears it", () => {
    setCookie("/oauth/consent/abc");
    expect(resolvePostLoginPath(null)).toBe("/oauth/consent/abc");
    expect(cookieLeft()).toBe(false);
  });

  it.each(["//evil.com", "/applications", "https://evil.com/oauth/consent/x", "/\\evil.com", "/oauth/consent/"])(
    "ignores unsafe/out-of-scope cookie %j",
    (v) => {
      setCookie(v);
      expect(resolvePostLoginPath(null)).toBe("/applications");
      expect(cookieLeft()).toBe(false);
    }
  );

  it("an explicit safe next beats the cookie (cookie still cleared)", () => {
    setCookie("/oauth/consent/abc");
    expect(resolvePostLoginPath("/profile")).toBe("/profile");
    expect(cookieLeft()).toBe(false);
  });

  it("an unsafe next falls back to the cookie", () => {
    setCookie("/oauth/consent/abc");
    expect(resolvePostLoginPath("//evil.com")).toBe("/oauth/consent/abc");
  });

  it("defaults to /applications with no cookie", () => {
    expect(resolvePostLoginPath(undefined)).toBe("/applications");
  });
});

describe("isConsentPath()", () => {
  it("accepts only /oauth/consent/<id>", () => {
    expect(isConsentPath("/oauth/consent/abc")).toBe(true);
    expect(isConsentPath("/oauth/consent/")).toBe(false);
    expect(isConsentPath("/oauth/other")).toBe(false);
    expect(isConsentPath("//oauth/consent/a")).toBe(false);
  });
});
