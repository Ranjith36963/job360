import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { PayAdvertised, describePay } from "./PayAdvertised";

const BASE = {
  amount: "£45,000 - £55,000",
  evidence: "Salary: £45,000 - £55,000",
  period: null,
  currency: "GBP",
  min: 45000,
  max: 55000,
  equity: false,
};

describe("PayAdvertised", () => {
  it("is hidden when the ad states no pay", () => {
    const { container } = render(<PayAdvertised pay={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("labels the amount 'as advertised' and adds nothing the ad did not say", () => {
    render(<PayAdvertised pay={BASE} />);
    expect(screen.getByTestId("pay-advertised")).toHaveTextContent("Pay, as advertised");
    expect(screen.getByTestId("pay-advertised-text")).toHaveTextContent("£45,000 - £55,000");
    expect(screen.getByTestId("pay-advertised-text").textContent).not.toMatch(/per (year|month|hour)/);
  });

  it("shows the period and a currency the amount text does not already carry", () => {
    expect(describePay({ ...BASE, amount: "90k", period: "annual", currency: "USD", min: 90000, max: 90000 })).toBe(
      "90k · per year · USD"
    );
    expect(describePay({ ...BASE, amount: "£25 per hour", period: "hourly" })).toBe("£25 per hour · GBP");
    expect(describePay({ ...BASE, amount: "$90k", currency: "UNKNOWN", equity: true })).toBe("$90k · plus equity");
  });
});
