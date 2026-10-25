import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReadyCard } from "./ReadyCard";
import { card } from "./ready-fixtures";

vi.mock("@/lib/api", () => ({ approveSend: vi.fn(), declineSend: vi.fn(), confirmAnswer: vi.fn() }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

describe("ReadyCard browser label", () => {
  it("shows nothing until the backend sends browser", () => {
    render(<ReadyCard card={card(5) as never} paused={false} onChanged={() => {}} />);
    expect(screen.queryByTestId("ready-browser-5")).toBeNull();
  });

  it("shows the label and, for own_browser, its reason", () => {
    const browser = { kind: "own_browser", label: "Your Chrome", reason: "Job360's browser failed" };
    render(<ReadyCard card={card(5, { browser }) as never} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-browser-label-5")).toHaveTextContent("Your Chrome");
    expect(screen.getByTestId("ready-browser-reason-5")).toHaveTextContent("Job360's browser failed");
  });

  it("hides the reason for job360", () => {
    const browser = { kind: "job360", label: "Job360's browser", reason: "first choice" };
    render(<ReadyCard card={card(5, { browser }) as never} paused={false} onChanged={() => {}} />);
    expect(screen.getByTestId("ready-browser-label-5")).toHaveTextContent("Job360's browser");
    expect(screen.queryByTestId("ready-browser-reason-5")).toBeNull();
  });
});
