import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { JobPriority } from "./JobPriority";
import type { ProfileResponse } from "@/lib/types";

const updateProfileFields = vi.fn();

vi.mock("@/lib/api", () => ({
  updateProfileFields: (...args: unknown[]) => updateProfileFields(...args),
}));

const BLOCK = {
  citizenship: ["IN"],
  countries: [
    { country: "GB", remote_ok: true, onsite: "yes", remote_priority: "high", note: "keep me" },
    { country: "ANY", remote_ok: true, onsite: "only_if_sponsored", needs_sponsorship: true },
    { country: "FR", remote_ok: false, onsite: "no" },
  ],
};

function saved(block: unknown): ProfileResponse {
  return { user_info: { right_to_work: block } } as unknown as ProfileResponse;
}

describe("JobPriority", () => {
  beforeEach(() => {
    updateProfileFields.mockReset();
  });

  it("renders a row per remote / on-site place, in record order, and none for places that are off", () => {
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    expect(screen.getByText("Job priority")).toBeInTheDocument();
    expect(screen.getByText("Remote — United Kingdom")).toBeInTheDocument();
    expect(screen.getByText("On-site — United Kingdom")).toBeInTheDocument();
    expect(screen.getByText("Remote — anywhere else")).toBeInTheDocument();
    expect(screen.getByText("On-site — anywhere else (visa sponsorship)")).toBeInTheDocument();
    expect(screen.queryByText(/France/)).toBeNull();
    expect(screen.getByTestId("job-priority-select-0-remote")).toHaveValue("high");
    expect(screen.getByTestId("job-priority-select-0-onsite")).toHaveValue("");
  });

  it("changing a select sends the WHOLE right_to_work with only that key changed", async () => {
    const after = { ...BLOCK, countries: BLOCK.countries.map((c, i) => (i === 1 ? { ...c, onsite_priority: "highest" } : c)) };
    updateProfileFields.mockResolvedValue(saved(after));
    const onSaved = vi.fn();
    render(<JobPriority rightToWork={BLOCK} onSaved={onSaved} />);

    fireEvent.change(screen.getByTestId("job-priority-select-1-onsite"), { target: { value: "highest" } });

    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    expect(updateProfileFields).toHaveBeenCalledWith([{ path: "user_info.right_to_work", value: after }]);
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved(after)));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it("Not set removes the key and keeps everything else on the record", async () => {
    updateProfileFields.mockResolvedValue(saved(BLOCK));
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);

    fireEvent.change(screen.getByTestId("job-priority-select-0-remote"), { target: { value: "" } });

    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    const sent = updateProfileFields.mock.calls[0][0][0].value as typeof BLOCK;
    expect(sent.countries[0]).toEqual({ country: "GB", remote_ok: true, onsite: "yes", note: "keep me" });
    expect("remote_priority" in sent.countries[0]).toBe(false);
    expect(sent.countries[1]).toEqual(BLOCK.countries[1]);
    expect(sent.citizenship).toEqual(["IN"]);
  });

  it("shows the error and leaves the page usable when a save fails", async () => {
    updateProfileFields.mockRejectedValue(new Error("boom"));
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    fireEvent.change(screen.getByTestId("job-priority-select-0-onsite"), { target: { value: "low" } });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByTestId("job-priority-select-0-onsite")).not.toBeDisabled();
  });

  it("shows the hint with a Memory link when there are no places", () => {
    render(<JobPriority rightToWork={{ countries: [{ country: "FR", remote_ok: false, onsite: "no" }] }} onSaved={() => {}} />);
    const hint = screen.getByTestId("job-priority-empty");
    expect(hint).toHaveTextContent("Add where you can work in the Memory tab first.");
    expect(screen.getByRole("link", { name: "Memory tab" })).toHaveAttribute("href", "/profile?tab=memory");
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("shows the hint when nothing is saved at all", () => {
    render(<JobPriority rightToWork={undefined} onSaved={() => {}} />);
    expect(screen.getByTestId("job-priority-empty")).toBeInTheDocument();
  });
});
