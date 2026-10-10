import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryTab } from "./MemoryTab";

const getProfile = vi.fn();
const getProfileEditHistory = vi.fn();
const updateProfileFields = vi.fn();

vi.mock("@/lib/api", () => ({
  getProfile: (...a: unknown[]) => getProfile(...a),
  getProfileEditHistory: (...a: unknown[]) => getProfileEditHistory(...a),
  updateProfileFields: (...a: unknown[]) => updateProfileFields(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const profile = {
  user_info: {
    contact: { email: "alex@example.com", phone: "+44 2" },
    right_to_work: {
      countries: [{ country: "GB", work_authorization: "citizen" }, { country: "DE", needs_sponsorship: true }],
      sanctions_country_citizen: "Prefer not to say",
    },
    logistics: {},
    languages: [{ language: "English", level: "fluent" }],
    equality: { gender: "Prefer not to say" },
    answers: [
      { question: "Biggest project?", answer: "An agent.", approved: true, recorded_at: "2026-10-04T12:00:00Z" },
      { question: "Draft?", answer: "no", approved: false, recorded_at: "2026-10-04T12:00:00Z" },
    ],
  },
  preferences: { salary_by_country: [{ country: "GB", min: 80000, max: 95000, currency: "GBP", period: "year" }] },
};

const histories: Record<string, unknown[]> = {
  "user_info.contact": [
    { set_by: "web", set_at: "2026-10-05T12:00:00Z", value: { email: "alex@example.com", phone: "+44 2" } },
    { set_by: "agent:Claude", set_at: "2026-10-03T12:00:00Z", value: { email: "alex@example.com", phone: "+44 1" } },
  ],
};

beforeEach(() => {
  getProfile.mockReset().mockResolvedValue(profile);
  getProfileEditHistory.mockReset().mockImplementation(async (p: string) => histories[p] ?? []);
  updateProfileFields.mockReset().mockImplementation(async () => profile);
});

describe("MemoryTab", () => {
  it("reads the profile and the seven histories in parallel", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    expect(getProfile).toHaveBeenCalledTimes(1);
    expect(getProfileEditHistory.mock.calls.map((c) => c[0]).sort()).toEqual([
      "preferences.salary_by_country", "user_info.answers", "user_info.contact", "user_info.equality",
      "user_info.languages", "user_info.logistics", "user_info.right_to_work",
    ]);
  });

  it("shows the approved sections in order, with provenance and 'was'", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual([
      "Contact & identity", "Right to work", "Logistics & languages", "Equality", "Approved answers",
      "How memory works", "Job targets",
    ]);
    expect(screen.getByTestId("memory-row-user_info.contact.phone")).toHaveTextContent("You changed this");
    expect(screen.getByTestId("memory-row-user_info.contact.phone")).toHaveTextContent("was: +44 1");
    expect(screen.getByTestId("memory-row-user_info.contact.email")).toHaveTextContent("alex@example.com");
    expect(screen.getByText("Biggest project?")).toBeInTheDocument();
    expect(screen.queryByText("Draft?")).toBeNull();
    expect(screen.getByText(/Voluntary\. Forms ask, and you never have to answer/)).toBeInTheDocument();
  });

  it("Germany is a collapsed card with an amber chip; the rail shows salary and the missing one", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    expect(screen.getByTestId("country-missing-DE")).toHaveTextContent("3 not saved yet");
    expect(screen.getByTestId("memory-rail")).toHaveTextContent("£80k–95k GBP");
    expect(screen.getByTestId("memory-row-preferences.salary_by_country.DE")).toHaveTextContent("not saved yet");
  });

  it("a web edit PATCHes the WHOLE block", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    fireEvent.click(screen.getByRole("button", { name: "Edit Phone" }));
    fireEvent.change(screen.getByLabelText("Phone"), { target: { value: "+44 3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "user_info.contact", value: { email: "alex@example.com", phone: "+44 3" } },
      ]),
    );
  });

  it("two quick edits in one block run in turn; the second keeps the first", async () => {
    let release: (p: unknown) => void = () => undefined;
    const afterPhone = { ...profile, user_info: { ...profile.user_info, contact: { email: "alex@example.com", phone: "+44 3" } } };
    updateProfileFields
      .mockImplementationOnce(() => new Promise((r) => { release = r; }))
      .mockImplementationOnce(async () => afterPhone);
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    fireEvent.click(screen.getByRole("button", { name: "Edit Phone" }));
    fireEvent.change(screen.getByLabelText("Phone"), { target: { value: "+44 3" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Edit Email" }));
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "new@example.com" } });
    fireEvent.click(within(screen.getByTestId("memory-row-user_info.contact.email")).getByRole("button", { name: "Save" }));
    await new Promise((r) => setTimeout(r, 20));
    expect(updateProfileFields).toHaveBeenCalledTimes(1); // the second waits for the first
    release(afterPhone);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(2));
    expect(updateProfileFields.mock.calls[1][0]).toEqual([
      { path: "user_info.contact", value: { email: "new@example.com", phone: "+44 3" } },
    ]);
  });

  it("Take back restores the previous phone as a new write", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    fireEvent.click(screen.getByTestId("memory-take-back"));
    await waitFor(() =>
      expect(updateProfileFields).toHaveBeenCalledWith([
        { path: "user_info.contact", value: { email: "alex@example.com", phone: "+44 1" } },
      ]),
    );
  });

  it("Add a country adds an empty card; nothing is saved", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    fireEvent.click(screen.getByRole("button", { name: "Add a country" }));
    fireEvent.change(screen.getByPlaceholderText("Search countries…"), { target: { value: "France" } });
    fireEvent.click(await screen.findByText("France"));
    expect(screen.getByTestId("country-missing-FR")).toHaveTextContent("4 not saved yet");
    expect(updateProfileFields).not.toHaveBeenCalled();
  });
});
