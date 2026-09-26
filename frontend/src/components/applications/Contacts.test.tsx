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
});
