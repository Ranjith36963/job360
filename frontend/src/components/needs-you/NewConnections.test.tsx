import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { JobCards } from "./JobCards";

// The real API module runs; only fetch is stubbed, so each test sees the exact URL every action calls.

type Call = { url: string; method: string; body: unknown };
let calls: Call[] = [];

const entry = (id: number, contact_id: number, entry: string, text: string, at: string) => ({
  id, contact_id, entry, channel: "linkedin", text, version_no: entry === "message" ? 1 : null,
  occurred_at: at, recorded_at: at, recorded_by: "agent:X", source_message_id: "", label: "",
});

function full(id: number, application_id: number | null, over: Record<string, unknown> = {}) {
  const msg = entry(id * 10, id, "message", `Hi from ${id}`, "2026-10-26T09:00:00+00:00");
  return {
    id, application_id, name: `Person ${id}`, role: "Recruiter", email: "", linkedin_url: `https://www.linkedin.com/in/p${id}`,
    notes: "", added_by: "agent:X", created_at: "2026-10-26T08:00:00+00:00", company: "Acme", linkedin_state: "not_connected",
    edit_history: {}, links: [], jobs: [],
    outreach: { messages: [msg], sent: [], replies: [], bounced: [], interactions: [], message_count: 1, last_sent: null, replied: false, last_reply: null },
    ...over,
  };
}

const listed = (id: number, jobs: { application_id: number; job_company: string; job_title: string }[] = []) => ({
  contact_ids: [id], name: `Person ${id}`, role: "Recruiter", email: "", linkedin_url: "", notes: "", jobs,
  message_count: 1, last_sent: null, replied: false, last_reply: null,
});

let people: unknown[] = [];
let records: Record<number, unknown> = {};

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  calls = [];
  people = [];
  records = {};
  vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(String(input), "http://x");
    const method = init?.method ?? "GET";
    calls.push({ url: url.pathname + url.search, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    if (url.pathname.endsWith("/api/people")) {
      const id = url.searchParams.get("contact_id");
      return json(id ? { person: records[Number(id)] ?? null } : { people });
    }
    if (url.pathname.endsWith("/ready-to-send")) return json({ paused: false, total: 0, unflagged: 0, items: [] });
    if (url.pathname.includes("cv-to-approve")) return json({ items: [] });
    const app = url.pathname.match(/\/api\/applications\/(\d+)$/);
    if (app) {
      const id = Number(app[1]);
      const contacts = Object.values(records).filter((r) => (r as { application_id: number | null }).application_id === id);
      return json({
        id, asks: [], contacts, artifacts: [], form_state: "", updated_at: "2026-10-26T10:00:00+00:00",
        job: { job_company: `Co${id}`, job_title: `Role${id}`, job_url: "https://x.example/" },
      });
    }
    return json({}, method === "POST" ? 201 : 200);
  }));
});
afterEach(() => vi.unstubAllGlobals());

const posts = () => calls.filter((c) => c.method === "POST");

describe("New connections", () => {
  it("lists a person with no job, and never a person on a job", async () => {
    people = [listed(1), listed(2, [{ application_id: 9, job_company: "Co9", job_title: "Role9" }])];
    records = { 1: full(1, null), 2: full(2, 9) };
    const onReport = vi.fn();
    render(<JobCards open={[]} onChanged={async () => {}} onReport={onReport} />);
    const group = await screen.findByTestId("new-connections");
    expect(group).toHaveTextContent("New connections");
    expect(within(group).getByTestId("person-1")).toHaveTextContent("Hi from 1");
    expect(within(group).queryByTestId("person-2")).not.toBeInTheDocument();
    // Person 2 stays inside their job's card.
    expect(within(await screen.findByTestId("people-to-reach")).getByTestId("person-2")).toBeInTheDocument();
    // Person 2 is read only through the job list, never as a new connection.
    expect(calls.some((c) => c.url.includes("contact_id=2"))).toBe(false);
    await waitFor(() => expect(onReport).toHaveBeenLastCalledWith(expect.objectContaining({ total: 1, connections: 1, settled: true })));
  });

  it("leaves out a cold contact whose note was sent since", async () => {
    people = [listed(3)];
    const sent = entry(31, 3, "sent", "", "2026-10-26T10:00:00+00:00");
    records = { 3: full(3, null, { outreach: { ...full(3, null).outreach, last_sent: sent } }) };
    const onReport = vi.fn();
    render(<JobCards open={[]} onChanged={async () => {}} onReport={onReport} />);
    await waitFor(() => expect(onReport).toHaveBeenLastCalledWith(expect.objectContaining({ settled: true })));
    expect(screen.queryByTestId("new-connections")).not.toBeInTheDocument();
  });

  it("is hidden when there are none", async () => {
    const onReport = vi.fn();
    render(<JobCards open={[]} onChanged={async () => {}} onReport={onReport} />);
    await waitFor(() => expect(onReport).toHaveBeenLastCalledWith(expect.objectContaining({ settled: true, connections: 0 })));
    expect(screen.queryByTestId("new-connections")).not.toBeInTheDocument();
  });

  it("I sent it records a sent mark on the contact, then hides the person", async () => {
    people = [listed(1)];
    records = { 1: full(1, null) };
    const onChanged = vi.fn(async () => {});
    render(<JobCards open={[]} onChanged={onChanged} />);
    fireEvent.click(await screen.findByTestId("sent-1"));
    await waitFor(() => expect(screen.queryByTestId("person-1")).not.toBeInTheDocument());
    expect(posts()).toEqual([{ url: "/api/contacts/1/outreach", method: "POST", body: { entry: "sent", channel: "linkedin" } }]);
    expect(onChanged).toHaveBeenCalled();
  });

  it("Skip hides the person and writes nothing", async () => {
    people = [listed(1)];
    records = { 1: full(1, null) };
    render(<JobCards open={[]} onChanged={async () => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Skip" }));
    expect(screen.queryByTestId("new-connections")).not.toBeInTheDocument();
    expect(posts()).toEqual([]);
  });

  it("Copy copies the note and Open profile links to the person's LinkedIn", async () => {
    const writeText = vi.fn(async () => {});
    vi.stubGlobal("navigator", { ...navigator, clipboard: { writeText } });
    people = [listed(1)];
    records = { 1: full(1, null) };
    render(<JobCards open={[]} onChanged={async () => {}} />);
    fireEvent.click(await screen.findByRole("button", { name: "Copy" }));
    expect(writeText).toHaveBeenCalledWith("Hi from 1");
    expect(screen.getByRole("link", { name: "Open profile ↗" })).toHaveAttribute("href", "https://www.linkedin.com/in/p1");
  });

  it("Send asks the user's own browser to send that one note", async () => {
    people = [listed(1)];
    records = { 1: full(1, null, { own_browser_task: { state: "waiting_on_you", message_id: 10, approved_at: null, claim_until: null } }) };
    render(<JobCards open={[]} onChanged={async () => {}} />);
    fireEvent.click(await screen.findByTestId("own-browser-send-1"));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(posts()[0]).toEqual({ url: "/api/contacts/1/own-browser-send", method: "POST", body: { message_id: 10, decision: "approve" } });
  });
});
