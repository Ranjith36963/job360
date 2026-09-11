import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { VisaSelect } from "./VisaSelect";

const setApplicationVisa = vi.fn();
vi.mock("@/lib/api", () => ({
  setApplicationVisa: (...args: unknown[]) => setApplicationVisa(...args),
}));

describe("VisaSelect", () => {
  beforeEach(() => {
    setApplicationVisa.mockReset();
    setApplicationVisa.mockResolvedValue({
      application_id: 7,
      visa: {
        signal: "no_sponsorship",
        detail: "",
        country: "DE",
        recorded_by: "web",
        recorded_at: "2026-09-11T00:00:00Z",
        needs_sponsorship: null,
      },
    });
  });

  it("choosing no_sponsorship, typing a country, and saving calls setApplicationVisa then onSaved", async () => {
    const onSaved = vi.fn().mockResolvedValue(undefined);
    render(
      <VisaSelect
        applicationId={7}
        visa={{
          signal: "unknown",
          detail: "",
          country: "",
          recorded_by: "",
          recorded_at: "",
          needs_sponsorship: null,
        }}
        onSaved={onSaved}
      />
    );

    fireEvent.change(screen.getByTestId("visa-select"), {
      target: { value: "no_sponsorship" },
    });
    fireEvent.change(screen.getByTestId("visa-country"), {
      target: { value: "de" },
    });
    fireEvent.click(screen.getByTestId("visa-save"));

    await waitFor(() =>
      expect(setApplicationVisa).toHaveBeenCalledWith(7, {
        visa_signal: "no_sponsorship",
        visa_country: "DE",
        visa_detail: "",
      })
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });
});
