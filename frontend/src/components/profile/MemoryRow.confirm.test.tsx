import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRow } from "./MemoryRow";
import { contactRows } from "@/lib/memory";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const phone = contactRows().find((r) => r.id.endsWith(".phone"))!;
const prov = (unconfirmed?: boolean) => ({ by: "agent:Claude", at: "2026-10-05T12:00:00Z", previous: undefined, unconfirmed });

describe("MemoryRow fact-confirm", () => {
  it("an inferred fact shows unconfirmed and Confirm calls onConfirm", async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined);
    render(<MemoryRow spec={phone} value="+44 1" provenance={prov(true)} onSave={vi.fn()} onConfirm={onConfirm} />);
    expect(screen.getByText("unconfirmed")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("fact-confirm"));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledTimes(1));
  });

  it("a confirmed fact shows no confirm control", () => {
    render(<MemoryRow spec={phone} value="+44 1" provenance={prov(false)} onSave={vi.fn()} onConfirm={vi.fn()} />);
    expect(screen.queryByTestId("fact-confirm")).toBeNull();
  });
});
