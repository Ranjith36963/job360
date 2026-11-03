import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryTab } from "./MemoryTab";
import { confirmRow, confirmItem, fromText } from "./CountryPages";

const getProfile = vi.fn();
const getProfileEditHistory = vi.fn();
const updateProfileFields = vi.fn();
const getCountryPage = vi.fn();

vi.mock("@/lib/api", () => ({
  getProfile: (...a: unknown[]) => getProfile(...a),
  getProfileEditHistory: (...a: unknown[]) => getProfileEditHistory(...a),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
  getCountryPage: (...a: unknown[]) => getCountryPage(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock("next/navigation", () => ({ usePathname: () => "/profile" }));
vi.mock("@/lib/assistant-state", () => ({ fetchSettingsShared: vi.fn().mockResolvedValue(null) }));

const GB = { country: "GB", work_authorization: "citizen", needs_sponsorship: false };
const AE = { country: "AE", work_authorization: "needs_sponsorship", needs_sponsorship: true, set_by: "assistant" };
const profile = {
  user_info: { contact: { email: "a@example.com" }, right_to_work: { countries: [GB, AE] }, logistics: {}, languages: [], equality: {}, answers: [] },
  preferences: {},
};
const pages: Record<string, unknown> = {
  GB: {
    country: "GB", confirmed: true, assistant_drafted: false,
    instructions: { from: "EUROPE", lines: ["Mention the Blue Card only if asked"] },
    right_to_work: { from: "GB", value: { work_authorization: "citizen", needs_sponsorship: false } },
    salary: { from: "ANY", value: { min: 80000, max: 95000, currency: "GBP", period: "year" } },
  },
  AE: {
    country: "AE", assistant_drafted: true, confirmed: false, region_fallback: "MIDDLE_EAST",
    to_confirm: ["right_to_work.AE.work_authorization", "right_to_work.AE.needs_sponsorship"],
    right_to_work: { from: "AE", value: { work_authorization: "needs_sponsorship", needs_sponsorship: true } },
  },
};

beforeEach(() => {
  getProfile.mockReset().mockResolvedValue(profile);
  getProfileEditHistory.mockReset().mockResolvedValue([]);
  updateProfileFields.mockReset().mockImplementation(async () => profile);
  getCountryPage.mockReset().mockImplementation(async (c: string) => pages[c]);
});

describe("country pages on the Memory page", () => {
  it("lists the countries the profile holds (not a fixed list) and says where each part comes from", async () => {
    render(<MemoryTab />);
    const section = await screen.findByTestId("country-pages");
    expect(getCountryPage.mock.calls.map((c) => c[0]).sort()).toEqual(["AE", "GB"]);
    expect(within(section).getAllByRole("tab").map((t) => t.textContent)).toEqual(["United Kingdom", "United Arab Emirateddraft"].map((x) => expect.stringContaining(x.slice(0, 6))));
    // AE is the draft, so it opens first; open GB.
    fireEvent.click(within(section).getByTestId("country-tab-GB"));
    const gb = await within(section).findByTestId("country-page-GB");
    expect(gb).toHaveTextContent("Mention the Blue Card only if asked");
    expect(gb).toHaveTextContent("From your Europe settings");
    expect(gb).toHaveTextContent("From your settings for every country");
    expect(gb).toHaveTextContent("80,000 to 95,000 GBP per year");
    expect(within(gb).queryByTestId("country-draft-banner")).toBeNull();
    expect(within(gb).getAllByTestId("country-from")).toHaveLength(2); // the country's own right to work says nothing
  });

  it("an assistant draft gets a banner naming what to confirm; Confirm sends only that row without its mark", async () => {
    render(<MemoryTab />);
    const ae = await screen.findByTestId("country-page-AE");
    const banner = within(ae).getByTestId("country-draft-banner");
    expect(banner).toHaveTextContent("Your assistant's draft — confirm");
    expect(banner).toHaveTextContent("Work authorization");
    expect(banner).toHaveTextContent("Needs visa sponsorship");
    fireEvent.click(within(banner).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "user_info.right_to_work", value: { countries: [GB, { country: "AE", work_authorization: "needs_sponsorship", needs_sponsorship: true }] } },
    ]);
  });

  it("saving something unrelated never writes the right-to-work block, so no draft is confirmed", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("country-draft-banner");
    const targets = screen.getByTestId("memory-step-targets");
    fireEvent.click(within(targets).getByRole("button", { name: "Edit Roles" }));
    fireEvent.change(screen.getByLabelText("Roles"), { target: { value: "Data Engineer" } });
    fireEvent.click(within(screen.getByTestId("memory-row-preferences.target_job_titles")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{ path: "preferences.target_job_titles", value: ["Data Engineer"] }]));
    expect(updateProfileFields.mock.calls.flat(2).some((e: { path: string }) => e.path === "user_info.right_to_work")).toBe(false);
  });
});

describe("country page helpers", () => {
  it("confirmRow drops the mark from that row only; other drafted rows go back as stored", () => {
    const FR = { country: "FR", needs_sponsorship: true, set_by: "assistant" };
    expect(confirmRow({ countries: [GB, AE, FR], sanctions_country_citizen: "x" }, "AE")).toEqual({
      countries: [GB, { country: "AE", work_authorization: "needs_sponsorship", needs_sponsorship: true }, FR], sanctions_country_citizen: "x",
    });
  });
  it("says where a value comes from in plain words, and nothing for the country's own", () => {
    expect(fromText("DE", "DE")).toBeNull();
    expect(fromText("EUROPE", "DE")).toBe("From your Europe settings");
    expect(fromText("ANY", "DE")).toBe("From your settings for every country");
    expect(confirmItem("right_to_work.AE.needs_sponsorship")).toBe("Needs visa sponsorship");
  });
});
