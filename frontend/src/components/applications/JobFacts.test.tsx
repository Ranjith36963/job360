import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { JobFacts } from "./JobFacts";

const updateApplicationJob = vi.fn();
vi.mock("@/lib/api", () => ({
  updateApplicationJob: (...args: unknown[]) => updateApplicationJob(...args),
}));

const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

const FACTS = { country: "FR", remote: true, found_on: "indeed" };

describe("JobFacts", () => {
  beforeEach(() => {
    updateApplicationJob.mockReset();
    toast.success.mockReset();
    toast.error.mockReset();
  });

  it("shows country name, Remote and Found on in plain words", () => {
    render(<JobFacts applicationId={7} facts={FACTS} />);
    expect(screen.getByTestId("job-facts-text")).toHaveTextContent("France · Remote · Found on Indeed");
  });

  it("shows only an invitation when nothing is set", () => {
    render(<JobFacts applicationId={7} facts={{ country: null, remote: null, found_on: null }} />);
    expect(screen.queryByTestId("job-facts-text")).toBeNull();
    expect(screen.getByTestId("job-facts-edit")).toHaveTextContent("Add job details");
  });

  it("Edit PATCHes only the changed keys and toasts on save", async () => {
    updateApplicationJob.mockResolvedValue({ application_id: 7, country: "DE", remote: true, found_on: "indeed" });
    render(<JobFacts applicationId={7} facts={FACTS} />);
    fireEvent.click(screen.getByTestId("job-facts-edit"));
    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "DE" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateApplicationJob).toHaveBeenCalledWith(7, { country: "DE" }));
    await waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(screen.getByTestId("job-facts-text")).toHaveTextContent("Germany · Remote · Found on Indeed");
  });

  it("Not set clears a fact with null", async () => {
    updateApplicationJob.mockResolvedValue({ application_id: 7, country: "FR", remote: null, found_on: null });
    render(<JobFacts applicationId={7} facts={FACTS} />);
    fireEvent.click(screen.getByTestId("job-facts-edit"));
    fireEvent.change(screen.getByLabelText("Remote"), { target: { value: "" } });
    fireEvent.change(screen.getByLabelText("Found on"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(updateApplicationJob).toHaveBeenCalledWith(7, { remote: null, found_on: null })
    );
  });

  it("remote No sends false", async () => {
    updateApplicationJob.mockResolvedValue({ application_id: 7, country: "FR", remote: false, found_on: "indeed" });
    render(<JobFacts applicationId={7} facts={FACTS} />);
    fireEvent.click(screen.getByTestId("job-facts-edit"));
    fireEvent.change(screen.getByLabelText("Remote"), { target: { value: "no" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateApplicationJob).toHaveBeenCalledWith(7, { remote: false }));
  });

  it("reverts to the old values and toasts an error when the save fails", async () => {
    updateApplicationJob.mockRejectedValue(new Error("nope"));
    render(<JobFacts applicationId={7} facts={FACTS} />);
    fireEvent.click(screen.getByTestId("job-facts-edit"));
    fireEvent.change(screen.getByLabelText("Country"), { target: { value: "DE" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalled());
    expect(screen.getByTestId("job-facts-text")).toHaveTextContent("France · Remote · Found on Indeed");
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("saving with no change makes no request", () => {
    render(<JobFacts applicationId={7} facts={FACTS} />);
    fireEvent.click(screen.getByTestId("job-facts-edit"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(updateApplicationJob).not.toHaveBeenCalled();
  });
});
