import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { Contacts } from "./Contacts";
import type { Contact } from "@/lib/api";

const addContact = vi.fn();
const updateContact = vi.fn();

vi.mock("@/lib/api", () => ({
  addContact: (...args: unknown[]) => addContact(...args),
  updateContact: (...args: unknown[]) => updateContact(...args),
}));

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));

function emptyOutreach(): Contact["outreach"] {
  return { messages: [], sent: [], replies: [], message_count: 0, last_sent: null, replied: false, last_reply: null };
}

function makeContact(overrides: Partial<Contact> = {}): Contact {
  return {
    id: 1,
    application_id: 42,
    name: "Priya Shah",
    role: "Talent Partner",
    email: "priya@northwind.example",
    linkedin_url: "",
    notes: "",
    added_by: "web",
    created_at: "2026-09-20T10:00:00+00:00",
    edit_history: {},
    outreach: emptyOutreach(),
    ...overrides,
  };
}

describe("Contacts", () => {
  beforeEach(() => {
    addContact.mockReset();
    updateContact.mockReset();
  });

  it("shows the latest drafted message with earlier versions folded", () => {
    const contact = makeContact({
      outreach: {
        ...emptyOutreach(),
        messages: [
          {
            id: 1, contact_id: 1, entry: "message", channel: "linkedin", text: "Hi Priya, v1",
            version_no: 1, occurred_at: "2026-09-20T10:00:00+00:00", recorded_at: "2026-09-20T10:00:00+00:00",
            recorded_by: "agent:1", source_message_id: "",
          },
          {
            id: 2, contact_id: 1, entry: "message", channel: "linkedin", text: "Hi Priya, v2",
            version_no: 2, occurred_at: "2026-09-21T10:00:00+00:00", recorded_at: "2026-09-21T10:00:00+00:00",
            recorded_by: "agent:1", source_message_id: "",
          },
        ],
      },
    });
    render(<Contacts applicationId={42} contacts={[contact]} />);
    expect(screen.getByText("Hi Priya, v2")).toBeInTheDocument();
    const details = screen.getByText("Earlier versions (1)").closest("details");
    expect(details).not.toBeNull();
    // Native <details> keeps its content in the DOM even collapsed — the
    // fold is a CSS/attribute state, not an absence of the node.
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(screen.getByText("Earlier versions (1)"));
  });

  it('shows "Sent on … via LinkedIn" once a send is recorded', () => {
    const contact = makeContact({
      outreach: {
        ...emptyOutreach(),
        last_sent: {
          id: 3, contact_id: 1, entry: "sent", channel: "linkedin", text: "",
          version_no: null, occurred_at: "2026-10-03T09:00:00+00:00", recorded_at: "2026-10-03T09:00:00+00:00",
          recorded_by: "web", source_message_id: "",
        },
      },
    });
    render(<Contacts applicationId={42} contacts={[contact]} />);
    expect(screen.getByText(/Sent on .* via LinkedIn/)).toBeInTheDocument();
  });

  it('shows "No reply yet" when nobody has replied', () => {
    render(<Contacts applicationId={42} contacts={[makeContact()]} />);
    expect(screen.getByText("No reply yet.")).toBeInTheDocument();
    expect(screen.getByText("Not sent yet.")).toBeInTheDocument();
  });

  it('an edited field shows "was X" in the folded history', async () => {
    const contact = makeContact({
      role: "Senior Recruiter",
      edit_history: {
        role: [
          { value: "Recruiter", recorded_at: "2026-09-20T10:00:00+00:00", recorded_by: "web" },
          { value: "Senior Recruiter", recorded_at: "2026-09-22T10:00:00+00:00", recorded_by: "web" },
        ],
      },
    });
    render(<Contacts applicationId={42} contacts={[contact]} />);
    fireEvent.click(screen.getByText("History"));
    expect(screen.getByText(/Role: was .Recruiter./)).toBeInTheDocument();
  });

  it('shows "added by you" for a contact the seeker added (display mapping only)', () => {
    render(<Contacts applicationId={42} contacts={[makeContact({ added_by: "web" })]} />);
    expect(screen.getByText(/added by you/)).toBeInTheDocument();
  });

  it('shows the assistant\'s name for a contact an assistant added', () => {
    render(<Contacts applicationId={42} contacts={[makeContact({ added_by: "agent:claude-code" })]} />);
    expect(screen.getByText(/added by claude-code/)).toBeInTheDocument();
  });

  it("editing a field calls updateContact and reflects the saved value", async () => {
    const contact = makeContact();
    updateContact.mockResolvedValue(
      makeContact({ role: "Talent Lead", edit_history: { role: [{ value: "Talent Lead", recorded_at: "2026-10-01T00:00:00+00:00", recorded_by: "web" }] } })
    );
    render(<Contacts applicationId={42} contacts={[contact]} />);
    fireEvent.click(screen.getAllByText("Edit")[0]);
    const roleInput = screen.getByLabelText("Role");
    fireEvent.change(roleInput, { target: { value: "Talent Lead" } });
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => expect(updateContact).toHaveBeenCalledWith(1, { role: "Talent Lead" }));
    await waitFor(() =>
      expect(screen.getByTestId("contacts-list").textContent).toContain("Talent Lead")
    );
  });

  it("shows where a person was found in plain words, and nothing when unset", () => {
    const { rerender } = render(
      <Contacts applicationId={42} contacts={[makeContact({ found_via: "linkedin" })]} />
    );
    expect(screen.getByTestId("contact-found-via")).toHaveTextContent("Found via LinkedIn");
    rerender(<Contacts applicationId={42} contacts={[makeContact({ found_via: "job_ad" })]} />);
    expect(screen.getByTestId("contact-found-via")).toHaveTextContent("Found via Job ad");
    rerender(<Contacts applicationId={42} contacts={[makeContact({ found_via: null })]} />);
    expect(screen.queryByTestId("contact-found-via")).toBeNull();
  });

  it("the add form sends found_via only when one is chosen", async () => {
    addContact.mockResolvedValue({ contact: makeContact({ id: 9, name: "Sam", found_via: "apollo" }), already_existed: false });
    render(<Contacts applicationId={42} contacts={[]} />);
    fireEvent.click(screen.getByTestId("contacts-add-toggle"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Sam" } });
    fireEvent.change(screen.getByLabelText("Found via (optional)"), { target: { value: "apollo" } });
    fireEvent.click(screen.getByRole("button", { name: "Add person" }));
    await waitFor(() => expect(addContact).toHaveBeenCalledWith(42, { name: "Sam", found_via: "apollo" }));
  });

  it("the edit form writes found_via, and Not set clears it", async () => {
    updateContact.mockResolvedValue(makeContact({ found_via: "event" }));
    render(<Contacts applicationId={42} contacts={[makeContact({ found_via: null })]} />);
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("Found via"), { target: { value: "event" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateContact).toHaveBeenCalledWith(1, { found_via: "event" }));

    updateContact.mockClear();
    updateContact.mockResolvedValue(makeContact({ found_via: null }));
    fireEvent.click(screen.getByText("Edit"));
    fireEvent.change(screen.getByLabelText("Found via"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateContact).toHaveBeenCalledWith(1, { found_via: "" }));
  });
});
