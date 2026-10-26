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
vi.mock("next/navigation", () => ({ usePathname: () => "/profile" }));
const fetchSettingsShared = vi.fn();
vi.mock("@/lib/assistant-state", () => ({ fetchSettingsShared: (...a: unknown[]) => fetchSettingsShared(...a) }));

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
  // Every step not done, so every step starts open.
  fetchSettingsShared.mockReset().mockResolvedValue(null);
});

describe("MemoryTab", () => {
  it("reads the profile and every memory history (job targets included) in parallel", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    expect(getProfile).toHaveBeenCalledTimes(1);
    expect(getProfileEditHistory.mock.calls.map((c) => c[0]).sort()).toEqual([
      "preferences.dealbreakers", "preferences.experience_level",
      "preferences.experience_years_max", "preferences.experience_years_min", "preferences.looking_for",
      "preferences.preferred_locations", "preferences.salary_by_country", "preferences.target_job_titles",
      "preferences.work_arrangement",
      "user_info.answers", "user_info.contact", "user_info.equality",
      "user_info.languages", "user_info.logistics", "user_info.right_to_work",
    ]);
  });

  it("Job targets shows the roles and other targets; an edit saves that one preference", async () => {
    const withTargets = { ...profile, preferences: { ...profile.preferences, target_job_titles: ["Data Engineer"], experience_level: "senior" } };
    getProfile.mockResolvedValue(withTargets);
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    const targets = screen.getByTestId("memory-step-targets");
    expect(within(targets).getByTestId("memory-row-preferences.target_job_titles")).toHaveTextContent("Data Engineer");
    expect(within(targets).getByTestId("memory-row-preferences.experience_level")).toHaveTextContent("Senior");
    expect(within(targets).getByTestId("memory-row-preferences.dealbreakers")).toHaveTextContent("Not saved yet");
    fireEvent.click(within(targets).getByRole("button", { name: "Edit Roles" }));
    fireEvent.change(screen.getByLabelText("Roles"), { target: { value: "Data Engineer\nAnalytics Engineer" } });
    fireEvent.click(within(screen.getByTestId("memory-row-preferences.target_job_titles")).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "preferences.target_job_titles", value: ["Data Engineer", "Analytics Engineer"] },
    ]));
  });

  it("puts your details for forms first and pinned open, then the four steps, then approved answers", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    const sections = screen.getAllByTestId(/^memory-step-[a-z]+$/).map((s) => s.getAttribute("data-testid"));
    expect(sections).toEqual(["memory-step-you", "memory-step-targets", "memory-step-visa", "memory-step-logistics", "memory-step-equality"]);
    // the details block has no open/close toggle; citizenship sits there, not under Right to work
    expect(screen.queryByTestId("memory-step-toggle-you")).toBeNull();
    const details = screen.getByTestId("memory-step-you");
    for (const id of ["name", "email", "phone", "address", "date_of_birth"]) {
      expect(within(details).getByTestId(`memory-row-user_info.contact.${id}`)).toBeInTheDocument();
    }
    expect(within(details).getByTestId("memory-row-user_info.right_to_work.citizenship")).toBeInTheDocument();
    expect(within(screen.getByTestId("memory-step-visa")).queryByTestId("memory-row-user_info.right_to_work.citizenship")).toBeNull();
    const steps = ["targets", "visa", "logistics", "equality"].map((id) => screen.getByTestId(`memory-step-toggle-${id}`));
    expect(steps.every((b) => b.getAttribute("aria-expanded") === "true")).toBe(true);
    expect(steps.map((b) => b.textContent)).toEqual([
      expect.stringContaining("Job targets"), expect.stringContaining("Right to work"),
      expect.stringContaining("Logistics"), expect.stringContaining("Equality (voluntary)"),
    ]);
    expect(screen.queryByText("Assistant settings")).toBeNull();
    const titles = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(titles).toEqual([expect.stringContaining("Your details for forms"), "Approved answers", "How memory works"]);
    expect(screen.getByTestId("memory-row-user_info.contact.phone")).toHaveTextContent("You changed this");
    expect(screen.getByTestId("memory-row-user_info.contact.phone")).toHaveTextContent("was: +44 1");
    expect(screen.getByTestId("memory-row-user_info.contact.email")).toHaveTextContent("alex@example.com");
    expect(screen.getByText("Biggest project?")).toBeInTheDocument();
    expect(screen.queryByText("Draft?")).toBeNull();
    expect(screen.getByText(/Voluntary\. Forms ask, and you never have to answer/)).toBeInTheDocument();
  });

  it("Job targets holds the job priority grid (salary per country); Right to work cards keep only visa facts", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    const targets = screen.getByTestId("memory-step-targets");
    expect(within(targets).getByTestId("work-grid")).toBeInTheDocument();
    expect(within(targets).getByTestId("work-grid-salary-GB")).toHaveTextContent("£80k–95k GBP");
    expect(within(targets).getByTestId("work-grid-salary-DE")).toHaveTextContent("Add salary");
    expect(screen.getByTestId("memory-rail")).not.toHaveTextContent("£80k–95k GBP");
    // the card counts status, visa type and expiry only: remote, on-site, priority and visa need are in the grid
    expect(screen.getByTestId("country-missing-DE")).toHaveTextContent("3 not saved yet");
    const visa = screen.getByTestId("memory-step-visa");
    expect(within(visa).queryByTestId("memory-row-user_info.right_to_work.GB.remote_ok")).toBeNull();
    expect(within(visa).queryByTestId("memory-row-user_info.right_to_work.GB.needs_sponsorship")).toBeNull();
    expect(within(visa).getByTestId("memory-row-user_info.right_to_work.GB.work_authorization")).toBeInTheDocument();
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

  it("a country is added once, in Job targets; Right to work has no second Add a country", async () => {
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    expect(screen.getAllByRole("button", { name: "Add a country" })).toHaveLength(1);
    expect(within(screen.getByTestId("memory-step-targets")).getByTestId("work-grid-add")).toBeInTheDocument();
    expect(screen.getByTestId("visa-add-hint")).toHaveTextContent("A new country is added once, in Job targets");
    fireEvent.click(screen.getByTestId("work-grid-add"));
    fireEvent.change(screen.getByPlaceholderText("Search countries…"), { target: { value: "France" } });
    fireEvent.click(await screen.findByText("France"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    const [{ path, value }] = updateProfileFields.mock.calls[0][0];
    expect(path).toBe("user_info.right_to_work");
    expect(value.countries.at(-1)).toEqual({ country: "FR" });
  });

  it("a done step with nothing to confirm starts closed and shows its date; a click opens it", async () => {
    fetchSettingsShared.mockResolvedValue({
      setup_progress: { rounds: { equality: { done_at: "2026-10-20T09:00:00Z" } } },
    });
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    const toggle = await screen.findByTestId("memory-step-toggle-equality");
    await waitFor(() => expect(toggle).toHaveAttribute("aria-expanded", "false"));
    expect(screen.getByTestId("memory-step-equality")).toHaveAttribute("data-done", "true");
    expect(screen.queryByTestId("memory-row-user_info.equality.gender")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByTestId("memory-row-user_info.equality.gender")).toBeInTheDocument();
    expect(screen.getByTestId("memory-step-toggle-visa")).toHaveAttribute("aria-expanded", "true");
  });

  it("Add my own fact saves the whole note list with the step name in front; Remove takes only that fact", async () => {
    const withNotes = { ...profile, preferences: { ...profile.preferences, assistant_notes: ["Never apply to agencies."] } };
    getProfile.mockResolvedValue(withNotes);
    const added = { ...withNotes, preferences: { ...withNotes.preferences, assistant_notes: ["Never apply to agencies.", "About you: I go by Alex."] } };
    updateProfileFields.mockResolvedValueOnce(added).mockResolvedValueOnce(withNotes);
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    fireEvent.change(screen.getByLabelText("Add a fact to About you"), { target: { value: "I go by Alex." } });
    fireEvent.click(within(screen.getByTestId("step-facts-you")).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "preferences.assistant_notes", value: ["Never apply to agencies.", "About you: I go by Alex."] },
    ]));
    const facts = screen.getByTestId("step-facts-you");
    expect(await within(facts).findByText("I go by Alex.")).toBeInTheDocument();
    expect(within(screen.getByTestId("step-facts-logistics")).queryByText("I go by Alex.")).toBeNull();
    fireEvent.click(within(facts).getByRole("button", { name: 'Remove "I go by Alex."' }));
    await waitFor(() => expect(updateProfileFields).toHaveBeenLastCalledWith([
      { path: "preferences.assistant_notes", value: ["Never apply to agencies."] },
    ]));
  });

  it("two quick adds in different steps both survive: the second is applied to the list the first saved", async () => {
    getProfile.mockResolvedValue({ ...profile, preferences: { ...profile.preferences, assistant_notes: [] } });
    let release: (p: unknown) => void = () => undefined;
    const afterFirst = { ...profile, preferences: { ...profile.preferences, assistant_notes: ["About you: I go by Alex."] } };
    updateProfileFields
      .mockImplementationOnce(() => new Promise((r) => { release = r; }))
      .mockImplementationOnce(async () => afterFirst);
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    fireEvent.change(screen.getByLabelText("Add a fact to About you"), { target: { value: "I go by Alex." } });
    fireEvent.click(within(screen.getByTestId("step-facts-you")).getByRole("button", { name: "Add" }));
    fireEvent.change(screen.getByLabelText("Add a fact to Logistics"), { target: { value: "No travel in August." } });
    fireEvent.click(within(screen.getByTestId("step-facts-logistics")).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    release(afterFirst);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(2));
    expect(updateProfileFields.mock.calls[1][0]).toEqual([
      { path: "preferences.assistant_notes", value: ["About you: I go by Alex.", "Logistics: No travel in August."] },
    ]);
  });

  it("your details ask citizenship, the country and the city you live in as separate questions, in that order", async () => {
    const lives = { ...profile, user_info: { ...profile.user_info, contact: { ...profile.user_info.contact, residence_country: "GB", residence_city: "London" } } };
    getProfile.mockResolvedValue(lives);
    render(<MemoryTab />);
    await screen.findByTestId("memory-tab");
    const ids = within(screen.getByTestId("memory-step-you")).getAllByTestId(/^memory-row-/).map((r) => r.getAttribute("data-testid"));
    const at = (id: string) => ids.indexOf(`memory-row-${id}`);
    expect(at("user_info.right_to_work.citizenship")).toBeLessThan(at("user_info.contact.residence_country"));
    expect(at("user_info.contact.residence_country")).toBe(at("user_info.contact.residence_city") - 1);
    expect(screen.getByTestId("memory-row-user_info.contact.residence_country")).toHaveTextContent("Country you live in");
    expect(screen.getByTestId("memory-row-user_info.contact.residence_country")).toHaveTextContent("United Kingdom");
    expect(screen.getByTestId("memory-row-user_info.contact.residence_city")).toHaveTextContent("London");
  });
});

