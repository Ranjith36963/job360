import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
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

  it("a priority box sits right next to each ticked work type, showing the saved level; none for an unticked one", () => {
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    expect(screen.getByTestId("work-grid-priority-remote-GB")).toHaveValue("high"); // an older level is kept as it is
    expect(screen.getByTestId("work-grid-priority-onsite-GB")).toHaveValue("");
    expect(screen.queryByTestId("work-grid-priority-contract-GB")).toBeNull(); // Contract not ticked
    expect(screen.queryByTestId("work-grid-priority-remote-FR")).toBeNull(); // France: nothing ticked
    expect(screen.queryByTestId("priorities")).toBeNull(); // no separate section any more
  });

  it("choosing a level sends the WHOLE right_to_work with only that key changed", async () => {
    updateProfileFields.mockImplementation(async (edits: { value: unknown }[]) => saved(edits[0].value));
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    fireEvent.change(screen.getByTestId("work-grid-priority-onsite-GB"), { target: { value: "medium" } });
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    const block = updateProfileFields.mock.calls[0][0][0].value;
    expect(block.countries[0]).toEqual({ ...BLOCK.countries[0], onsite_priority: "medium" });
    expect(block.countries.slice(1)).toEqual(BLOCK.countries.slice(1));
    expect(block.citizenship).toEqual(["IN"]);
  });

  it("Priority… (not chosen) removes the key and keeps everything else on the record", async () => {
    updateProfileFields.mockImplementation(async (edits: { value: unknown }[]) => saved(edits[0].value));
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    fireEvent.change(screen.getByTestId("work-grid-priority-remote-GB"), { target: { value: "" } });
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    const gb = updateProfileFields.mock.calls[0][0][0].value.countries[0];
    expect(gb).not.toHaveProperty("remote_priority");
    expect(gb.note).toBe("keep me");
  });

  it("shows the error and leaves the page usable when a priority save fails", async () => {
    updateProfileFields.mockRejectedValueOnce(new Error("nope"));
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    fireEvent.change(screen.getByTestId("work-grid-priority-onsite-GB"), { target: { value: "low" } });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.getByTestId("work-grid-priority-onsite-GB")).not.toBeDisabled();
  });

  it("offers only Highest, Medium and Low (an older High stays listed until it is changed)", () => {
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    const fresh = within(screen.getByTestId("work-grid-priority-onsite-GB")).getAllByRole("option").map((o) => o.textContent);
    expect(fresh).toEqual(["Priority…", "Highest", "Medium", "Low"]);
    const older = within(screen.getByTestId("work-grid-priority-remote-GB")).getAllByRole("option").map((o) => o.textContent);
    expect(older).toEqual(["Priority…", "Highest", "Medium", "Low", "High"]);
  });

  it("with nothing saved, it offers places to add and shows no priority box", () => {
    render(<JobPriority rightToWork={undefined} onSaved={() => {}} />);
    expect(screen.getByTestId("work-grid-add")).toBeInTheDocument();
    expect(screen.getByTestId("work-grid-add-region-EUROPE")).toBeInTheDocument();
    expect(screen.queryByTestId(/^work-grid-priority-/)).toBeNull();
  });

  it("the grid shows every place, all other countries last, with its ticks and visa answer", () => {
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    const rows = screen.getAllByTestId(/^work-grid-row-/).map((r) => r.getAttribute("data-testid"));
    expect(rows).toEqual(["work-grid-row-GB", "work-grid-row-FR", "work-grid-row-ANY"]);
    expect(screen.getByTestId("work-grid-row-ANY")).toHaveTextContent("All other countries");
    expect(screen.getByTestId("work-grid-remote-GB")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("work-grid-onsite-ANY")).toHaveAttribute("aria-pressed", "true"); // only if sponsored = on
    expect(screen.getByTestId("work-grid-onsite-FR")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByTestId("work-grid-contract-GB")).toHaveAttribute("aria-pressed", "false"); // unanswered reads off
    expect(screen.getByTestId("work-grid-visa-need-ANY")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("work-grid-visa-noneed-GB")).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByTestId("work-grid-add-any")).toBeNull(); // ANY already there
  });

  it("a tick writes one key of that place's record and keeps everything else", async () => {
    updateProfileFields.mockImplementation(async (edits: { value: unknown }[]) => saved(edits[0].value));
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    fireEvent.click(screen.getByTestId("work-grid-contract-GB"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    const block = updateProfileFields.mock.calls[0][0][0].value;
    expect(block.citizenship).toEqual(["IN"]);
    expect(block.countries[0]).toEqual({ ...BLOCK.countries[0], contract_ok: true });
    expect(block.countries.slice(1)).toEqual(BLOCK.countries.slice(1));
  });

  it("on-site on is yes, off is no; the visa choice clicked again clears it", async () => {
    updateProfileFields.mockImplementation(async (edits: { value: unknown }[]) => saved(edits[0].value));
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    fireEvent.click(screen.getByTestId("work-grid-onsite-FR"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    expect(updateProfileFields.mock.calls[0][0][0].value.countries[2].onsite).toBe("yes");
    fireEvent.click(screen.getByTestId("work-grid-visa-need-ANY"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(2));
    expect(updateProfileFields.mock.calls[1][0][0].value.countries[1]).not.toHaveProperty("needs_sponsorship");
  });

  it("two quick ticks on different places both land (each starts from the last save)", async () => {
    let release: (p: unknown) => void = () => undefined;
    updateProfileFields
      .mockImplementationOnce((edits: { value: unknown }[]) => new Promise((r) => { release = () => r(saved(edits[0].value)); }))
      .mockImplementationOnce(async (edits: { value: unknown }[]) => saved(edits[0].value));
    render(<JobPriority rightToWork={BLOCK} onSaved={() => {}} />);
    fireEvent.click(screen.getByTestId("work-grid-contract-GB"));
    fireEvent.click(screen.getByTestId("work-grid-remote-FR"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(1));
    release(undefined);
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledTimes(2));
    const last = updateProfileFields.mock.calls[1][0][0].value.countries;
    expect(last[0].contract_ok).toBe(true);
    expect(last[2].remote_ok).toBe(true);
  });

  it("Add all other countries adds an empty ANY place; a refused save shows its reason", async () => {
    updateProfileFields.mockRejectedValueOnce(new Error("nope"));
    render(<JobPriority rightToWork={{ countries: [{ country: "GB", remote_ok: true }] }} onSaved={() => {}} />);
    fireEvent.click(screen.getByTestId("work-grid-add-any"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "user_info.right_to_work", value: { countries: [{ country: "GB", remote_ok: true }, { country: "ANY" }] } },
    ]));
    fireEvent.click(screen.getByTestId("work-grid-contract-GB"));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("Need on a place where you are a citizen says why in plain words and sends nothing", () => {
    render(<JobPriority rightToWork={{ countries: [{ country: "IN", work_authorization: "citizen", remote_ok: true }] }} onSaved={() => {}} />);
    fireEvent.click(screen.getByTestId("work-grid-visa-need-IN"));
    expect(screen.getByRole("alert")).toHaveTextContent("You saved that you're a citizen of India, so no visa is needed there.");
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("salary column: shows the saved range, and saving one country keeps the others", async () => {
    const salaries = [
      { country: "GB", min: 80000, max: 95000, currency: "GBP", period: "year" },
      { country: "DE", min: 70000, max: 70000, currency: "EUR", period: "year" },
    ];
    updateProfileFields.mockImplementation(async () => ({ preferences: {} }) as unknown as ProfileResponse);
    render(<JobPriority rightToWork={BLOCK} salaries={salaries} onSaved={() => {}} />);
    expect(screen.getByTestId("work-grid-salary-GB")).toHaveTextContent("£80k–95k GBP");
    expect(screen.getByTestId("work-grid-salary-FR")).toHaveTextContent("Add salary");
    expect(screen.queryByTestId("work-grid-salary-ANY")).toBeNull(); // set per country
    fireEvent.click(screen.getByTestId("work-grid-salary-FR"));
    const form = screen.getByTestId("work-grid-salary-form-FR");
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "60000" } });
    fireEvent.change(within(form).getByLabelText("Up to"), { target: { value: "75000" } });
    fireEvent.change(within(form).getByLabelText("Currency"), { target: { value: "EUR" } });
    fireEvent.change(within(form).getByLabelText("Per"), { target: { value: "year" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([{
      path: "preferences.salary_by_country",
      value: [...salaries, { country: "FR", min: 60000, max: 75000, currency: "EUR", period: "year" }],
    }]));
  });

  it("salary form refuses an incomplete range in plain words and sends nothing", () => {
    render(<JobPriority rightToWork={BLOCK} salaries={[]} onSaved={() => {}} />);
    fireEvent.click(screen.getByTestId("work-grid-salary-GB"));
    const form = screen.getByTestId("work-grid-salary-form-GB");
    fireEvent.change(within(form).getByLabelText("From"), { target: { value: "50000" } });
    expect(within(form).getByRole("alert")).toHaveTextContent("Salary needs a figure above 0, a 3-letter currency, and a period.");
    expect(within(form).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(updateProfileFields).not.toHaveBeenCalled();
  });

  it("is a table with four columns: country, work type, visa, salary range (one line per country)", () => {
    render(<JobPriority rightToWork={BLOCK} salaries={[]} onSaved={() => {}} />);
    expect(screen.getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Country", "Work type", "Visa", "Salary range"]);
    const gb = screen.getByTestId("work-grid-row-GB");
    const cells = within(gb).getAllByRole("cell");
    expect(within(gb).getByRole("rowheader")).toHaveTextContent("United Kingdom");
    expect(within(cells[0]).getAllByRole("button").map((b) => b.textContent)).toEqual(["☑Remote", "☑On-site", "☐Contract"]);
    expect(within(cells[1]).getAllByRole("button").map((b) => b.textContent)).toEqual(["Need", "No need"]);
    expect(cells[2]).toHaveTextContent("Add salary");
    expect(within(screen.getByTestId("work-grid-row-ANY")).getAllByRole("cell")[2]).toHaveTextContent("Set per country");
  });

  it("a whole region can be added as one line; it sits after the countries and before all other countries", async () => {
    updateProfileFields.mockImplementation(async (edits: { value: unknown }[]) => saved(edits[0].value));
    const block = { countries: [{ country: "ANY", remote_ok: true }, { country: "EUROPE", onsite: "only_if_sponsored" }, { country: "IN", remote_ok: true }] };
    render(<JobPriority rightToWork={block} onSaved={() => {}} />);
    expect(screen.getAllByTestId(/^work-grid-row-/).map((r) => r.getAttribute("data-testid"))).toEqual([
      "work-grid-row-IN", "work-grid-row-EUROPE", "work-grid-row-ANY",
    ]);
    expect(within(screen.getByTestId("work-grid-row-EUROPE")).getByRole("rowheader")).toHaveTextContent("Europe");
    // Europe is already a line, so it is not offered again; the Middle East is
    expect(screen.queryByTestId("work-grid-add-region-EUROPE")).toBeNull();
    fireEvent.click(screen.getByTestId("work-grid-add-region-MIDDLE_EAST"));
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "user_info.right_to_work", value: { countries: [...block.countries, { country: "MIDDLE_EAST" }] } },
    ]));
  });

  it("contract gets its own priority next to its tick, saved as contract_priority (Europe: remote Highest, contract Low)", async () => {
    updateProfileFields.mockImplementation(async (edits: { value: unknown }[]) => saved(edits[0].value));
    const block = { countries: [{ country: "EUROPE", remote_ok: true, remote_priority: "highest", contract_ok: true }] };
    render(<JobPriority rightToWork={block} onSaved={() => {}} />);
    expect(screen.getByTestId("work-grid-priority-remote-EUROPE")).toHaveValue("highest");
    fireEvent.change(screen.getByTestId("work-grid-priority-contract-EUROPE"), { target: { value: "low" } });
    await waitFor(() => expect(updateProfileFields).toHaveBeenCalledWith([
      { path: "user_info.right_to_work", value: { countries: [{ ...block.countries[0], contract_priority: "low" }] } },
    ]));
  });
});

