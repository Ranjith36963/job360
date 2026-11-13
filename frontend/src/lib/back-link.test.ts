import { describe, expect, it } from "vitest";
import { appHref, backTarget, parseJobParam } from "./back-link";

describe("backTarget", () => {
  it("Needs you with a chip: names the chip and returns to it with the job", () => {
    const t = backTarget(90, { from: "needs-you", show: "signin_captcha" });
    expect(t.label).toBe("Back to Needs you · Sign in / CAPTCHA");
    expect(t.href).toBe("/needs-you?show=signin_captcha&job=90");
    expect(t.trail.map((x) => x.name)).toEqual(["Needs you", "Sign in / CAPTCHA"]);
  });
  it("Needs you, All or no chip: just 'Back to Needs you'", () => {
    for (const show of ["all", undefined]) {
      const t = backTarget(7, { from: "needs-you", show });
      expect(t.label).toBe("Back to Needs you");
      expect(t.href).toBe("/needs-you?job=7");
    }
  });
  it("Other is a known chip", () => {
    expect(backTarget(7, { from: "needs-you", show: "other" }).href).toBe("/needs-you?show=other&job=7");
  });
  it("Outreach and the dashboard go home to their page", () => {
    expect(backTarget(7, { from: "outreach" })).toMatchObject({ label: "Back to Outreach", href: "/outreach/people" });
    expect(backTarget(7, { from: "home" })).toMatchObject({ label: "Back to Progress dashboard", href: "/" });
  });
  it("no from, or a bad one, reads All applications", () => {
    for (const from of [undefined, "", "evil", "//evil.example", "javascript:alert(1)", "__proto__", "toString"]) {
      expect(backTarget(7, { from })).toMatchObject({ label: "All applications", href: "/applications" });
    }
  });
  it("a bad show falls back to All and never reaches the link", () => {
    for (const show of ["nope", "a&job=1", "<script>", "../x", ""]) {
      const t = backTarget(7, { from: "needs-you", show });
      expect(t.href).toBe("/needs-you?job=7");
      expect(t.label).toBe("Back to Needs you");
    }
  });
  it("show is only read for Needs you; an array takes its first value", () => {
    expect(backTarget(7, { from: "outreach", show: "signin_captcha" }).href).toBe("/outreach/people");
    expect(backTarget(7, { from: ["needs-you", "x"], show: ["check_send"] }).label).toBe("Back to Needs you · Check & send");
  });
});

describe("parseJobParam / appHref", () => {
  it("accepts only a positive whole number", () => {
    expect(parseJobParam("90")).toBe(90);
    for (const v of ["0", "-1", "1.5", "abc", "", null, undefined, "9999999999"]) expect(parseJobParam(v)).toBeNull();
  });
  it("builds the link with where it came from", () => {
    expect(appHref(5)).toBe("/applications/5");
    expect(appHref(5, "home")).toBe("/applications/5?from=home");
    expect(appHref(5, "needs-you", "signin_captcha")).toBe("/applications/5?from=needs-you&show=signin_captcha");
    expect(appHref(5, "needs-you", "all")).toBe("/applications/5?from=needs-you");
  });
});
