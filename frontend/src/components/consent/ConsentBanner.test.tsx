/**
 * Consent banner (docs/fable/05 C3).
 *
 * The point of these tests is the LEGAL behaviour, not the pixels:
 *  - an undecided visitor is asked,
 *  - a decided visitor is not nagged,
 *  - "Decline" is a real, equally-available choice that is remembered,
 *  - nothing is stored until the user actually chooses.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ConsentBanner } from "./ConsentBanner";
import { CONSENT_KEY, getConsent } from "@/lib/consent";

describe("ConsentBanner", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("asks an undecided visitor", async () => {
    render(<ConsentBanner />);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /accept/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /decline/i })).toBeInTheDocument();
  });

  it("stores nothing until the user chooses", () => {
    render(<ConsentBanner />);
    expect(window.localStorage.getItem(CONSENT_KEY)).toBeNull();
  });

  it("records acceptance and hides the banner", async () => {
    render(<ConsentBanner />);
    await userEvent.click(await screen.findByRole("button", { name: /accept/i }));
    expect(getConsent()).toBe("accepted");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("records a decline and hides the banner", async () => {
    render(<ConsentBanner />);
    await userEvent.click(await screen.findByRole("button", { name: /decline/i }));
    expect(getConsent()).toBe("declined");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("does not nag a visitor who already decided", () => {
    window.localStorage.setItem(CONSENT_KEY, "declined");
    render(<ConsentBanner />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
