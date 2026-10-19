import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import BringJobPage from "../page";

const bringJob = vi.fn();
const fetchJobUrl = vi.fn();
const push = vi.fn();
const success = vi.fn();
const info = vi.fn();

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("@/lib/api", () => ({
  bringJob: (...a: unknown[]) => bringJob(...a),
  fetchJobUrl: (...a: unknown[]) => fetchJobUrl(...a),
}));
vi.mock("@/lib/toast", () => ({
  toast: { success: (m: string) => success(m), info: (m: string) => info(m), apiError: vi.fn(), error: vi.fn() },
}));

function fill() {
  fireEvent.change(screen.getByLabelText("Job title"), { target: { value: "Data Engineer" } });
  fireEvent.change(screen.getByLabelText("Company"), { target: { value: "Northwind" } });
  fireEvent.change(screen.getByLabelText("The ad, as written"), { target: { value: "Build pipelines." } });
}

beforeEach(() => {
  bringJob.mockReset().mockResolvedValue({ application_id: 7, existing: false, queued: true, queue_note: null });
  fetchJobUrl.mockReset();
  push.mockReset();
  success.mockReset();
  info.mockReset();
});

describe("Bring a job: Add to batch", () => {
  it("is ticked by default, sends queue and a pasted-ad origin, and says it was added", async () => {
    render(<BringJobPage />);
    const tick = screen.getByTestId("bring-to-batch").querySelector("input") as HTMLInputElement;
    expect(tick.checked).toBe(true);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /Bring this job/ }));
    await waitFor(() => expect(bringJob).toHaveBeenCalled());
    expect(bringJob.mock.calls[0][0]).toMatchObject({ queue: true, origin: "user_paste" });
    await waitFor(() => expect(success).toHaveBeenCalledWith("Added to your batch."));
    expect(push).toHaveBeenCalledWith("/applications/7");
  });

  it("a fetched link is tagged as your link; untick keeps it out of the batch", async () => {
    fetchJobUrl.mockResolvedValue({ outcome: "ok", message: "", title: "Data Engineer", company: "Northwind", description: "Build.", final_url: "https://x.example/j/1", found: [] });
    bringJob.mockResolvedValue({ application_id: 8, existing: false, queued: null, queue_note: null });
    render(<BringJobPage />);
    fireEvent.change(screen.getByLabelText(/Fetch a job from a link/), { target: { value: "https://x.example/j/1" } });
    fireEvent.click(screen.getByRole("button", { name: /Fetch/ }));
    await waitFor(() => expect(fetchJobUrl).toHaveBeenCalled());
    await waitFor(() => expect((screen.getByLabelText("Job title") as HTMLInputElement).value).toBe("Data Engineer"));
    fireEvent.click(screen.getByTestId("bring-to-batch").querySelector("input") as HTMLInputElement);
    fireEvent.click(screen.getByRole("button", { name: /Bring this job/ }));
    await waitFor(() => expect(bringJob).toHaveBeenCalled());
    expect(bringJob.mock.calls[0][0]).toMatchObject({ queue: false, origin: "user_link" });
    expect(success).not.toHaveBeenCalledWith("Added to your batch.");
  });

  it("says so when the job is already applied or closed", async () => {
    bringJob.mockResolvedValue({ application_id: 9, existing: true, queued: false, queue_note: "not open" });
    render(<BringJobPage />);
    fill();
    fireEvent.click(screen.getByRole("button", { name: /Bring this job/ }));
    await waitFor(() => expect(info).toHaveBeenCalledWith("Not added to your batch: it is already applied or closed."));
  });
});
