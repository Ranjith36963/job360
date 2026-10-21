import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const { listPeople, getPerson, addPerson, apiError, sonnerToast } = vi.hoisted(() => ({
  listPeople: vi.fn(),
  getPerson: vi.fn(),
  addPerson: vi.fn(),
  apiError: vi.fn(),
  sonnerToast: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ listPeople, getPerson, addPerson }));
vi.mock("@/lib/toast", () => ({ toast: { apiError } }));
vi.mock("sonner", () => ({ toast: sonnerToast }));

import { PeopleClient } from "../PeopleClient";
import PeoplePage, { metadata } from "../page";
import { ApiError } from "@/lib/api-error";
import { formatDate } from "@/lib/format-date";

const entry = (over: Record<string, unknown>) => ({
  id: 5,
  contact_id: 11,
  entry: "sent",
  channel: "linkedin",
  text: "",
  version_no: null,
  occurred_at: "2026-10-03T09:00:00Z",
  recorded_at: "2026-10-03T09:00:00Z",
  recorded_by: "agent:claude",
  source_message_id: "",
  ...over,
});

const P1 = {
  contact_ids: [11, 12],
  name: "Priya Raman",
  role: "Engineering Manager",
  email: "priya@northwind.example",
  linkedin_url: "https://www.linkedin.com/in/priya-raman",
  notes: "Met at PyCon",
  found_via: "referral",
  jobs: [
    { application_id: 42, job_title: "Staff Engineer", job_company: "Northwind" },
    { application_id: 57, job_title: "Platform Lead", job_company: "Contoso" },
  ],
  message_count: 2,
  last_sent: entry({}),
  replied: true,
  last_reply: entry({ id: 6, entry: "reply", occurred_at: "2026-10-05T14:00:00Z" }),
};

const P2 = {
  contact_ids: [31],
  name: "Sam Okafor",
  role: "",
  email: "",
  linkedin_url: "javascript:alert(1)",
  notes: "",
  found_via: null,
  jobs: [],
  message_count: 1,
  last_sent: null,
  replied: false,
  last_reply: null,
};

beforeEach(() => {
  vi.clearAllMocks();
  listPeople.mockResolvedValue({ people: [P1, P2], person: null, truncated: false });
});

async function rows() {
  return await screen.findAllByTestId("person-row");
}

