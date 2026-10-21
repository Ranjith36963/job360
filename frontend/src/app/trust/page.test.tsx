import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";
import TrustPage from "./page";
import PrivacyPage from "../privacy/page";
import { SUBPROCESSORS } from "@/lib/legal";

describe("TrustPage", () => {
  it("renders every section", () => {
    render(<TrustPage />);
    for (const id of [
      "trust-where",
      "trust-recorded",
      "trust-retention",
      "trust-delete",
      "trust-export",
      "trust-never",
      "trust-subprocessors",
    ]) {
      expect(screen.getByTestId(id)).toBeInTheDocument();
    }
  });

  it("discloses masked, opt-in session replay plainly with the sample rates", () => {
    render(<TrustPage />);
    const replay = screen.getByTestId("trust-replay").textContent ?? "";
    expect(replay).toMatch(/records your screen only if you turn on/i);
    expect(replay).toMatch(/Help improve\s+Job360/);
    expect(replay).toMatch(/off by default/);
    expect(replay).toMatch(/all text and media on screen are hidden/);
    expect(replay).not.toMatch(/not masked/);
    expect(replay).toMatch(/10%/);
    expect(replay).toMatch(/every session\s+that hits an error/);
  });

  it("states hosting as the privacy page does and never claims EU hosting", () => {
    render(<TrustPage />);
    const where = screen.getByTestId("trust-where").textContent ?? "";
    expect(where).toMatch(/servers currently in the USA/);
    expect(where).toMatch(/Sentry\) is located in the EU \(Germany\)/);
    expect(where).not.toMatch(/hosted in the EU/i);
  });

  it("lists exactly the same subprocessors as the privacy page", () => {
    const trust = render(<TrustPage />);
    const trustRows = within(trust.getByTestId("trust-subprocessors"))
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.textContent);
    trust.unmount();
    const privacy = render(<PrivacyPage />);
    const privacyRows = privacy
      .getAllByRole("row")
      .slice(1)
      .map((r) => r.textContent);
    expect(trustRows).toEqual(privacyRows);
    expect(trustRows).toHaveLength(SUBPROCESSORS.length);
  });
});
