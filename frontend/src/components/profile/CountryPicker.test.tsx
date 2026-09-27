/**
 * CountryPicker — a searchable multi-select over the full ISO-3166-1 region
 * list (lib/countries.ts), storing alpha-2 codes while showing real country
 * names. Replaces the old free-typed ISO-code text box (docs/product/
 * VISION.md decision 27: visa is two facts + list membership, no country
 * rule of ours — this is UX sugar only, the wire value is unchanged).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CountryPicker } from "./CountryPicker";

describe("CountryPicker", () => {
  it("shows no suggestions until the user types", () => {
    render(<CountryPicker label="Countries" tags={[]} onChange={vi.fn()} />);
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("searches by country name and adds the ISO code on click", () => {
    const onChange = vi.fn();
    render(<CountryPicker label="Countries" tags={[]} onChange={onChange} />);
    fireEvent.change(
      screen.getByLabelText(/search countries to add to countries/i),
      { target: { value: "United King" } }
    );
    const option = screen.getByRole("option", { name: "United Kingdom" });
    fireEvent.click(option);
    expect(onChange).toHaveBeenCalledWith(["GB"]);
  });

  it("renders a stored ISO code as its country name, removable", () => {
    const onChange = vi.fn();
    render(<CountryPicker label="Countries" tags={["IN"]} onChange={onChange} />);
    expect(screen.getByText("India")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("Remove India"));
    expect(onChange).toHaveBeenCalledWith([]);
  });

  it("never offers a country already selected", () => {
    render(<CountryPicker label="Countries" tags={["GB"]} onChange={vi.fn()} />);
    fireEvent.change(
      screen.getByLabelText(/search countries to add to countries/i),
      { target: { value: "United King" } }
    );
    expect(screen.queryByRole("option", { name: "United Kingdom" })).not.toBeInTheDocument();
  });

  it("Enter adds the top match", () => {
    const onChange = vi.fn();
    render(<CountryPicker label="Countries" tags={[]} onChange={onChange} />);
    const input = screen.getByLabelText(/search countries to add to countries/i);
    fireEvent.change(input, { target: { value: "Germany" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onChange).toHaveBeenCalledWith(["DE"]);
  });
});