describe("/people", () => {
  it("renders one row per person with role, found via, linked applications and outreach marks", async () => {
    render(<PeopleClient />);
    const r = await rows();
    expect(r).toHaveLength(2);
    expect(r[0]).toHaveTextContent("Priya Raman");
    expect(r[0]).toHaveTextContent("Engineering Manager");
    expect(r[0]).toHaveTextContent("Found via Referral");
    expect(within(r[0]).getByRole("link", { name: "Staff Engineer · Northwind" })).toHaveAttribute(
      "href",
      "/applications/42"
    );
    expect(within(r[0]).getByRole("link", { name: "Platform Lead · Contoso" })).toHaveAttribute(
      "href",
      "/applications/57"
    );
    expect(within(r[0]).getByTestId("person-last-sent")).toHaveTextContent(
      `Sent on ${formatDate("2026-10-03T09:00:00Z")} via LinkedIn`
    );
    expect(within(r[0]).getByTestId("person-last-reply")).toHaveTextContent(
      formatDate("2026-10-05T14:00:00Z")
    );
    expect(within(r[0]).queryByTestId("person-not-sent")).toBeNull();
  });

  it("flags a cold contact that was never sent to", async () => {
    render(<PeopleClient />);
    const r = await rows();
    expect(within(r[1]).getByTestId("person-not-sent")).toHaveTextContent("Not sent yet · 1 drafted");
    expect(within(r[1]).getByTestId("person-no-jobs")).toHaveTextContent("Not linked to a job");
    expect(within(r[1]).getByTestId("person-no-reply")).toBeInTheDocument();
  });

  it("never turns a non-https LinkedIn value into a link", async () => {
    render(<PeopleClient />);
    const r = await rows();
    const hrefs = Array.from(r[1].querySelectorAll("a")).map((a) => a.getAttribute("href") ?? "");
    expect(hrefs.some((h) => h.includes("javascript:"))).toBe(false);
    expect(r[1]).toHaveTextContent("javascript:alert(1)");
  });

  it("loads the edit history for every contact row only when History is opened", async () => {
    getPerson.mockImplementation(async (id: number) =>
      id === 11
        ? {
            id: 11,
            jobs: [{ application_id: 42, job_title: "Staff Engineer", job_company: "Northwind" }],
            edit_history: {
              role: [
                { value: "Recruiter", recorded_at: "2026-09-30T08:00:00Z", recorded_by: "agent:claude" },
                { value: "Engineering Manager", recorded_at: "2026-10-01T08:00:00Z", recorded_by: "web" },
              ],
            },
          }
        : { id: 12, jobs: [], edit_history: {} }
    );
    const user = userEvent.setup();
    render(<PeopleClient />);
    const r = await rows();
    expect(getPerson).not.toHaveBeenCalled();
    await user.click(within(r[0]).getByTestId("person-history-toggle"));
    const panel = await within(r[0]).findByTestId("person-history");
    expect(getPerson).toHaveBeenCalledTimes(2);
    expect(getPerson).toHaveBeenCalledWith(11);
    expect(getPerson).toHaveBeenCalledWith(12);
    expect(panel).toHaveTextContent("Role: was “Recruiter”");
    expect(panel).toHaveTextContent(formatDate("2026-09-30T08:00:00Z"));
    expect(panel).toHaveTextContent("by claude");
  });

  it("+ Add person posts a cold contact without application_id and reloads the list", async () => {
    const user = userEvent.setup();
    addPerson.mockResolvedValue({ contact: {}, contact_id: 99, already_existed: false, event_id: 1 });
    render(<PeopleClient />);
    await rows();
    listPeople.mockResolvedValue({
      people: [{ ...P2, contact_ids: [99], name: "Lena Fischer" }],
      person: null,
      truncated: false,
    });
    await user.click(screen.getByTestId("people-add-toggle"));
    await user.type(screen.getByLabelText("Name"), "Lena Fischer");
    await user.type(screen.getByLabelText("Email"), "lena@fabrikam.example");
    await user.selectOptions(screen.getByLabelText(/Found via/), "linkedin");
    await user.click(screen.getByRole("button", { name: "Add person" }));
    await waitFor(() => expect(addPerson).toHaveBeenCalledTimes(1));
    expect(addPerson).toHaveBeenCalledWith({
      name: "Lena Fischer",
      email: "lena@fabrikam.example",
      found_via: "linkedin",
    });
    expect(addPerson.mock.calls[0][0]).not.toHaveProperty("application_id");
    await waitFor(() => expect(listPeople).toHaveBeenCalledTimes(2));
    expect(await screen.findByText("Lena Fischer")).toBeInTheDocument();
  });

  it("shows a plain error when adding fails", async () => {
    const user = userEvent.setup();
    addPerson.mockRejectedValue(new ApiError(409, "cold-contact cap reached; CONTACTS_UNLINKED_MAX is 200"));
    render(<PeopleClient />);
    await rows();
    await user.click(screen.getByTestId("people-add-toggle"));
    await user.type(screen.getByLabelText("Name"), "Lena Fischer");
    await user.click(screen.getByRole("button", { name: "Add person" }));
    expect(await screen.findByText(/cold-contact cap reached/)).toBeInTheDocument();
  });

  it("shows the truncation note only when the list was cut", async () => {
    listPeople.mockResolvedValue({ people: [P1], person: null, truncated: true });
    const { unmount } = render(<PeopleClient />);
    expect(await screen.findByTestId("people-truncated")).toBeInTheDocument();
    unmount();
    listPeople.mockResolvedValue({ people: [P1], person: null, truncated: false });
    render(<PeopleClient />);
    await rows();
    expect(screen.queryByTestId("people-truncated")).toBeNull();
  });

  it("empty list shows the empty state and still offers + Add person", async () => {
    listPeople.mockResolvedValue({ people: [], person: null, truncated: false });
    render(<PeopleClient />);
    expect(await screen.findByText("No people yet")).toBeInTheDocument();
    expect(screen.getByTestId("people-add-toggle")).toBeInTheDocument();
  });

  it("offers no send control", async () => {
    render(<PeopleClient />);
    await rows();
    expect(screen.queryByRole("button", { name: /send/i })).toBeNull();
  });

  it("page is a server component titled People", () => {
    expect(metadata.title).toBe("People");
    const src = readFileSync(join(process.cwd(), "src/app/people/page.tsx"), "utf-8");
    expect(src).not.toContain('"use client"');
    render(<PeoplePage />);
    expect(screen.getByRole("heading", { level: 1, name: "People" })).toBeInTheDocument();
  });

  it("a failed load shows an error line, not the empty state", async () => {
    listPeople.mockRejectedValue(new ApiError(500, "db unavailable"));
    render(<PeopleClient />);
    expect(await screen.findByTestId("people-error")).toBeInTheDocument();
    expect(screen.queryByText("No people yet")).toBeNull();
    expect(apiError).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("people-add-toggle")).toBeInTheDocument();
  });
});
