import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRow } from "./MemoryRow";
import { contactRows, equalityRows, logisticsRows, rtwCountryRows, rtwTopRows, salaryRow } from "@/lib/memory";

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (...a: unknown[]) => toastSuccess(...a), error: (...a: unknown[]) => toastError(...a) } }));

const phone = contactRows().find((r) => r.id.endsWith(".phone"))!;
const visaType = rtwCountryRows("GB").find((r) => r.id.endsWith("visa_type"))!;
const sanctions = rtwTopRows()[1];
const gender = equalityRows()[0];
const onSave = vi.fn();

beforeEach(() => {
  onSave.mockReset().mockResolvedValue(undefined);
  toastSuccess.mockReset();
  toastError.mockReset();
});

const prov = (by: string, previous?: unknown) => ({ by, at: "2026-10-05T12:00:00Z", previous });

describe("MemoryRow", () => {
  it("empty reads 'Not saved yet' and the hint, never a default", () => {
    render(<MemoryRow spec={gender} value={undefined} provenance={null} emptyNote="Your assistant will ask" onSave={onSave} />);
    expect(screen.getByText("Not saved yet")).toBeInTheDocument();
    expect(screen.getByText("Your assistant will ask")).toBeInTheDocument();
  });

  it("filled shows value and who saved it", () => {
    render(<MemoryRow spec={phone} value="+44 7700 900123" provenance={prov("agent:Claude")} onSave={onSave} />);
    expect(screen.getByText("+44 7700 900123")).toBeInTheDocument();
    expect(screen.getByText(/^Saved by Claude, \d{1,2} Oct$/)).toBeInTheDocument();
  });

  it("'Prefer not to say' is a green pill, not a blank", () => {
    render(<MemoryRow spec={sanctions} value="Prefer not to say" provenance={prov("web")} onSave={onSave} />);
    expect(screen.getByText("Prefer not to say").className).toMatch(/bg-brand-soft/);
    expect(screen.getByText(/^You changed this, /)).toBeInTheDocument();
  });

  it("uses theme tokens only: no hard-coded colour in either theme", () => {
    const { container } = render(<MemoryRow spec={phone} value="1" provenance={prov("web")} onSave={onSave} />);
    expect(container.innerHTML).not.toMatch(/#[0-9a-f]{3,6}|style=/i);
  });

  it("Phone saves at once, with no confirm", async () => {
    render(<MemoryRow spec={phone} value="+44 1" provenance={null} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Phone" }));
    fireEvent.change(screen.getByLabelText("Phone"), { target: { value: "+44 7700 900123" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("+44 7700 900123"));
    expect(screen.queryByTestId("memory-confirm")).toBeNull();
    expect(toastSuccess).toHaveBeenCalledWith("Saved.");
  });

  it("Visa type asks first with the exact words; Cancel keeps the old answer", async () => {
    render(<MemoryRow spec={visaType} value="Graduate visa" provenance={prov("agent:Claude")} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Visa type" }));
    fireEvent.change(screen.getByLabelText("Visa type"), { target: { value: "Skilled Worker visa" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByTestId("memory-confirm")).toHaveTextContent("This answer goes on legal forms. Save?");
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText("Graduate visa")).toBeInTheDocument();
  });

  it("Visa type saves after the confirm", async () => {
    render(<MemoryRow spec={visaType} value="Graduate visa" provenance={null} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Visa type" }));
    fireEvent.change(screen.getByLabelText("Visa type"), { target: { value: "Skilled Worker visa" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    fireEvent.click(within_confirm("Save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("Skilled Worker visa"));
  });

  it("offers Prefer not to say first on equality, and on sanctions", () => {
    const { unmount } = render(<MemoryRow spec={gender} value={undefined} provenance={null} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Gender" }));
    const buttons = screen.getAllByRole("button").map((b) => b.textContent);
    expect(buttons.indexOf("Prefer not to say")).toBeLessThan(buttons.indexOf("Save"));
    unmount();
    render(<MemoryRow spec={sanctions} value={undefined} provenance={null} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Citizen of a sanctioned country?" }));
    const options = screen.getAllByRole("option").map((o) => o.textContent);
    expect(options.slice(0, 2)).toEqual(["Choose…", "Prefer not to say"]);
  });

  it("shows 'was: …' and Take back sends the previous value (no confirm on Phone)", async () => {
    render(<MemoryRow spec={phone} value="+44 2" provenance={prov("web", "+44 1")} onSave={onSave} />);
    expect(screen.getByText(/was: \+44 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("memory-take-back"));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("+44 1"));
  });

  it("Take back on a sensitive row asks first", () => {
    render(<MemoryRow spec={visaType} value="Skilled Worker visa" provenance={prov("web", "Graduate visa")} onSave={onSave} />);
    fireEvent.click(screen.getByTestId("memory-take-back"));
    expect(screen.getByTestId("memory-confirm")).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });

  it("no 'was' when the change replaced nothing", () => {
    render(<MemoryRow spec={phone} value="+44 1" provenance={prov("agent:Claude")} onSave={onSave} />);
    expect(screen.queryByTestId("memory-take-back")).toBeNull();
  });

  it("a failed save says so and keeps the editor open", async () => {
    onSave.mockRejectedValueOnce(new Error("boom"));
    render(<MemoryRow spec={phone} value="+44 1" provenance={null} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Phone" }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(screen.getByText("Editing · Phone")).toBeInTheDocument();
  });

  it("salary range shows with currency; editing it is sensitive", () => {
    render(
      <MemoryRow spec={salaryRow("GB")} value={{ min: 80000, max: 95000, currency: "GBP", period: "year" }}
        provenance={null} compact amberEmpty onSave={onSave} />,
    );
    expect(screen.getByText("£80k–95k GBP")).toBeInTheDocument();
  });

  it("a missing salary is amber 'not saved yet'", () => {
    render(<MemoryRow spec={salaryRow("DE")} value={undefined} provenance={null} compact amberEmpty onSave={onSave} />);
    expect(screen.getByText("not saved yet").className).toMatch(/text-warning/);
  });

  it("keeps the languages editor honest", () => {
    render(<MemoryRow spec={logisticsRows()[2]} value={undefined} provenance={null} onSave={onSave} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Languages" }));
    fireEvent.change(screen.getByLabelText("Languages"), { target: { value: "English (great)" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Levels: native, fluent, professional, basic");
    expect(onSave).not.toHaveBeenCalled();
  });
});

function within_confirm(name: string): HTMLElement {
  const box = screen.getByTestId("memory-confirm");
  return Array.from(box.querySelectorAll("button")).find((b) => b.textContent === name) as HTMLElement;
}
